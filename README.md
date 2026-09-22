# Chapterise

A local browser tool for finding potential chapters in M4A/M4B audio using pauses or a matching EPUB, reviewing the proposed breaks, and writing approved chapter metadata into a new file.

## EPUB-assisted chaptering

Install the optional speech recognizer using **Transcription setup** below, then start the upload interface:

```bash
.venv/bin/python app.py
```

1. Open the printed local URL. Choose an **Audiobook** and its **Matching book (.epub)**. Alternatively, use `.venv/bin/python app.py "/path/book.m4b" --epub "/path/book.epub"` to read existing files without copying the audiobook.
2. Choose a speech model and language, then **Find EPUB chapters**. The app transcribes the recording locally, reads the EPUB contents list, and searches for matching passages in chapter order. An existing transcript is reused; use **Generate searchable transcript** first if you want to change its model or language.
3. Each proposal shows its timestamp, evidence from the book and audio, and **Strong opening match**, **Needs review**, or **Unmatched**. Preview plays exactly from the proposed timestamp for ten seconds. **Use this marker** adds it to the review; **Locate manually** fills the manual chapter editor. Accepted markers support the existing timestamp editor, ±15-second slider, removal, and undo.
4. Review the selected markers, approve them, and export. Uploads export to the local project storage; **Download exported audio** saves the verified result through your browser. Source audio is never altered, and suggestions never export themselves.

This first version combines AI speech recognition with phrase alignment; it does not use an LLM or a paid agent service. EPUB 2 NCX and EPUB 3 navigation are supported, including multiple chapter anchors in one XHTML file. Without usable navigation, it falls back to spine documents and shows a warning. Encrypted/DRM books are not supported.

Adaptations may omit the title page, acknowledgments, prologue, or other sections. An unmatched section is not proof of an omission: listen/check, then choose **Mark not present in recording**. **Reconsider section** reverses that decision. These decisions survive project restarts and are included in review JSON; they do not remove audio or chapter markers.

The matcher also suggests **Intro** and **Outro / credits** when it finds production and cast announcements outside the matched book chapters. These are labelled **Audio-only suggestions**, always require review, and are never inserted automatically. Intro reuses the required zero-time marker. Closing-credit suggestions start at spoken evidence; adjust earlier if credits music begins first. Quiet music-only intros/outros and unrecognized announcements still need manual markers. These heuristics currently recognize English production-credit phrases.

**GraphicAudio and adaptations:** music, overlapping voices, omitted narration, rearranged scenes, and changed wording can reduce matches. The matcher searches the first 2,400 words of each section using shared phrases, tolerates gaps, and enforces increasing chapter order. Confidence labels describe evidence, not calibrated probabilities. A later passage is explicitly flagged with its word offset; it is a navigation clue and may be well after the true chapter start. Unmatched sections remain visible for manual review. Even a strong match can miss a spoken heading or introductory music—listen and adjust before accepting.

### Saved projects and resume

Projects, transcripts, EPUB text, and uploaded audio are kept in `.chapterise-data/` beside the app (excluded from Git). Use `--workspace /path/to/projects` to choose another location. Starting without a file reopens the last project; the **Saved project** menu switches between recordings. CLI source files stay in their original location and must remain available. Uploads make a local copy, so allow enough disk space for both the source and export (limits: 32 GB audio / 100 MB EPUB).

Chapter edits save automatically after a short delay; check the save status before closing. Export approval is never saved. **Save review JSON** remains available as a portable backup. Projects use the source path, size, modification time, and inode to detect changed source files.

Speech recognition saves each completed five-minute chunk. **Pause processing** stops transcription/matching at its next safe point; the current incomplete chunk may need to run again. Resume with the same operation, model, and language. Closing the server also retains completed chunks. Changing model/language creates a separate cache. Scanning and export cannot be paused; let export finish before stopping the server. No audio or book text is sent to a cloud service; the initial speech model download needs internet access.

## Run

Requires **Python 3.10+** and **FFmpeg** (`ffmpeg` and `ffprobe` on your PATH). Pause detection and chapter editing need no Python packages. Optional transcription setup is below. Run the following commands from the Chapterise repository directory.

```bash
python3 app.py "/path/to/book.m4a"
```

Open the `http://127.0.0.1:...` URL printed in the terminal. Keep the terminal running while reviewing. Stop with Ctrl+C after export finishes.

1. Click **Scan for pauses**. Defaults: at least 2 seconds below −35 dB, with suggested markers at least 60 seconds apart.
2. **Preview** each candidate from its exact current marker timestamp for the next 10 seconds (or until the audio ends). Use its slider to adjust up to **±15 seconds from the original position**, with millisecond steps using the arrow keys. The timestamp and offset update as you drag; preview and export use the adjusted time. Sliders stop at the audio boundaries and the opening chapter stays at zero. You can also check or uncheck markers, type timestamps, edit titles, or manually add missing chapters (see below). Longer pauses get selection priority; candidates too close to other selected markers are still listed unchecked. Leading and trailing silence are ignored.
3. Check the approval box, then **Export approved chapters**. Editing a marker clears approval.

To add a missing chapter, use **Add a missing chapter** in the review section:

- Click **Insert after…** beside an existing marker to seek to the midpoint of the gap after it and fill the new chapter time. This only prepares a position; it does not add a marker yet.
- Listen and seek using the audio player, then click **Use playback position** to capture the exact spot. Alternatively, type seconds or `HH:MM:SS.mmm` directly. **Preview this time** starts at that exact time and plays the next 10 seconds without changing your entered time.
- Optionally enter a title, then click **Add chapter break**. It is inserted in chronological order, highlighted, and selected for export. It gets the same ±15-second adjustment slider as detected markers. Uncheck it to exclude it.

