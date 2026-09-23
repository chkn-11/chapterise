#!/usr/bin/env python3
"""Local M4A chapter review. Python standard library + FFmpeg only."""

import argparse
from collections import deque
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import importlib.util
import hashlib
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote, quote

from transcription import transcribe_audio, validate_options
from epub_match import read_epub, match_book
from persistence import save_json


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


def verify_chapters(actual, expected, duration):
    """Reject changed chapter metadata and identify the first mismatch."""
    def fail(detail):
        raise ValueError(f"Chapter verification failed: {detail} Output was not published.")

    if len(actual) != len(expected):
        fail(f"Expected {len(expected)} chapters, found {len(actual)}.")
    for index, (written, approved) in enumerate(zip(actual, expected)):
        for field, target in (
            ("start", approved["start"]),
            ("end", expected[index + 1]["start"] if index + 1 < len(expected) else round(duration * 1000)),
        ):
            try:
                value = float(written[f"{field}_time"]) * 1000
            except (KeyError, TypeError, ValueError):
                fail(f"Chapter {index + 1} has no valid {field} time.")
            if not math.isfinite(value) or abs(value - target) > 2:
                fail(f"Chapter {index + 1} {field} is {value / 1000:.3f}s; expected {target / 1000:.3f}s.")
        if written.get("tags", {}).get("title") != approved["title"]:
            fail(f"Chapter {index + 1} title differs from the approved title.")


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
                        # FFmpeg's automatic movie timescale can overflow chapter
                        # sample durations on long books. Match our 1 ms metadata.
                        "-movie_timescale", "1000", "-movflags", "+faststart", "-f", "mp4", str(temp)])
        run(command)
        actual = probe(temp).get("chapters", [])
        verify_chapters(actual, checked, duration)
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


def validate_review_rows(rows, duration):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise ValueError('Invalid saved chapter list.')
    starts = set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get('selected')) is not bool:
            raise ValueError('Invalid chapter marker.')
        if type(row.get('start')) not in (int, float):
            raise ValueError('Chapter start must be a number.')
        start = number(row.get('start'), 'Chapter start', 0, duration)
        tick = round(start * 1000)
        if tick >= round(duration * 1000) or tick in starts:
            raise ValueError('Chapter markers must have distinct times before the audio ends.')
        starts.add(tick)
        if not isinstance(row.get('title'), str) or len(row['title']) > 500:
            raise ValueError('Invalid chapter title.')
        if row.get('kind') not in {'start', 'pause', 'manual', 'existing', 'epub'}:
            raise ValueError('Invalid chapter source.')
        if 'originalStart' in row:
            number(row['originalStart'], 'Original marker time', 0, duration)
    if not any(row['start'] == 0 and row['selected'] for row in rows):
        raise ValueError('Keep the opening chapter selected at zero.')
    return sorted(rows, key=lambda row: row['start'])


def validate_transcript(value, duration):
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get('segments'), list) or len(value['segments']) > 200000:
        raise ValueError('Invalid saved transcript.')
    for segment in value['segments']:
        if not isinstance(segment, dict) or not isinstance(segment.get('text'), str) or len(segment['text']) > 10000:
            raise ValueError('Invalid transcript passage.')
        for key in ('start', 'end'):
            if type(segment.get(key)) not in (int, float):
                raise ValueError('Invalid transcript time.')
            number(segment[key], 'Transcript time', 0, duration + .001)
        if segment['end'] <= segment['start']:
            raise ValueError('Invalid transcript passage duration.')
        words = segment.get('words', [])
        if not isinstance(words, list) or len(words) > 10000:
            raise ValueError('Invalid transcript words.')
        for word in words:
            if not isinstance(word, dict) or not isinstance(word.get('word'), str):
                raise ValueError('Invalid transcript word.')
            for key in ('start', 'end'):
                if type(word.get(key)) not in (int, float):
                    raise ValueError('Invalid word time.')
                number(word[key], 'Word time', segment['start'], segment['end'])
            if word['end'] < word['start']:
                raise ValueError('Invalid word duration.')
        words.sort(key=lambda w: w['start'])
    value['segments'].sort(key=lambda s: s['start'])
    return value


