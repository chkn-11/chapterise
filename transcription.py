"""Optional local speech recognition, processing long recordings in bounded chunks."""

import math
import os
from pathlib import Path
import re
import subprocess
import tempfile


MODELS = {"tiny", "base", "small"}
CHUNK_SECONDS = 300


def validate_options(model, language):
    if not isinstance(model, str) or model not in MODELS:
        raise ValueError("Choose the tiny, base, or small transcription model.")
    if language is not None and (not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}", language)):
        raise ValueError("Use a language code such as en, or leave language blank for automatic detection.")


def transcribe_audio(source, duration, model="base", language=None, progress=None):
    validate_options(model, language)
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ValueError("Transcription requires the optional speech package. Follow the Transcription setup steps in README.md and restart the app using that Python environment.") from None

    report = progress or (lambda *_: None)
    report(0, "Loading speech model; first use downloads model files…")
    recognizer = WhisperModel(model, device="cpu", compute_type="int8",
                              cpu_threads=min(4, os.cpu_count() or 1))
    transcript, languages = [], set()
    with tempfile.TemporaryDirectory(prefix="chapterise-speech-") as directory:
        clip = Path(directory) / "chunk.wav"
        for offset in range(0, math.ceil(duration), CHUNK_SECONDS):
            length = min(CHUNK_SECONDS, duration - offset)
            report(round(offset / duration * 100), "Transcribing audio locally…")
            # Decode only five minutes at a time, rather than loading an entire book.
            result = subprocess.run([
                "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
                "-ss", str(offset), "-i", str(source), "-t", str(length), "-map", "0:a:0",
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(clip),
            ], capture_output=True, text=True)
            if result.returncode:
                raise ValueError(result.stderr[-3000:] or "Could not decode audio for transcription.")
            segments, info = recognizer.transcribe(str(clip), language=language,
                                                   vad_filter=True, beam_size=5,
                                                   condition_on_previous_text=False)
            for segment in segments:
                text = segment.text.strip()
                start = round(max(offset, offset + segment.start), 3)
                end = round(min(duration, offset + length, offset + segment.end), 3)
                if text and math.isfinite(start) and math.isfinite(end) and start < end:
                    transcript.append({"start": start, "end": end, "text": text})
                    languages.add(info.language)
                report(min(99, round(min(offset + length, offset + segment.end) / duration * 100)),
                       "Transcribing audio locally…")
    return {"segments": sorted(transcript, key=lambda s: s["start"]),
            "model": model, "languages": sorted(languages)}
