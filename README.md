# Chapterise

A local browser tool for finding potential chapters in M4A/M4B audio, listening to the breaks, and writing approved chapter metadata into a new file.

## Run

Requires **Python 3.10+** and **FFmpeg** (`ffmpeg` and `ffprobe` on your PATH). There are no Python packages to install. Run the following commands from the Chapterise repository directory.

```bash
python3 app.py "/path/to/book.m4a"
```

Open the `http://127.0.0.1:...` URL printed in the terminal. Keep the terminal running while reviewing. Stop with Ctrl+C after export finishes.

1. Click **Scan for pauses**. Defaults: at least 2 seconds below −35 dB, with suggested markers at least 60 seconds apart.
2. **Preview** each candidate to hear 8 seconds before and after its current marker. Use its slider to adjust up to **±15 seconds from the original position**, with millisecond steps using the arrow keys. The timestamp and offset update as you drag; preview and export use the adjusted time. Sliders stop at the audio boundaries and the opening chapter stays at zero. You can also check or uncheck markers, type timestamps, edit titles, or manually add missing chapters (see below). Longer pauses get selection priority; candidates too close to other selected markers are still listed unchecked. Leading and trailing silence are ignored.
3. Check the approval box, then **Export approved chapters**. Editing a marker clears approval.

To add a missing chapter, use **Add a missing chapter** in the review section:

- Click **Insert after…** beside an existing marker to seek to the midpoint of the gap after it and fill the new chapter time. This only prepares a position; it does not add a marker yet.
- Listen and seek using the audio player, then click **Use playback position** to capture the exact spot. Alternatively, type seconds or `HH:MM:SS.mmm` directly. **Preview this time** plays 8 seconds either side without changing your entered time.
- Optionally enter a title, then click **Add chapter break**. It is inserted in chronological order, highlighted, and selected for export. It gets the same ±15-second adjustment slider as detected markers. Uncheck it to exclude it.

Manual chapters work before or after scanning and are included in saved reviews and approved exports. Adding one clears approval. A new scan replaces the current review, including manual chapters, so save your review before rescanning if you want to keep it.

The default output is `book.chaptered.m4a`, beside the original. An existing output is never overwritten. To choose another output, including M4B:

```bash
python3 app.py "/path/to/book.m4a" --output "/path/to/book.chaptered.m4b"
```

Use `--port 8765` if you want a fixed port. The server only binds to this computer's loopback address; use the exact URL it prints.

**Save review JSON** downloads your current choices, including each slider's original position, so you can close the app and later restore them with **Load review JSON**. Older review files use their saved timestamps as the slider origins. Unsaved edits are lost on refresh or rescan. Reviews are checked against filename and duration, not a full audio fingerprint; load them only for the original recording. An export always requires approval, including after loading a review.

## Detection and file handling

This uses FFmpeg's existing [silencedetect filter](https://ffmpeg.org/ffmpeg-filters.html#silencedetect) and [chapter metadata format](https://ffmpeg.org/ffmpeg-formats.html#Metadata). It is a pause-based heuristic, not a semantic AI classifier. No audio is sent to a service, and no API key or model download is needed. It is most useful for narrated recordings with distinct pauses. Music beds, room noise, and ordinary dramatic pauses can cause missed or extra candidates; listening and approval are the final decision.

- Increase the minimum pause or suggested spacing if there are too many candidates. Increase the threshold towards zero (for example, −30 dB) if background noise prevents pause detection.
- Timestamps land in the middle of pauses and can be edited to millisecond precision. Selection spacing is a suggestion, not an export restriction.
- Existing chapters are included initially. The final selected list replaces chapter metadata in the output. Blank titles become `Chapter 1`, `Chapter 2`, etc.
- All audio streams and attached cover images are copied without re-encoding. Detection and browser preview use the first audio stream. FFmpeg copies supported global tags; arbitrary vendor-specific MP4 atoms and non-audio/non-cover tracks are not guaranteed to survive remuxing. Keep the original.
- Export verifies chapter count, starts, and titles before publishing the new file. Temporary space of up to twice the source file size may be needed in the destination directory.
- Playback depends on the browser's support for the source audio codec. Chapter display depends on your audio player; the browser player here is for previewing the source. M4B may be more convenient for audiobook players.
- Scanning decodes the recording once and runs in the background; long books can take a while. No cancellation UI is provided. Let export finish before stopping the app.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests create short synthetic audio files in a temporary directory and exercise detection, approved metadata export, preservation of encoded audio and common tags, validation, and the local HTTP approval boundary.
