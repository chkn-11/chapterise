"""Optional local speech recognition with resumable, bounded audio chunks."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from persistence import save_json

MODELS = {"tiny", "base", "small"}
CHUNK_SECONDS = 300


def validate_options(model, language):
    if not isinstance(model, str) or model not in MODELS:
        raise ValueError("Choose the tiny, base, or small transcription model.")
    if language is not None and (not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}", language)):
        raise ValueError("Use a language code such as en, or leave language blank for automatic detection.")


def transcribe_audio(source, duration, model="base", language=None, progress=None,
                     checkpoint_dir=None, cancelled=None, word_timestamps=False):
    validate_options(model, language)
    report = progress or (lambda *_: None)
    checkpoint = None
    if checkpoint_dir is not None:
        stat = Path(source).stat()
        identity = [str(Path(source).resolve()), stat.st_size, stat.st_mtime_ns, duration,
                    model, language, word_timestamps, CHUNK_SECONDS, 1]
        key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        checkpoint = Path(checkpoint_dir) / key
        checkpoint.mkdir(parents=True, exist_ok=True)
    recognizer = None
    transcript, languages = [], set()
    def check_cancel():
        if cancelled and cancelled():
            raise InterruptedError('Paused. Start again to resume from the last completed audio chunk.')
    with tempfile.TemporaryDirectory(prefix="chapterise-speech-") as directory:
        clip = Path(directory) / "chunk.wav"
        for offset in range(0, math.ceil(duration), CHUNK_SECONDS):
            check_cancel()
            length = min(CHUNK_SECONDS, duration - offset)
            cached = checkpoint / f'{offset}.json' if checkpoint else None
            if cached and cached.exists():
                try:
                    chunk = json.loads(cached.read_text(encoding='utf-8'))
                except (ValueError, OSError):
                    chunk = None
                if isinstance(chunk, dict) and isinstance(chunk.get('segments'), list) and 'language' in chunk:
                    transcript.extend(chunk['segments'])
                    if chunk['segments']:
                        languages.add(chunk['language'])
                    report(min(99, round((offset + length) / duration * 100)), 'Restoring completed transcription chunks…')
                    continue
            if recognizer is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError:
                    raise ValueError("Transcription requires the optional speech package. Follow the Transcription setup steps in README.md and restart the app using that Python environment.") from None
                report(min(99, round(offset / duration * 100)), 'Loading speech model; first use downloads model files…')
                recognizer = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=min(4, os.cpu_count() or 1))
            report(min(99, round(offset / duration * 100)), "Transcribing audio locally…")
            result = subprocess.run([
                "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
                "-ss", str(offset), "-i", str(source), "-t", str(length), "-map", "0:a:0",
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(clip),
            ], capture_output=True, text=True)
            if result.returncode:
                raise ValueError(result.stderr[-3000:] or "Could not decode audio for transcription.")
            segments, info = recognizer.transcribe(str(clip), language=language, vad_filter=True,
                                                   beam_size=5, condition_on_previous_text=False,
                                                   word_timestamps=word_timestamps)
            completed = []
            for segment in segments:
                check_cancel()
                text = segment.text.strip()
                start = round(max(offset, offset + segment.start), 3)
                end = round(min(duration, offset + length, offset + segment.end), 3)
                if text and math.isfinite(start) and math.isfinite(end) and start < end:
                    item = {"start": start, "end": end, "text": text}
                    if word_timestamps and getattr(segment, 'words', None):
                        item['words'] = [{'word': w.word, 'start': round(max(start, offset + w.start), 3),
                                          'end': round(min(end, offset + w.end), 3)} for w in segment.words
                                         if math.isfinite(w.start) and math.isfinite(w.end)
                                         and min(end, offset + w.end) >= max(start, offset + w.start)]
                    completed.append(item)
                    languages.add(info.language)
                report(min(99, round(min(offset + length, offset + segment.end) / duration * 100)), "Transcribing audio locally…")
            check_cancel()
            if cached:
                save_json(cached, {'segments': completed, 'language': info.language})
            transcript.extend(completed)
    return {"segments": sorted(transcript, key=lambda s: s["start"]),
            "model": model, "languages": sorted(languages), 'word_timestamps': word_timestamps}
