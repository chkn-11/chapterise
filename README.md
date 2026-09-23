# Chapterise

Turn an M4A or M4B audiobook into a chaptered file using pause detection, a matching EPUB, or manually placed markers. Listen, adjust, and approve the result in your browser before exporting.

> [!WARNING]
> **This project was vibe coded with substantial AI assistance.** It has automated tests, but bugs, incorrect transcripts, and inaccurate chapter suggestions are still possible. Keep your original files and backups, listen to the proposed boundaries, and check the exported file in your audiobook player. Treat the results as suggestions, not an authoritative chapter map.

Chapterise runs on your computer or Docker server. Audio and EPUB text are processed there rather than sent to a cloud AI service. The first transcription downloads a speech model; no account, API key, or paid AI service is required.

## TL;DR — what each feature does

| Feature | What it does |
| --- | --- |
| [Uploads and projects](#1-open-or-upload-an-audiobook) | Open an M4A/M4B, optionally add its EPUB, and return to saved work later. |
| [Pause detection](#2-find-chapters-from-pauses) | Suggest chapter breaks where the audio becomes quiet. No speech model needed. |
| [Speech-to-text](#3-generate-and-search-a-transcript) | Transcribe the recording locally and show nearby speech beside each marker. |
| [Transcript search](#3-generate-and-search-a-transcript) | Find a word or phrase, preview it, and use its position for a manual chapter. |
| [EPUB matching](#4-find-chapters-from-an-epub) | Match chapter text against recognized speech and propose boundaries with evidence. |
| [Omitted sections](#5-handle-omitted-epub-sections) | Mark an unmatched prologue, title page, or other section as absent from this recording. |
| [Intro and credits](#6-review-audio-only-intros-and-outros) | Suggest audio-only sections when production/cast announcements are recognized. |
| [Preview and precision](#7-preview-adjust-rename-and-select-markers) | Play ten seconds from the marker; adjust with a ±15-second slider or typed timestamp. |
| [Manual chapters](#8-add-a-missing-chapter) | Place a missing break anywhere between the beginning and end of the recording. |
| [Remove and undo](#9-remove-or-restore-a-marker) | Remove a chapter boundary without deleting any audio; undo the last removal. |
| [Saved reviews](#10-save-back-up-and-restore-your-review) | Autosave the project and download/import review JSON containing markers and transcripts. |
| [Pause and resume](#11-pause-and-resume-long-processing) | Reuse completed transcription chunks after pausing or restarting. |
| [Approved export](#12-approve-export-and-download) | Write selected chapters into a new file, verify them, then download the result. |

**Fastest route with an EPUB:** upload audio → upload EPUB → **Find EPUB chapters** → preview/accept/adjust → approve → export.

**Without an EPUB:** upload audio → **Scan for pauses**, transcribe, or add markers manually → review → approve → export.

## Install with Docker or Portainer

Published image: **`ghcr.io/chkn-11/chapterise:v1`**

The public image supports **Linux x86-64 (Intel/AMD)** and includes Python, FFmpeg, and CPU speech recognition. No registry login or host Python installation is needed. ARM and GPU images are not provided in this release.

### Sample Compose file

Everything needed for deployment is in this file. No `.env`, server-IP setting, token URL, or log inspection is required.

```yaml
services:
  chapterise:
    image: ghcr.io/chkn-11/chapterise:v1
    init: true
    restart: unless-stopped
    ports:
      - "8765:8765"
    volumes:
      - projects:/data
      - models:/models

volumes:
  projects:
  models:
```

**Portainer (Docker Standalone):**

1. Open **Stacks → Add stack → Web editor**.
2. Name the stack `chapterise` and paste the YAML above, or use [compose.portainer.yaml](compose.portainer.yaml).
3. Select **Deploy the stack**.
4. Open **`http://YOUR-SERVER:8765/`** in your browser. On the same computer, use **`http://localhost:8765/`**.

**Docker Compose:**

1. Save the example as `compose.yaml` in its own directory. Alternatively, use [compose.published.yaml](compose.published.yaml) with `-f compose.published.yaml` in your commands.
2. Run:

   ```bash
   docker compose pull
   docker compose up -d
   ```

3. Open `http://YOUR-SERVER:8765/` or `http://localhost:8765/`.

Port 8765 is the app's default. If it is occupied on your host, change only the mapping to `"8877:8765"` and browse to port 8877. The app automatically uses the host/port from the browser request.

### Versions, updates, and persistent storage

| Image tag | Purpose |
| --- | --- |
| `v1` | This release; ordinary pushes to `main` do not advance this tag. |
| `latest` | The newest successfully published build from `main`. |
| `sha-<full-commit-id>` | A build associated with a particular source commit. |

For an exact image artifact, use its registry digest (`image@sha256:...`).

To update, change the image tag if desired, then pull and redeploy after active exports finish. In Portainer, enable the option to pull the image when updating the stack. Keep the existing stack name and volumes. If migrating from an early setup, remove its `CHAPTERISE_PUBLIC_ORIGIN` environment entry unless you deliberately want to restrict access to that one address.

- `projects` is mounted at `/data`: uploads, reviews, EPUB text, transcripts, completed transcription chunks, and exported audio.
- `models` is mounted at `/models`: downloaded speech models, reused between runs.
- Stopping/recreating the container preserves these volumes. `docker compose down` keeps them; **`docker compose down --volumes` deletes them and their contents**.
- Back up both the project volume and your original files. A review JSON backup does not contain the audio or EPUB itself.

> [!IMPORTANT]
> The web UI has no login. Anyone who can reach the published port can use the app and access its projects. Use a trusted LAN/VPN, or put an authenticated reverse proxy in front of it. The Compose example publishes the port on the Docker host's interfaces.

A reverse proxy should serve the app at the domain root, preserve the browser's `Host` header, and allow large uploads. HTTP/HTTPS requests with matching host and port are supported without special configuration; unrelated browser origins are rejected for writes. Forwarded headers do not override the checks. An explicit `--public-origin` restriction remains available for advanced deployments.

## Detailed usage

### 1. Open or upload an audiobook

**What it does:** creates a project for a recording and loads any existing chapter metadata into the review.

1. Under **Open a chaptering project**, choose **Audiobook (.m4b / .m4a)**.
2. Wait for the upload to complete. The file is copied to the machine running Chapterise; for Docker, that is your Docker host, not necessarily your browser's computer.
3. Check the filename and duration in the player.
4. If you want EPUB matching, choose the corresponding file under **Matching book (.epub)**. Pause detection, transcription, and manual editing work without an EPUB.
5. To resume an existing project, select it under **Saved project** and click **Open saved project**. Avoid uploading another copy merely to reopen existing work.

Uploads allow up to 32 GB for audio and 100 MB for an EPUB. Allow disk space for the uploaded source, processing data, and exported output. Existing chapter markers are included; an opening marker at zero is inserted when needed. That opening marker is required and cannot be removed.

Use the EPUB for the same book and edition where possible. The app does not remove DRM or decrypt protected ebooks.

### 2. Find chapters from pauses

**What it does:** uses FFmpeg's silence detection to suggest markers in the middle of quiet gaps. It does not understand whether a gap is a real chapter break.

1. In **Find possible breaks**, set the detection controls:

   | Control | Default | Effect |
   | --- | --- | --- |
   | Minimum pause | 2 seconds | Quiet gaps shorter than this are ignored. Increase it to reduce suggestions. |
   | Silence threshold | −35 dB | Audio below this level counts as quiet. Moving towards zero detects more gaps, but can also treat quiet speech as silence. |
   | Suggested chapter spacing | 60 seconds | Controls which suggestions are initially selected. Closer candidates can remain listed unchecked. |

2. Click **Scan for pauses** and wait for the scan to finish.
3. Review the resulting markers, listen to their previews, and keep or move the useful ones.
4. Uncheck or remove false positives. You can add missing breaks manually.

Leading and trailing silence are ignored. Longer pauses receive selection priority where suggestions conflict. Chapter spacing is a selection aid, not a restriction on your final export.

A rescan replaces pause suggestions, including their previous adjustments, while preserving manual, EPUB, and existing-file markers. Save a review JSON before rescanning if you want a backup of your current decisions. Music beds, effects, and dialogue pauses make silence detection less reliable for dramatized audiobooks.

### 3. Generate and search a transcript

**What it does:** turns speech into timestamped text using local [faster-whisper](https://github.com/SYSTRAN/faster-whisper), supplies text beside markers, and enables literal text search.

1. Under **Search the spoken audio**, choose a model:

   | Model | Tradeoff |
   | --- | --- |
   | Tiny | Fastest, with lower recognition accuracy. |
   | Base | Default balance of speed and accuracy. |
   | Small | More demanding on CPU/memory, with potentially better recognition. |

2. Leave **Language code** blank for automatic detection, or enter a code such as `en` for English.
3. Click **Generate searchable transcript**. First use downloads the selected model; later runs reuse the model cache.
4. When complete, read the recognized speech under marker titles or type a word/phrase into **Search transcript**.
5. Click a search result to preview ten seconds from that passage's timestamp and fill the manual chapter time.
6. If you found a missing boundary, adjust that time and use **Add chapter break**.

Search is case-insensitive, literal, and limited to text within individual transcript passages. It is not a semantic search engine. The UI shows the first 100 matching passages; narrow your query if there are more.

Displayed sentences can extend beyond the ten-second preview because they use passage boundaries. Recognition can omit or invent words, especially with music or overlapping voices. Transcription does not add, remove, or rename chapter markers. Editing/scanning/export wait while the background job runs.

Docker includes transcription support. For a direct Python installation, see [running without Docker](#run-without-docker).

### 4. Find chapters from an EPUB

**What it does:** reads the ebook's chapter structure and looks for corresponding phrases in the audiobook transcript, keeping proposed matches in book order.

1. Upload/open the audiobook, then upload its matching EPUB.
2. Check the displayed book title, number of contents entries, and any parsing warnings.
3. Choose the speech model/language under **Search the spoken audio**.
4. Click **Find EPUB chapters**. If there is no transcript, the app generates one first. If a transcript already exists, it is reused.
5. For each proposal, compare **Book passage** with **Recognized speech**, read its reason, and click **Preview match**.
6. Click **Use this marker** when it is useful. The proposal becomes a selected marker in **Review your chapters**, where you can refine its time/title.
7. Use **Locate manually** when the proposed time is uncertain or no match was found. This fills the manual editor; it does not insert a marker until you choose **Add chapter break**.

| Label | Meaning |
| --- | --- |
| Strong opening match | Good text evidence near the chapter opening, with word timing. Still listen before accepting. |
| Needs review | Partial, ambiguous, later-passage, or coarse-timing evidence. The actual start may be earlier. |
| Unmatched | No usable ordered match was found. The section may be missing from the audio, or matching may have failed. |

Confidence labels describe evidence, not calibrated probabilities. No proposals are automatically accepted or exported. A marker already at the exact proposed time must be handled in the review rather than duplicated. An Intro proposal at zero reuses the required opening marker.

To change the model/language of an existing transcript, run **Generate searchable transcript** with the new settings before matching again. Rerunning matching does not overwrite your accepted chapter edits.

**How it works:** EPUB 2 NCX and EPUB 3 navigation are supported, including chapter anchors within the same XHTML file. If there is no usable contents list, the parser falls back to one section per reading-order document and warns you. The matcher searches up to the first 2,400 words of each section, tolerates missing text, and enforces increasing chapter order. This is AI speech recognition plus phrase alignment, not an autonomous LLM agent.

**GraphicAudio and adaptations:** narration, prologues, or entire scenes may be cut or rearranged. A clear match hundreds of words into a chapter is a navigation clue, not its verified opening. Even a strong opening-text match may come after a spoken chapter heading or introductory music.

### 5. Handle omitted EPUB sections

**What it does:** records that an unmatched ebook section is not part of this particular audio adaptation.

1. Review an **Unmatched** proposal, such as a title page, acknowledgments, or prologue.
2. Check the audio before assuming the section was omitted; search/recognition can miss real content.
3. If it is absent, click **Mark not present in recording**.
4. To revisit that decision, click **Reconsider section**.

The decision is saved in the project and review JSON. It does not delete audio, remove existing chapter markers, or force later sections to match. The app does not automatically declare every unmatched section omitted.

### 6. Review audio-only intros and outros

**What it does:** suggests **Intro** and **Outro / credits** markers when it detects production/cast announcements outside the matched book chapters.

1. Run **Find EPUB chapters**.
2. Look for proposals labelled **Audio-only suggestion**.
3. Preview the recognized announcements and listen around the proposed time using the player.
4. Choose **Use this marker**, then adjust its position/title in the review if needed. Alternatively, use **Locate manually**.

These suggestions always need review. Intro uses the zero-time marker. Closing credits are suggested at spoken evidence; credits music may start several seconds earlier. Music-only sections or unrecognized announcements need manual markers. The production-announcement heuristics currently recognize English phrases and depend on usable book matches to locate the recording's edges.

### 7. Preview, adjust, rename, and select markers

**What it does:** lets you refine the proposed boundaries before writing metadata.

1. In **Review your chapters**, click **Preview** beside a marker. Playback starts at that exact marker timestamp and continues for ten seconds, or until the file ends. It does not start five seconds before the marker.
2. Move its slider up to **15 seconds earlier or later than its original position**. The arrow keys make millisecond adjustments.
3. For a larger change, type a start time as seconds or `HH:MM:SS.mmm`, then leave the field to apply it. The slider remains anchored to the original position; it does not reset after a typed edit.
4. Edit the chapter title. Blank titles receive automatic `Chapter 1`, `Chapter 2`, etc. names at export.
5. Use **Keep** to include/exclude a marker. **Select all** includes every marker; **Clear optional markers** keeps only the required opening marker selected.
6. Preview again after adjusting and repeat until satisfied.

Markers are displayed in time order. Times must be distinct and within the recording. The opening marker stays selected at zero. Any edit clears export approval, so review the final selection before approving again.

### 8. Add a missing chapter

**What it does:** inserts a new boundary at a manually chosen position, including between automatically discovered chapters.

1. Click **Insert after…** beside a marker to start at the midpoint of the gap after it. This seeks the player and fills **New chapter start**; it does not create a marker yet.
2. Listen and seek to the desired boundary.
3. Click **Use playback position**, or type a time directly into **New chapter start**.
4. Optionally fill **Chapter title**.
5. Click **Preview this time** to check the next ten seconds.
6. Click **Add chapter break**.

The new marker is inserted in chronological order, highlighted, selected, and given the same adjustment slider as other markers. You can also start directly from the manual editor or a transcript search result. Duplicate times, zero, and the exact end of the recording are rejected for new markers.

### 9. Remove or restore a marker

**What it does:** removes a chapter boundary from the review without changing the audio.

1. Click **Remove** beside an unwanted marker.
2. Its audio will belong to the preceding selected chapter when you export.
3. Click **Undo removal** if you want the last removed marker back, including its title and adjustment.

Undo is a single-removal convenience, not a full history, and does not survive a page reload. A marker cannot be restored if another marker now occupies the same time. Use **Keep** instead of **Remove** when you want to retain a marker for later without exporting it. The opening marker cannot be removed.

### 10. Save, back up, and restore your review

**What it does:** preserves work locally and lets you move review decisions between installations.

**Automatic project save:**

1. Make your edits and wait for the save status to confirm they were saved.
2. Leave the source file/project storage in place.
3. Reopen the app; it attempts to restore the last project. Use **Saved project → Open saved project** to switch recordings.

**Portable review JSON:**

1. Click **Save review JSON** to download your review.
2. Keep the original audiobook and, for matching, its EPUB separately.
3. On the destination installation, upload/open the same recording.
4. Click **Load review JSON** and select the saved file.
5. Review the imported markers. Upload the matching EPUB and run **Find EPUB chapters** if you need new proposals.

Review JSON includes marker titles/times/selections, original slider positions, the transcript when present, and omitted-section decisions. It does not include the audio, the EPUB, or the whole project database. The maximum imported review size is 100 MB.

Imports are checked against the recording's filename and duration, not a full content hash. Use them only with the same recording. Loading JSON can restore transcripts without another speech-recognition run. Export approval is never saved and must be given again.

Projects use absolute source paths and file fingerprints. Copying a desktop `.chapterise-data` directory into Docker is not a portable migration; use original files plus review JSON instead. There is one active project per running app instance, so coordinate use if multiple browsers are connected.

### 11. Pause and resume long processing

**What it does:** saves each completed five-minute transcription chunk so long recordings do not have to restart from zero.

1. During transcription or EPUB matching, click **Pause processing**.
2. Wait for processing to stop at a safe point. The current incomplete chunk may need to be repeated.
3. To continue, choose the same model/language and click **Generate searchable transcript** or **Find EPUB chapters** again.
4. After a server/container restart, reopen the saved project and start the operation again; jobs do not resume automatically.

Changing the source, model, language, or transcription options uses a separate cache. A completed transcript is reused by matching. During the matching stage, pausing saves no partial match list; rerun matching using the saved transcript. Scan/export cannot be paused. Let exports finish before stopping the app.

### 12. Approve, export, and download

**What it does:** writes the selected chapter titles and boundaries into a new audio file, then verifies the chapter metadata before publishing it.

1. Check the **Keep** selections, start times, titles, and chapter count.
2. Confirm the destination shown under **Approve & export**.
3. Check **I have reviewed and approve these chapter markers and titles**.
4. Click **Export approved chapters** and wait for completion.
5. Click **Download exported audio** to save the result through your browser.
6. Open it in your audiobook player and check chapter navigation and playback.

The selected markers replace the output file's chapter metadata; the source is left untouched. Each selected marker starts a chapter that continues to the next selected marker, or the file's end. Editing, adding, removing, or loading markers clears approval.

All audio streams and attached cover images are copied without re-encoding. Common global tags are copied, but arbitrary vendor-specific MP4 atoms and non-audio/non-cover tracks are not guaranteed to survive remuxing. Transcript text is not embedded in chapter metadata.

The default filename is `NAME.chaptered.m4a` or `NAME.chaptered.m4b`, beside the source. Uploaded sources and outputs live in project storage; use the download link to retrieve the export. An existing output is never overwritten. For a revised upload-based export, create a fresh project by uploading the original again and load your review JSON. For CLI use, choose a different `--output` path.

Export verifies chapter count, starts, ends, and titles before making the new file available. A failed verification does not publish the output. Temporary free space of up to twice the source size may be needed in the destination directory. Browser preview plays the source; chapter display in the exported file depends on your audiobook player.

## Run without Docker

Basic pause detection and editing require **Python 3.10+** and **FFmpeg**, with `ffmpeg` and `ffprobe` on `PATH`. From a source checkout:

```bash
python3 app.py
```

Open `http://localhost:8765/` and upload a recording, or provide files directly:

```bash
python3 app.py "/path/book.m4b" --epub "/path/book.epub"
```

For speech recognition, create a virtual environment and install the optional dependencies. Python 3.12 matches the published container:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-transcription.txt
.venv/bin/python app.py
```

On Windows, use `.venv\Scripts\python.exe` instead. Speech packages need compatible wheels for your Python/platform; pause/manual features do not require them.

| Option | Purpose |
| --- | --- |
| `file` | Optional source M4A/M4B path; reads it in place instead of uploading a copy. |
| `--epub /path/book.epub` | Add the matching EPUB at startup. |
| `--output /path/new.m4b` | Choose a new output path when providing a source file. |
| `--workspace /path/projects` | Change project/checkpoint storage; default is `.chapterise-data` beside the app. |
| `--port 8877` | Override default port 8765. |
| `--host 0.0.0.0` | Listen for other computers; direct Python runs use loopback by default. |
| `--public-origin https://books.example` | Optional exact browser-origin restriction for an advanced deployment. |

The speech model cache uses the recognition library's normal local cache. Docker uses its separate `/models` volume. Keep the process running during work; stop with Ctrl+C after active exports finish.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| UI unavailable after updating an old stack | Pull/redeploy the intended image; remove an obsolete `CHAPTERISE_PUBLIC_ORIGIN` setting; check the published port/firewall. Open `/`, not an old token URL. |
| Port 8765 is occupied | Change the host mapping to `8877:8765`, or use `--port 8877` for direct Python. |
| Speech recognition unavailable | Docker includes it. For direct Python, install the transcription requirements and start the app with that environment's Python. |
| First transcription appears slow | The model may still be downloading/loading. Long recordings run on CPU; use Tiny for speed or wait for progress. |
| Too many or too few pause suggestions | Adjust minimum pause, threshold, and spacing; music-backed recordings often need EPUB matching or manual review. |
| EPUB section has no match | It may be omitted, differently worded, reordered, or poorly recognized. Search/listen before marking it not present. |
| Good text match, bad chapter start | The match may follow a heading or music, or be from a later passage. Adjust the timestamp or locate the opening manually. |
| Audio will not play in the browser | Browser codec support varies. Use a compatible browser to review; detection/export may still work. |
| Source changed or saved project will not open | Restore the original source location/content or upload it as a new project. Project fingerprints deliberately reject changed files. |
| Export says output exists | Use a new CLI output path or a fresh uploaded project with your saved review. Existing exports are never overwritten. |
| Export fails verification or runs out of space | Read the reported chapter/field error, check markers and free disk space, and keep the source and review backup. |

## Development and release checks

Build a local image with `docker compose up --build -d` using this repository's [compose.yaml](compose.yaml). It uses `chapterise:local`; published deployment files use `ghcr.io/chkn-11/chapterise:v1`.

Run the automated tests:

```bash
python3 -m unittest discover -s tests -v
```

Optional browser integration test (Chromium-based browser):

```bash
CHAPTERISE_TEST_BROWSER=/path/to/chromium python3 -m unittest discover -s tests -v
```

Tests use synthetic audio/EPUB fixtures and mocked speech output, without downloading models. They cover detection, EPUB matching, omissions/credits, review persistence, transcription checkpoints, approved exports, metadata verification, and HTTP access checks. Browser tests cover the user workflow, including root-page entry, uploads, sliders, search, removal/undo, and refresh restoration. These checks do not establish recognition accuracy on every audiobook.

The publishing workflow builds the Linux AMD64 image, runs the tests inside it, and checks the root URL through a remapped Docker port with no environment configuration before pushing. `main` publishes `latest`; a Git tag such as `v1` publishes that release tag. Release tags do not overwrite `latest` merely by being built.
