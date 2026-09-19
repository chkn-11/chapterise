#!/usr/bin/env python3
"""Local M4A chapter review. Python standard library + FFmpeg only."""

import argparse
from collections import deque
import json
import math
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


STATIC = Path(__file__).parent / "static"


def run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr[-3000:] or "FFmpeg command failed")
    return result.stdout


def probe(path):
    info = json.loads(run(["ffprobe", "-v", "error", "-show_format",
                           "-show_streams", "-show_chapters", "-of", "json", str(path)]))
    if not any(s["codec_type"] == "audio" for s in info["streams"]):
        raise ValueError("The file has no audio stream.")
    return info


def number(value, name, low, high):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number.") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}.")
    return value


def detect_pauses(path, duration, noise=-35, minimum=2, spacing=60, progress=None):
    """Stream detection logs so even long books don't require decoded audio in RAM."""
    noise = number(noise, "Silence threshold", -90, -5)
    minimum = number(minimum, "Minimum pause", 0.1, 120)
    spacing = number(spacing, "Minimum chapter length", 0, 86400)
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-i", str(path),
               "-map", "0:a:0", "-af", f"asetpts=PTS-STARTPTS,silencedetect=noise={noise}dB:d={minimum}",
               "-progress", "pipe:2", "-f", "null", "-"]
    pauses, tail, start = [], deque(maxlen=30), None
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          text=True) as process:
        for line in process.stderr:
            tail.append(line)
            match = re.search(r"silence_start: ([-\d.eE+]+)", line)
            if match:
                start = max(0, float(match[1]))
            match = re.search(r"silence_end: ([-\d.eE+]+)", line)
            if match and start is not None:
                end = min(duration, float(match[1]))
                # Leading/trailing silence isn't a new chapter.
                if start > 0.1 and end < duration - 0.1:
                    pauses.append((start, end))
                start = None
            if progress and line.startswith("out_time_us="):
                try:
                    progress(min(99, float(line.split("=", 1)[1]) / 10000 / duration))
                except ValueError:
                    pass
        if process.wait():
            raise ValueError("".join(tail)[-3000:])
    # Long pauses get priority; nearby shorter pauses remain available for review.
    selected = [0, duration]
    candidates = []
    for start, end in sorted(pauses, key=lambda p: p[1] - p[0], reverse=True):
        boundary = round((start + end) / 2, 3)
        suggested = all(abs(boundary - other) >= spacing for other in selected)
        if suggested:
            selected.append(boundary)
        candidates.append({"start": boundary, "pause": round(end - start, 3),
                           "selected": suggested, "kind": "pause", "title": ""})
    return sorted(candidates, key=lambda c: c["start"])


def validate_chapters(chapters, duration):
    if not isinstance(chapters, list) or not 1 <= len(chapters) <= 10000:
        raise ValueError("Choose between 1 and 10,000 chapters.")
    result = []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise ValueError("Invalid chapter.")
        start = round(number(chapter.get("start"), "Chapter start", 0, duration) * 1000)
        title = chapter.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise ValueError("Every chapter needs a title of 1–500 characters.")
        if any(ord(c) < 32 for c in title):
            raise ValueError("Chapter titles cannot contain control characters.")
        result.append({"start": start, "title": title.strip()})
    result.sort(key=lambda c: c["start"])
    if result[0]["start"] != 0:
        raise ValueError("The first chapter must start at 0.")
    if len({c["start"] for c in result}) != len(result):
        raise ValueError("Two chapters cannot start at the same millisecond.")
    if result[-1]["start"] >= round(duration * 1000):
        raise ValueError("The last chapter must start before the audio ends.")
    return result


def escape_metadata(value):
    return re.sub(r"([\\=;#])", r"\\\1", value)


