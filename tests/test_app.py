import array
import hashlib
import http.client
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import (Session, ThreadingHTTPServer, detect_pauses, handler_for, probe,
                 run, validate_chapters, write_chapters)


class ValidationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
