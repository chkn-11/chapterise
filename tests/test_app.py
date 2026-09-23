import array
import hashlib
import http.client
import json
import math
import queue
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import threading
import time
import unittest
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import (Session, ThreadingHTTPServer, detect_pauses, handler_for, probe,
                 run, validate_chapters, verify_chapters, write_chapters, validate_public_origin)


class ValidationTests(unittest.TestCase):
    def test_public_origin_validation(self):
        self.assertEqual(validate_public_origin('http://192.168.1.50:8765/'), 'http://192.168.1.50:8765')
        self.assertEqual(validate_public_origin('https://Books.Example:443'), 'https://books.example')
        self.assertIsNone(validate_public_origin(None))
        for value in ('https://example/path', 'http://user:secret@example', 'file:///tmp',
                      'http://example:0', 'http://example:99999', 'http://example?query',
                      'http://bad host', 'http://example\\other'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_public_origin(value)

    def test_server_origin_allows_configured_host_and_rejects_others(self):
        session = Session()
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(session, 'test', 'http://books.example:8765'))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        def request(host, origin=None, forwarded=None):
            headers = {'Host': host, 'Content-Type': 'application/json'}
            if origin:
                headers['Origin'] = origin
            if forwarded:
                headers['X-Forwarded-Host'] = forwarded
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            connection.request('POST' if origin else 'GET', '/test/api/cancel' if origin else '/test/api/state',
                               '{}' if origin else None, headers)
            response = connection.getresponse()
            code = response.status
            response.read()
            connection.close()
            return code
        try:
            self.assertEqual(request('books.example:8765'), 200)
            self.assertEqual(request('books.example:8765', 'http://books.example:8765'), 200)
            self.assertEqual(request('books.example:8765', 'http://other.example'), 400)
            self.assertEqual(request('other.example', forwarded='books.example:8765'), 403)
        finally:
            server.shutdown()
            server.server_close()

    def test_verification_reports_specific_mismatches(self):
        expected = [{"start": 0, "title": "Intro"}]
        valid = {"start_time": "0", "end_time": "10", "tags": {"title": "Intro"}}
        verify_chapters([valid], expected, 10)
        cases = [([], "Expected 1 chapters, found 0"),
                 ([{**valid, "start_time": "0.100"}], "Chapter 1 start"),
                 ([{**valid, "end_time": "9.500"}], "Chapter 1 end"),
                 ([{**valid, "start_time": "nan"}], "Chapter 1 start"),
                 ([{**valid, "start_time": None}], "no valid start time"),
                 ([{**valid, "tags": {"title": "Wrong"}}], "Chapter 1 title")]
        for actual, detail in cases:
            with self.subTest(detail=detail), self.assertRaisesRegex(ValueError, detail):
                verify_chapters(actual, expected, 10)

    def test_reject_invalid_boundaries_and_titles(self):
        invalid = [[], [{"start": 1, "title": "Missing beginning"}],
                   [{"start": float("nan"), "title": "NaN"}],
                   [{"start": 0, "title": "Empty"}, {"start": 10, "title": "At end"}],
                   [{"start": 0, "title": "A"}, {"start": .0001, "title": "Duplicate"}],
                   [{"start": 0, "title": ""}], [{"start": 0, "title": "Injected\n[CHAPTER]"}]]
        for chapters in invalid:
            with self.subTest(chapters=chapters), self.assertRaises(ValueError):
                validate_chapters(chapters, 10)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.source = cls.directory / "original.m4a"
        wav = cls.directory / "source.wav"
        rate = 16000
        samples = array.array("h", (int(12000 * math.sin(2 * math.pi * 440 * i / rate))
                                    if 1 <= i / rate < 5 or 8 <= i / rate < 14 or 17 <= i / rate < 21
                                    else 0 for i in range(rate * 22)))
        if sys.byteorder != "little":
            samples.byteswap()
        with wave.open(str(wav), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(samples.tobytes())
        cover = cls.directory / "cover.ppm"
        cover.write_bytes(b"P6\n16 16\n255\n" + bytes([255, 0, 0]) * 256)
        run(["ffmpeg", "-v", "error", "-i", str(wav), "-i", str(cover),
             "-map", "0:a", "-map", "1:v", "-c:a", "aac", "-c:v", "mjpeg",
             "-disposition:v", "attached_pic", "-metadata", "title=Test book", "-metadata", "artist=Reader",
             str(cls.source)])
        cls.duration = float(probe(cls.source)["format"]["duration"])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_detection_and_spacing(self):
        pauses = detect_pauses(self.source, self.duration, minimum=2, spacing=0)
        self.assertEqual(len(pauses), 2)
        self.assertAlmostEqual(pauses[0]["start"], 6.5, delta=.1)
        self.assertAlmostEqual(pauses[1]["start"], 15.5, delta=.1)
        self.assertTrue(all(p["selected"] for p in pauses))
        # With a shorter detection threshold, leading/trailing silence is still excluded.
        self.assertEqual(len(detect_pauses(self.source, self.duration, minimum=.5, spacing=0)), 2)
        spaced = detect_pauses(self.source, self.duration, minimum=2, spacing=10)
        self.assertEqual(len(spaced), 2)
        self.assertFalse(any(p["selected"] for p in spaced))
        self.assertEqual(detect_pauses(self.source, self.duration, minimum=5), [])

    def test_export_preserves_audio_cover_tags_and_source(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        output = self.directory / "chaptered.m4b"
        chapters = [{"start": 0, "title": "Intro = #1; \\ café"},
                    {"start": 6.5, "title": "Chapter two"}, {"start": 15.5, "title": "Last chapter"}]
        write_chapters(self.source, output, chapters, self.duration)
        info = probe(output)
        self.assertEqual([c["tags"]["title"] for c in info["chapters"]], [c["title"] for c in chapters])
        self.assertEqual(info["format"]["tags"]["title"], "Test book")
        self.assertEqual(info["format"]["tags"]["artist"], "Reader")
        self.assertTrue(any(s.get("disposition", {}).get("attached_pic") for s in info["streams"]))
        def encoded_audio_hash(path):
            return run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
                        "-c", "copy", "-f", "hash", "-hash", "sha256", "-"])
        self.assertEqual(encoded_audio_hash(self.source), encoded_audio_hash(output))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)
        with self.assertRaises(ValueError):
            write_chapters(self.source, output, chapters, self.duration)
        with self.assertRaises(ValueError):
            write_chapters(self.source, self.source, chapters, self.duration)
        # An already-chaptered input must have its chapters replaced cleanly.
        revised = self.directory / "revised.m4a"
        write_chapters(output, revised, [{"start": 0, "title": "One chapter"}], self.duration)
        self.assertEqual(len(probe(revised)["chapters"]), 1)
        self.assertEqual(encoded_audio_hash(output), encoded_audio_hash(revised))

    def test_long_book_chapters_keep_millisecond_timestamps(self):
        # A short audio fixture keeps this fast while the chapter track spans 37h.
        # Multi-hour chapter samples overflow with FFmpeg 9's automatic timescale.
        duration = 133752.976
        chapters = [{"start": 0, "title": "Opening"},
                    {"start": 138.629, "title": "Prelude"},
                    {"start": 28321.590, "title": "Part two"},
                    {"start": 61273.282, "title": "Part three"},
                    {"start": 133514.770, "title": "Closing"}]
        output = self.directory / "long-book.m4b"
        write_chapters(self.source, output, chapters, duration)
        actual = probe(output)["chapters"]
        self.assertEqual(len(actual), len(chapters))
        for index, (written, approved) in enumerate(zip(actual, chapters)):
            self.assertAlmostEqual(float(written["start_time"]), approved["start"], places=3)
            end = chapters[index + 1]["start"] if index + 1 < len(chapters) else duration
            self.assertAlmostEqual(float(written["end_time"]), end, places=3)
            self.assertEqual(written["tags"]["title"], approved["title"])

    def test_verification_failure_does_not_publish_output(self):
        output = self.directory / "failed-verification.m4a"
        with patch("app.verify_chapters", side_effect=ValueError("Chapter verification failed")), self.assertRaisesRegex(ValueError, "Chapter verification failed"):
            write_chapters(self.source, output, [{"start": 0, "title": "Opening"}], self.duration)
        self.assertFalse(output.exists())

    def test_http_preview_and_approval(self):
        output = self.directory / "http-approved.m4a"
        session = Session(self.source, output)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(session, "test-token"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.request(method, path, json.dumps(body) if body is not None else None,
                               headers or ({"Content-Type": "application/json"} if body is not None else {}))
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            connection.close()
            return result
        try:
            self.assertEqual(request("GET", "/wrong/api/state")[0], 403)
            self.assertEqual(request("GET", "/test-token/")[0], 200)
            code, headers, data = request("GET", "/test-token/audio", headers={"Range": "bytes=10-29"})
            self.assertEqual(code, 206)
            self.assertEqual(data, self.source.read_bytes()[10:30])
            self.assertEqual(headers["Content-Length"], "20")
            self.assertEqual(request("GET", "/test-token/audio", headers={"Range": "bytes=999999999-"})[0], 416)
            chapters = [{"start": 0, "title": "Reviewed"}, {"start": 6.5, "title": "Next"}]
            self.assertEqual(request("POST", "/test-token/api/export", {"chapters": chapters})[0], 400)
            self.assertFalse(output.exists())
            self.assertEqual(request("POST", "/test-token/api/export", {"approved": True, "chapters": chapters})[0], 202)
            deadline = time.monotonic() + 15
            while session.state()["job"]["status"] == "running" and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertEqual(session.state()["job"]["status"], "done", session.state())
            self.assertEqual(len(probe(output)["chapters"]), 2)
        finally:
            server.shutdown()
            server.server_close()

    def test_container_listen_address_keeps_private_url_checks(self):
        process = subprocess.Popen([
            sys.executable, str(Path(__file__).resolve().parents[1] / 'app.py'),
            '--host', '0.0.0.0', '--port', '0', '--workspace', str(self.directory / 'container-projects'),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            lines = queue.Queue()
            threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
            line = lines.get(timeout=15)
            self.assertTrue(line.startswith('Open http://127.0.0.1:'), line)
            from urllib.parse import urlsplit
            url = urlsplit(line.strip().removeprefix('Open '))
            def request(path, method='GET', headers=None, body=None):
                connection = http.client.HTTPConnection('127.0.0.1', url.port, timeout=5)
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                result = response.status, response.read()
                connection.close()
                return result
            code, data = request(url.path + 'api/state')
            self.assertEqual(code, 200)
            self.assertIsNone(json.loads(data)['filename'])
            self.assertEqual(request('/')[0], 403)
            self.assertEqual(request(url.path, headers={'Host': 'untrusted.example'})[0], 403)
            code, data = request(url.path + 'api/review', 'POST',
                                 {'Content-Type': 'application/json', 'Origin': 'http://untrusted.example'}, '{}')
            self.assertEqual(code, 400)
            self.assertIn(b'Unexpected request origin', data)
        finally:
            process.terminate()
            process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