class Session:
    def __init__(self, source=None, output=None, workspace=None):
        self.workspace = Path(workspace).resolve() if workspace else None
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.job = {'status': 'idle', 'progress': 0}
        self.source = self.output = self.project = None
        self.info, self.rows, self.duration = {}, [], 0
        self.transcript = self.book = self.alignment = None
        self.omitted_sections = []
        self.notice = ''
        if self.workspace:
            self.workspace.mkdir(parents=True, exist_ok=True)
        if source:
            self.load_source(Path(source), output)
        elif self.workspace and (self.workspace / 'last.json').exists():
            try:
                self.open_project(json.loads((self.workspace / 'last.json').read_text())['id'])
            except (ValueError, OSError, KeyError) as error:
                self.notice = f'Could not reopen the previous project: {error}'

    def load_source(self, source, output=None):
        info = probe(source)
        duration = number(info['format']['duration'], 'Audio duration', .001, 1e9)
        with self.lock:
            self.source = source.resolve()
            self.output = Path(output) if output else self.source.with_name(self.source.stem + '.chaptered' + self.source.suffix)
            self.info, self.duration = info, duration
            self.fingerprint = self.signature()
            self.transcript = self.book = self.alignment = None
            self.omitted_sections = []
            self.rows = [{'start': float(c['start_time']), 'title': c.get('tags', {}).get('title', ''),
                          'selected': True, 'kind': 'existing', 'pause': None} for c in info.get('chapters', [])]
            if not self.rows or self.rows[0]['start'] > 0:
                self.rows.insert(0, {'start': 0, 'title': 'Chapter 1', 'selected': True, 'kind': 'start', 'pause': None})
            self.project = None
            if self.workspace:
                key = hashlib.sha256(json.dumps([str(self.source), self.fingerprint]).encode()).hexdigest()[:24]
                self.project = self.workspace / key
                saved = self.project / 'project.json'
                if saved.exists():
                    data = json.loads(saved.read_text(encoding='utf-8'))
                    if tuple(data['fingerprint']) == self.fingerprint:
                        self.rows = validate_review_rows(data['rows'], duration)
                        self.transcript, self.book, self.alignment = data.get('transcript'), data.get('book'), data.get('alignment')
                        self.omitted_sections = data.get('omitted_sections', [])
                save_json(self.workspace / 'last.json', {'id': key})
                self.persist()

    def persist(self):
        if self.project:
            with self.lock:
                save_json(self.project / 'project.json', {
                    'source': str(self.source), 'output': str(self.output), 'fingerprint': self.fingerprint,
                    'duration': self.duration, 'rows': self.rows, 'transcript': self.transcript,
                    'book': self.book, 'alignment': self.alignment, 'omitted_sections': self.omitted_sections})

    def projects(self):
        result = []
        if self.workspace:
            for file in self.workspace.glob('*/project.json'):
                try:
                    data = json.loads(file.read_text(encoding='utf-8'))
                    result.append({'id': file.parent.name, 'filename': Path(data['source']).name})
                except (ValueError, OSError, KeyError):
                    continue
        return result

    def open_project(self, identifier):
        if not self.workspace or not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{24}', identifier):
            raise ValueError('Invalid project identifier.')
        data = json.loads((self.workspace / identifier / 'project.json').read_text(encoding='utf-8'))
        source = Path(data['source'])
        stat = source.stat()
        if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != tuple(data['fingerprint']):
            raise ValueError('The source changed; upload it again to create a new project.')
        self.load_source(source, data['output'])

    def signature(self):
        if not self.source:
            raise ValueError('Upload an M4A or M4B file first.')
        stat = self.source.stat()
        return (stat.st_size, stat.st_mtime_ns, stat.st_ino)

    def unchanged(self):
        if self.signature() != self.fingerprint:
            raise ValueError('The source file changed. Reopen it before continuing.')

    def state(self):
        with self.lock:
            return {'filename': self.source.name if self.source else None, 'duration': self.duration,
                    'output': str(self.output) if self.output else '', 'rows': self.rows, 'job': self.job.copy(),
                    'transcription_available': importlib.util.find_spec('faster_whisper') is not None,
                    'transcript_ready': self.transcript is not None,
                    'book_title': self.book['title'] if self.book else None,
                    'alignment_ready': self.alignment is not None, 'omitted_sections': self.omitted_sections,
                    'project_id': self.project.name if self.project else None, 'notice': self.notice}

    def progress(self, value, detail):
        with self.lock:
            self.job.update(progress=value, detail=detail)

    def transcribe(self, settings):
        options = {}
        if self.project:
            options = {'checkpoint_dir': self.project / 'chunks', 'cancelled': self.cancelled.is_set,
                       'word_timestamps': True}
        transcript = transcribe_audio(self.source, self.duration, settings.get('model', 'base'),
                                      settings.get('language') or None, self.progress, **options)
        self.unchanged()
        with self.lock:
            self.transcript = transcript
            self.alignment = None
            self.persist()
        return {'operation': 'transcribe', 'count': len(transcript['segments'])}

    def align(self, settings):
        if not self.book:
            raise ValueError('Upload the matching EPUB first.')
        if self.transcript is None:
            self.transcribe(settings)
        if self.cancelled.is_set():
            raise InterruptedError('Paused. Start again to resume.')
        def progress(value, detail):
            if self.cancelled.is_set():
                raise InterruptedError('Matching paused. Run again to retry; the transcript is saved.')
            self.progress(value, detail)
        result = match_book(self.book, self.transcript, progress)
        self.unchanged()
        with self.lock:
            self.alignment = result
            self.persist()
        return {'operation': 'align', 'count': sum(p['start'] is not None for p in result['proposals'])}

    def start_job(self, operation, cancellable=False):
        with self.lock:
            if self.job['status'] == 'running':
                raise ValueError('An operation is already running.')
            self.job = {'status': 'running', 'progress': 0, 'cancellable': cancellable}
            self.cancelled.clear()
        def worker():
            try:
                self.unchanged()
                result = operation()
                with self.lock:
                    self.job.update(status='done', progress=100, result=result)
            except Exception as error:
                with self.lock:
                    self.job.update(status='error', error=str(error))
        threading.Thread(target=worker, daemon=True).start()

    def scan(self, settings):
        candidates = detect_pauses(self.source, self.duration, settings.get('noise', -35),
                                   settings.get('minimum', 2), settings.get('spacing', 60),
                                   lambda value: self.progress(round(value), 'Finding pauses…'))
        self.unchanged()
        existing = [r for r in self.rows if r['kind'] != 'pause']
        candidates = [r for r in candidates if all(abs(r['start'] - e['start']) > .5 for e in existing)]
        with self.lock:
            self.rows = sorted(existing + candidates, key=lambda r: r['start'])
            self.persist()
        return {'operation': 'scan', 'count': len(candidates)}