def write_chapters(source, output, chapters, duration):
    """Copy encoded audio and artwork, replace chapters, never overwrite files."""
    source, output = Path(source).resolve(), Path(output).absolute()
    if source == output.resolve() or output.exists():
        raise ValueError("Output already exists. Choose a new output filename.")
    if output.suffix.lower() not in {".m4a", ".m4b"}:
        raise ValueError("Output must end in .m4a or .m4b.")
    checked = validate_chapters(chapters, duration)
    info = probe(source)
    metadata = [";FFMETADATA1"]
    for index, chapter in enumerate(checked):
        end = checked[index + 1]["start"] if index + 1 < len(checked) else round(duration * 1000)
        metadata.extend(["[CHAPTER]", "TIMEBASE=1/1000", f"START={chapter['start']}",
                         f"END={end}", f"title={escape_metadata(chapter['title'])}"])
    # Work in the destination filesystem; publish only after verifying chapters.
    with tempfile.TemporaryDirectory(prefix=".chapterise-", dir=output.parent) as directory:
        meta = Path(directory) / "chapters.ffmetadata"
        temp = Path(directory) / output.name
        meta.write_text("\n".join(metadata) + "\n", encoding="utf-8")
        command = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n",
                   "-i", str(source), "-f", "ffmetadata", "-i", str(meta), "-map", "0:a"]
        for stream in info["streams"]:
            if stream.get("disposition", {}).get("attached_pic"):
                command.extend(["-map", f"0:{stream['index']}"])
        command.extend(["-map_metadata", "0", "-map_chapters", "1", "-c", "copy",
                        "-movflags", "+faststart", "-f", "mp4", str(temp)])
        run(command)
        actual = probe(temp).get("chapters", [])
        if len(actual) != len(checked) or any(
            abs(float(a["start_time"]) * 1000 - b["start"]) > 2
            or a.get("tags", {}).get("title") != b["title"]
            for a, b in zip(actual, checked)
        ):
            raise ValueError("Chapter verification failed; output was not published.")
        # Exclusive create also protects against files created during the export.
        created = False
        try:
            with output.open("xb") as destination, temp.open("rb") as original:
                created = True
                shutil.copyfileobj(original, destination)
        except BaseException:
            if created:
                output.unlink(missing_ok=True)
            raise
    return output


class Session:
    def __init__(self, source, output):
        self.source, self.output = source, output
        self.info = probe(source)
        self.duration = number(self.info["format"]["duration"], "Audio duration", 0.001, 1e9)
        self.fingerprint = self.signature()
        self.lock = threading.Lock()
        self.job = {"status": "idle", "progress": 0}
        self.rows = [{"start": float(c["start_time"]), "title": c.get("tags", {}).get("title", ""),
                      "selected": True, "kind": "existing", "pause": None}
                     for c in self.info.get("chapters", [])]
        if not self.rows or self.rows[0]["start"] > 0:
            self.rows.insert(0, {"start": 0, "title": "Chapter 1", "selected": True,
                                 "kind": "start", "pause": None})

    def signature(self):
        stat = self.source.stat()
        return (stat.st_size, stat.st_mtime_ns, stat.st_ino)

    def unchanged(self):
        if self.signature() != self.fingerprint:
            raise ValueError("The source file changed. Restart the app before continuing.")

    def state(self):
        with self.lock:
            return {"filename": self.source.name, "duration": self.duration,
                    "output": str(self.output), "rows": self.rows, "job": self.job.copy()}

    def start_job(self, operation):
        with self.lock:
            if self.job["status"] == "running":
                raise ValueError("An operation is already running.")
            self.job = {"status": "running", "progress": 0}

        def worker():
            try:
                self.unchanged()
                result = operation()
                with self.lock:
                    self.job.update(status="done", progress=100, result=result)
            except Exception as error:
                with self.lock:
                    self.job.update(status="error", error=str(error))
        threading.Thread(target=worker, daemon=True).start()

    def scan(self, settings):
        def progress(value):
            with self.lock:
                self.job["progress"] = round(value)
        candidates = detect_pauses(self.source, self.duration, settings.get("noise", -35),
                                   settings.get("minimum", 2), settings.get("spacing", 60), progress)
        self.unchanged()
        existing = [r for r in self.rows if r["kind"] != "pause"]
        candidates = [r for r in candidates if all(abs(r["start"] - e["start"]) > 0.5 for e in existing)]
        with self.lock:
            self.rows = sorted(existing + candidates, key=lambda r: r["start"])
        return {"operation": "scan", "count": len(candidates)}