Manual chapters work before or after scanning and are included in saved reviews and approved exports. Adding one clears approval. A new scan replaces pause suggestions while preserving manual, EPUB, and existing chapter markers.

**Remove** deletes a marker from the review. Its audio becomes part of the preceding chapter in the approved export; no audio is deleted. **Undo removal** restores the last deleted marker, including its title and adjustment. Removal and undo clear export approval. The opening chapter at zero is required and cannot be removed. Rescanning restores detected markers; save your review to retain removals.

The default output is `book.chaptered.m4a`, beside the original. An existing output is never overwritten. To choose another output, including M4B:

```bash
python3 app.py "/path/to/book.m4a" --output "/path/to/book.chaptered.m4b"
```

Use `--port 8765` if you want a fixed port. The server only binds to this computer's loopback address; use the exact URL it prints.

**Save review JSON** downloads your current choices, including each slider's original position, so you can restore them with **Load review JSON**. Older review files use their saved timestamps as the slider origins. The automatic local project save also preserves chapter edits across refreshes. Imported review JSON is checked against filename and duration, not a full audio fingerprint; load it only for the original recording. An export always requires approval, including after loading a review.

## Transcription setup

Install the optional [faster-whisper](https://github.com/SYSTRAN/faster-whisper) speech recognizer in a virtual environment (Python 3.11–3.14 recommended for available dependency wheels):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-transcription.txt
.venv/bin/python app.py "/path/to/book.m4a"
```

On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.

Click **Generate searchable transcript**. Choose **Tiny** for speed, **Base** for a balance of speed and accuracy, or **Small** for better recognition at higher CPU cost. Language detection is automatic; specify a language code such as `en` if it chooses the wrong language. First use downloads the chosen model from Hugging Face to its normal local cache; subsequent runs can use the cached model. Audio is processed locally and is not uploaded. No API key or paid service is required.

- Speech overlapping each marker's 10-second preview window appears under its title. Adjusting the marker refreshes the displayed text. Segment timestamps are approximate, so displayed sentences can extend beyond the preview window.
- Search finds literal, case-insensitive words or phrases within transcript passages throughout the recording. Select a result to preview 10 seconds from its exact timestamp and fill the manual chapter time, then add a missing break if appropriate. The first 100 matching passages are shown; narrow the query for more specific results.
- Transcription preserves your current chapter edits and does not automatically add or rename chapters. It runs as a background job; scanning/export and chapter edits wait until it finishes.
- **Save review JSON** includes the transcript. **Load review JSON** restores it without requiring the speech package or another transcription run (maximum review file size: 100 MB). Transcript text is for review/search and EPUB matching; it is not embedded in the exported audio metadata.
- Recognition runs on CPU in five-minute chunks to bound audio memory use. Long books can take a while; phrases crossing chunk boundaries may be incomplete. Silence filtering reduces spurious text but recognition can still omit or invent words. Listen before deciding on a break.

## Detection and file handling

Pause detection uses FFmpeg's existing [silencedetect filter](https://ffmpeg.org/ffmpeg-filters.html#silencedetect) and [chapter metadata format](https://ffmpeg.org/ffmpeg-formats.html#Metadata). It is independent of EPUB matching and needs no API key or model download. It is most useful for narrated recordings with distinct pauses. Music beds, room noise, and ordinary dramatic pauses can cause missed or extra candidates; listening and approval are the final decision.

- Increase the minimum pause or suggested spacing if there are too many candidates. Increase the threshold towards zero (for example, −30 dB) if background noise prevents pause detection.
- Timestamps land in the middle of pauses and can be edited to millisecond precision. Selection spacing is a suggestion, not an export restriction.
- Existing chapters are included initially. The final selected list replaces chapter metadata in the output. Blank titles become `Chapter 1`, `Chapter 2`, etc.
- All audio streams and attached cover images are copied without re-encoding. Detection and browser preview use the first audio stream. FFmpeg copies supported global tags; arbitrary vendor-specific MP4 atoms and non-audio/non-cover tracks are not guaranteed to survive remuxing. Keep the original.
- Export uses an explicit millisecond movie/chapter timescale to avoid timestamp overflow in long audiobook chapters with FFmpeg's automatic timescale. It verifies chapter count, starts, ends, and titles before publishing the new file; verification errors identify the mismatched chapter and field. Temporary space of up to twice the source file size may be needed in the destination directory.
- Playback depends on the browser's support for the source audio codec. Chapter display depends on your audio player; the browser player here is for previewing the source. M4B may be more convenient for audiobook players.
- Scanning decodes the recording once and runs in the background; long books can take a while. Let scanning/export finish before stopping the app.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests create synthetic audio and EPUB files in temporary directories and exercise detection, approved metadata export, preservation of encoded audio and common tags, validation, uploads, project restoration, EPUB navigation, matching ambiguity/omissions, and transcription checkpoint reuse/errors. They do not download speech models.

To also run the browser integration test, point `CHAPTERISE_TEST_BROWSER` at a Chromium-based browser:

```bash
CHAPTERISE_TEST_BROWSER=/path/to/chromium python3 -m unittest discover -s tests -v
```

That test uses fixed speech output to check search, preview, manual insertion, sliders, removal/undo, saved reviews, export, uploads, EPUB proposal acceptance, and refresh restoration. Real speech recognition is tested separately with local recordings.