def validate_public_origin(value):
    """One explicit browser origin; never trust request/forwarded headers as configuration."""
    if not value:
        return None
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or
            parsed.path not in {'', '/'} or parsed.query or parsed.fragment or
            re.search(r'[\s\\]', value) or
            not re.fullmatch(r'[a-zA-Z0-9._:-]+', parsed.hostname)):
        raise ValueError('Public origin must be an http(s) URL with a host and optional port, without a path or credentials.')
    port = parsed.port
    if port == 0:
        raise ValueError('Public origin requires a nonzero port.')
    hostname = f'[{parsed.hostname}]' if ':' in parsed.hostname else parsed.hostname
    suffix = f':{port}' if port and port != (443 if parsed.scheme == 'https' else 80) else ''
    return f'{parsed.scheme}://{hostname}{suffix}'


def handler_for(session, token=None, public_origin=None):
    public_origin = validate_public_origin(public_origin)
    class Handler(BaseHTTPRequestHandler):
        def browser_origin(self):
            hosts = self.headers.get_all('Host', [])
            if len(hosts) != 1 or not hosts[0]:
                raise ValueError('Expected one Host header.')
            requested = validate_public_origin('http://' + hosts[0])
            if public_origin and urlsplit(public_origin).netloc != urlsplit(requested).netloc:
                raise ValueError('Unexpected request host.')
            return public_origin or requested

        def log_message(self, *_):
            pass

        def route(self):
            prefix = f"/{token}/" if token else '/'
            path = urlsplit(self.path).path
            try:
                self.browser_origin()
            except ValueError:
                self.send_error(403)
                return None
            if not path.startswith(prefix):
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
                elif route == "api/transcript":
                    with session.lock:
                        transcript = session.transcript
                    self.json(transcript)
                elif route == "api/alignment":
                    self.json({'book': {'title': session.book['title'], 'chapters': [
                        {k: c[k] for k in ('id', 'title', 'word_count')} for c in session.book['chapters']],
                        'warnings': session.book['warnings']} if session.book else None,
                        'alignment': session.alignment})
                elif route == "api/projects":
                    self.json(session.projects())
                elif route == "export" and session.output and session.output.is_file():
                    self.audio(session.output, download=True)
                elif route == "audio" and session.source:
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

        def audio(self, path=None, download=False):
            source = path or session.source
            size = source.stat().st_size
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
            if download:
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(source.name))
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-store")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with source.open("rb") as audio:
                audio.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = audio.read(min(remaining, 256 * 1024))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)

        def upload(self, route):
            if not session.workspace:
                raise ValueError('Uploads require a project workspace. Restart using app.py.')
            name = unquote(self.headers.get('X-File-Name', ''))
            if not name or Path(name).name != name or '\\' in name or any(ord(c) < 32 for c in name):
                raise ValueError('Invalid upload filename.')
            audio = route.endswith('/audio')
            suffixes = {'.m4a', '.m4b'} if audio else {'.epub'}
            if Path(name).suffix.lower() not in suffixes:
                raise ValueError('Choose an M4A/M4B audio file or an EPUB book.')
            length = int(self.headers.get('Content-Length', '0'))
            maximum = 32 * 1024**3 if audio else 100 * 1024**2
            if not 0 < length <= maximum:
                raise ValueError('Upload exceeds the size limit (32 GB audio / 100 MB EPUB).')
            with session.lock:
                if session.job['status'] == 'running':
                    raise ValueError('An operation is already running.')
                if not audio and not session.source:
                    raise ValueError('Upload the audiobook first.')
                session.job = {'status': 'running', 'progress': 0, 'detail': 'Receiving file…'}
            directory = None
            try:
                directory = Path(tempfile.mkdtemp(prefix='upload-', dir=session.workspace))
                path = directory / name
                if shutil.disk_usage(directory).free < length + 100_000_000:
                    raise ValueError('Not enough disk space to receive this file.')
                self.connection.settimeout(600)
                with path.open('xb') as output:
                    remaining = length
                    while remaining:
                        block = self.rfile.read(min(remaining, 1024 * 1024))
                        if not block:
                            raise ValueError('Upload interrupted. Select the file and retry.')
                        output.write(block)
                        remaining -= len(block)
                if audio:
                    session.load_source(path)
                    directory = None  # The project owns the uploaded source now.
                else:
                    book = read_epub(path)
                    with session.lock:
                        session.book, session.alignment = book, None
                        session.omitted_sections = []
                        session.persist()
            finally:
                if directory:
                    shutil.rmtree(directory)
                with session.lock:
                    session.job = {'status': 'idle', 'progress': 0}
            self.json({'status': 'loaded'})

        def do_POST(self):
            route = self.route()
            if route is None:
                return
            try:
                origin = self.headers.get("Origin")
                if origin:
                    parsed_origin = validate_public_origin(origin)
                    expected = self.browser_origin()
                    if (not parsed_origin or (public_origin and parsed_origin != expected) or
                            urlsplit(parsed_origin).netloc != urlsplit(expected).netloc):
                        raise ValueError("Unexpected request origin.")
                if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                    raise ValueError('Unexpected cross-site request.')
                if route in {'api/upload/audio', 'api/upload/epub'}:
                    self.upload(route)
                    return
                if self.headers.get("Content-Type") != "application/json":
                    raise ValueError("Expected JSON.")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 100_000_000:
                    raise ValueError("Invalid request size.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object.")
                if route == 'api/cancel':
                    session.cancelled.set()
                    self.json({'status': 'pausing'})
                    return
                if route in {'api/review', 'api/open'}:
                    with session.lock:
                        if session.job['status'] == 'running':
                            raise ValueError('Wait for the current operation to finish.')
                        if route == 'api/open':
                            session.open_project(body.get('id'))
                            session.job = {'status': 'idle', 'progress': 0}
                        else:
                            session.unchanged()
                            if 'project_id' in body and body['project_id'] != (session.project.name if session.project else None):
                                raise ValueError('The open project changed. Reload this page before editing.')
                            rows = validate_review_rows(body.get('rows'), session.duration)
                            omitted = body.get('omitted_sections', session.omitted_sections)
                            if (not isinstance(omitted, list) or len(omitted) > 2000 or
                                    any(not isinstance(i, str) or len(i) > 100 for i in omitted)):
                                raise ValueError('Invalid omitted section list.')
                            if 'transcript' in body:
                                transcript = validate_transcript(body['transcript'], session.duration)
                                session.transcript, session.alignment = transcript, None
                            session.rows = rows
                            session.omitted_sections = list(dict.fromkeys(omitted))
                            session.persist()
                    self.json({'status': 'saved'})
                    return
                if route == 'api/align':
                    validate_options(body.get('model', 'base'), body.get('language') or None)
                    if not session.book:
                        raise ValueError('Upload the matching EPUB first.')
                    session.start_job(lambda: session.align(body), cancellable=True)
                elif route == "api/scan":
                    # Validate before launching a long-running job.
                    number(body.get("noise", -35), "Silence threshold", -90, -5)
                    number(body.get("minimum", 2), "Minimum pause", 0.1, 120)
                    number(body.get("spacing", 60), "Minimum chapter length", 0, 86400)
                    session.start_job(lambda: session.scan(body))
                elif route == "api/transcribe":
                    validate_options(body.get("model", "base"), body.get("language") or None)
                    session.start_job(lambda: session.transcribe(body), cancellable=True)
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
    parser.add_argument("file", nargs="?", type=Path, help="Optional source .m4a/.m4b; omit for browser uploads")
    parser.add_argument("--epub", type=Path, help="Optional matching EPUB")
    parser.add_argument("--workspace", type=Path, default=STATIC.parent / ".chapterise-data", help="Local project and transcription storage")
    parser.add_argument("--output", type=Path, help="New output path; defaults to NAME.chaptered.m4a")
    parser.add_argument("--port", type=int, default=8765, help="Listen port (default: 8765)")
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1",
                        help="Listen address; use 0.0.0.0 inside Docker")
    parser.add_argument("--public-origin", default=os.environ.get('CHAPTERISE_PUBLIC_ORIGIN'),
                        help="Optional restriction to one browser origin; otherwise accept the request host")
    args = parser.parse_args()
    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            parser.error(f"Install FFmpeg and ensure {binary} is on PATH.")
    source = args.file.expanduser().resolve() if args.file else None
    if source and (not source.is_file() or source.suffix.lower() not in {'.m4a', '.m4b'}):
        parser.error('Provide an existing .m4a or .m4b file.')
    if args.output and not source:
        parser.error('--output requires a source file.')
    output = args.output.expanduser().absolute() if args.output else None
    if output and (output.suffix.lower() not in {'.m4a', '.m4b'} or not output.parent.is_dir()):
        parser.error('Output must be an .m4a/.m4b file in an existing directory.')
    try:
        public_origin = validate_public_origin(args.public_origin)
        session = Session(source, output, args.workspace.expanduser())
        if args.epub:
            if not session.source:
                parser.error('Load an audiobook before adding an EPUB.')
            session.book = read_epub(args.epub.expanduser())
            session.alignment = None
            session.persist()
        server = ThreadingHTTPServer((args.host, args.port), handler_for(session, public_origin=public_origin))
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"Open {public_origin or f'http://localhost:{server.server_port}'}/", flush=True)
    if args.host == '0.0.0.0' and not public_origin:
        print(f"On another computer, open http://<server-address>:{server.server_port}/", flush=True)
    print(f"Projects: {session.workspace}\nPress Ctrl+C to stop. Completed transcription chunks are saved.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