def handler_for(session, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Don't log the private session URL.

        def route(self):
            prefix = f"/{token}/"
            path = urlsplit(self.path).path
            host = self.headers.get("Host", "")
            expected = f"127.0.0.1:{self.server.server_port}"
            if host != expected or not path.startswith(prefix):
                self.send_error(403)
                return None
            return path[len(prefix):]

        def send_bytes(self, data, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def json(self, data, status=200):
            self.send_bytes(json.dumps(data).encode(), "application/json", status)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            route = self.route()
            if route is None:
                return
            try:
                if route == "api/state":
                    self.json(session.state())
                elif route == "audio":
                    self.audio()
                elif route in {"", "app.js", "style.css"}:
                    filename, kind = {"": ("index.html", "text/html; charset=utf-8"),
                                      "app.js": ("app.js", "text/javascript"),
                                      "style.css": ("style.css", "text/css")}[route]
                    self.send_bytes((STATIC / filename).read_bytes(), kind)
                else:
                    self.send_error(404)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def audio(self):
            size = session.source.stat().st_size
            start, end, partial = 0, size - 1, False
            byte_range = self.headers.get("Range")
            if byte_range:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range)
                if not match or not any(match.groups()):
                    self.send_error(416)
                    return
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(int(right), size - 1) if right else size - 1
                else:
                    start = max(0, size - int(right))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                partial = True
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "audio/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-store")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with session.source.open("rb") as audio:
                audio.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = audio.read(min(remaining, 256 * 1024))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)

        def do_POST(self):
            route = self.route()
            if route is None:
                return
            try:
                origin = self.headers.get("Origin")
                if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
                    raise ValueError("Unexpected request origin.")
                if self.headers.get("Content-Type") != "application/json":
                    raise ValueError("Expected JSON.")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2_000_000:
                    raise ValueError("Invalid request size.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object.")
                if route == "api/scan":
                    # Validate before launching a long-running job.
                    number(body.get("noise", -35), "Silence threshold", -90, -5)
                    number(body.get("minimum", 2), "Minimum pause", 0.1, 120)
                    number(body.get("spacing", 60), "Minimum chapter length", 0, 86400)
                    session.start_job(lambda: session.scan(body))
                elif route == "api/export":
                    if body.get("approved") is not True:
                        raise ValueError("Review and approve the chapter list first.")
                    chapters = body.get("chapters")
                    validate_chapters(chapters, session.duration)
                    session.start_job(lambda: {"operation": "export", "path": str(
                        write_chapters(session.source, session.output, chapters, session.duration))})
                else:
                    self.send_error(404)
                    return
                self.json({"status": "started"}, 202)
            except (ValueError, OSError) as error:
                self.json({"error": str(error)}, 400)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="Source .m4a or .m4b file")
    parser.add_argument("--output", type=Path, help="New output path; defaults to NAME.chaptered.m4a")
    parser.add_argument("--port", type=int, default=0, help="Local port; default chooses a free port")
    args = parser.parse_args()
    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            parser.error(f"Install FFmpeg and ensure {binary} is on PATH.")
    source = args.file.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in {".m4a", ".m4b"}:
        parser.error("Provide an existing .m4a or .m4b file.")
    output = (args.output.expanduser().absolute() if args.output else
              source.with_name(f"{source.stem}.chaptered{source.suffix}"))
    if output.exists() or output.suffix.lower() not in {".m4a", ".m4b"} or not output.parent.is_dir():
        parser.error("Output must be a new .m4a/.m4b file in an existing directory.")
    try:
        session = Session(source, output)
        token = secrets.token_urlsafe(24)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(session, token))
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"Open http://127.0.0.1:{server.server_port}/{token}/", flush=True)
    print(f"Approved output: {output}\nPress Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
