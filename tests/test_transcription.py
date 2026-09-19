import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transcription import transcribe_audio, validate_options


class TranscriptionTests(unittest.TestCase):
    def test_invalid_options(self):
        for model, language in [("../custom", None), ([], None), ("base", "English"), ("tiny", 5)]:
            with self.subTest(model=model, language=language), self.assertRaises(ValueError):
                validate_options(model, language)

    def test_chunks_have_absolute_timestamps_and_bounded_audio(self):
        recognizer = Mock()
        recognizer.transcribe.side_effect = [
            (iter([SimpleNamespace(start=1, end=3, text=" First chapter ")]), SimpleNamespace(language="en")),
            (iter([SimpleNamespace(start=2, end=15, text="Next chapter")]), SimpleNamespace(language="en")),
        ]
        factory = Mock(return_value=recognizer)
        progress = Mock()
        with patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=factory)}), patch("transcription.subprocess.run", return_value=SimpleNamespace(returncode=0)) as decode:
            result = transcribe_audio(Path("book.m4a"), 310, "tiny", "en", progress)
        self.assertEqual(result["segments"], [
            {"start": 1, "end": 3, "text": "First chapter"},
            {"start": 302, "end": 310, "text": "Next chapter"},
        ])
        self.assertEqual(result["languages"], ["en"])
        calls = [call.args[0] for call in decode.call_args_list]
        self.assertEqual([args[args.index("-t") + 1] for args in calls], ["300", "10"])
        self.assertEqual([args[args.index("-ss") + 1] for args in calls], ["0", "300"])
        self.assertTrue(all(args.index("-ss") < args.index("-i") for args in calls))
        self.assertTrue(all(0 <= call.args[0] <= 99 for call in progress.call_args_list))

    def test_missing_package_is_actionable(self):
        with patch.dict(sys.modules, {"faster_whisper": None}), self.assertRaisesRegex(ValueError, "optional speech package"):
            transcribe_audio(Path("book.m4a"), 10)

    def test_decode_failure_is_not_reported_as_success(self):
        factory = Mock()
        with patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=factory)}), patch("transcription.subprocess.run", return_value=SimpleNamespace(returncode=1, stderr="Unreadable audio")), self.assertRaisesRegex(ValueError, "Unreadable audio"):
            transcribe_audio(Path("book.m4a"), 10)


if __name__ == "__main__":
    unittest.main()
