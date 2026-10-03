# Changelog

## v1.5.0-beta.2 — 2026-10-03 (matching test release)

- Recognise expected spoken chapter and part numbers immediately before opening prose, using word timestamps and chapter-order constraints.
- Require a quiet lead-in for sparse source-track adjustments; retain dense source chapter maps only when many independent strong opening matches corroborate them.
- Retain original text-evidence timestamps and confidence; flag unmatched opening words for boundary review.
- Preserve saved review markers and section identifiers; rerun matching to generate updated suggestions using the existing transcript.

Validation: all 44 regression tests passed, including browser workflows. Six local recording comparisons lost no matches. Well of Ascension improved 57 boundaries, with median absolute error across 66 reviewed sections falling from 3.75 to 0.66 seconds. Successful earlier source-map boundaries were preserved; no compared saved review boundaries worsened.

Upgrade: pull `v1.5.0-beta.2` or `ui-test` and redeploy the test stack with the same test volumes. Rerun **Find EPUB chapters** to reuse the saved transcript and generate updated suggestions. Accepted markers remain unchanged; save a review JSON backup before reconsidering decisions. Stable `latest` remains on v1.4.

## v1.5.0-beta.1 — 2026-10-03 (UI test release)

- Replace the long configuration page with workspace navigation for projects, transcription queue, detection, transcript search, review and export.
- Add a compact project header and persistent bottom audio player; retain exact ten-second previews.
- Collapse help, pause settings and manual chapter insertion until needed.
- Stack chapter controls on mobile without horizontal table scrolling.
- Publish the test branch as `ui-test` and beta tags separately; stable `latest` remains unchanged.

Try `compose.test.yaml` on port 8766 with separate project/model volumes, or deploy `ghcr.io/chkn-11/chapterise:v1.5.0-beta.1`.

## v1.4 — 2026-10-02

- Add a persistent serial transcription queue with multiple audiobook uploads, per-task model/language settings, and optional delayed starts.
- Optionally match an attached EPUB after queued transcription.
- Add queue pause/resume, priority changes, retries, removal, and restart recovery from completed chunks.
- Allow reviewing and switching projects during queued transcription while preserving saved chapter edits and omission decisions.

Upgrade: pull `ghcr.io/chkn-11/chapterise:v1.4` or `latest` and redeploy with the same data/model volumes. Keep the container running for scheduled tasks.

Validation: all 38 regression tests passed, including browser batch uploads, queue controls, transcript loading, scheduling, pause/restart recovery, and preservation of manual review decisions.

## v1.3.1 — 2026-10-02

- Constrain PyAV to versions below 19 to fix `open() got an unexpected keyword argument metadata_errors` during speech decoding.
- Add a real faster-whisper audio-decoding check to container CI for both default and custom users, without downloading a model.

Upgrade: pull `ghcr.io/chkn-11/chapterise:v1.3.1` or `latest` and redeploy with the same volumes. Retry transcription or EPUB matching; existing completed transcription chunks can be reused. No audiobook metadata edits or re-upload are required. For direct Python installations, rerun pip installation from `requirements-transcription.txt` and restart the app.

## v1.3 — 2026-10-02

- Retain illustration-only EPUB entries and search for distinctive spoken titles as review candidates.
- Use nearby earlier embedded chapter boundaries for opening-text matches, with explicit review warnings; accepting a suggestion reuses the corresponding unlinked source marker.

- Include short leading EPUB block quotations and attributions in their own chapter when the document has one contents target. Preserve shared-document anchors and skip ordinary preceding prose or earlier headed sections.
- Verify unchanged parsing for five previously used audiobook EPUBs, and add EPUB2/EPUB3 epigraph and boundary regression tests.

Upgrade: pull `ghcr.io/chkn-11/chapterise:v1.3` (or `latest`) and redeploy with the same volumes. Save a review JSON backup, re-upload the EPUB, then rerun **Find EPUB chapters** using the existing transcript. Existing saved decisions remain intact; repaired section boundaries can change proposal IDs, so check for duplicates before accepting suggestions.

Validation: 34 regression tests passed, including browser interactions, source-marker reuse, image-only sections, epigraph recovery, and shared-document boundaries. Fixed a browser-test reload synchronization race.

Known limitations: direct detection of spoken numerical headings before matched prose and flexible map-title prefixes are not included in this release. Headings absent from the transcript, some narrated illustrations, and later-passage matches still need manual review. Source-boundary adjustments are suggestions, not verified chapter starts.

## v1.2 — 2026-09-28

- Add a separate rapid-review window after EPUB matching, with 250 ms / Shift-5-second boundary adjustments, exact ten-second previews, Enter to save and advance, editable names, and visible match evidence.
- Include unmatched entries and audio-only suggestions; support not-present decisions, manual audio-only markers, revisiting accepted markers, and immediate project saving with retry feedback.
- Keep the main editor paused while rapid review is open. Export still requires separate approval.
- Allow not-present/reconsider decisions for matched proposals and audio-only suggestions in the main review as well.

Upgrade: pull `ghcr.io/chkn-11/chapterise:v1.2` (or `latest`) and redeploy with the same volumes. Open a saved project with EPUB matching results, then choose **Open rapid review ↗**. Existing results and transcripts can be reused; keep the main window open.

Validation: browser regression tests cover keyboard increments, edited titles, omission/reconsideration, audio-only markers, failed-save retries, duplicate prevention, and refresh persistence.

## v1.1 — 2026-09-27

### EPUB matching and GraphicAudio recordings

- Recover missing EPUB contents anchors when the link identifies a single, unambiguous document. Show a parsing warning rather than silently dropping the chapter. Ambiguous links in shared documents remain skipped.
- Suggest audio-only production blocks between book chapters, including part credits, advertisements, and additional intros. Group nearby announcements into one reviewable suggestion with a distinct ID.
- Require nearby production and cast evidence for internal suggestions, and suppress announcement text already present in the EPUB. All audio-only suggestions still require review.
- Update the alignment engine identifier to `phrase-alignment-v3`.

### Docker and documentation

- Document bind-mount ownership and custom numeric users through Compose's `user: "UID:GID"`; include `compose.bind.yaml`. The default remains `10001:10001`.
- Check uploads and model-cache writes under a custom non-root UID in container CI.
- Update deployment examples to `ghcr.io/chkn-11/chapterise:v1.1`. Release builds publish the same image as the version tag and `latest`.

### Upgrade an existing project

1. Save a review JSON backup and finish active processing before updating the container. Keep the same data/model mounts.
2. Pull `v1.1` (or `latest`) and redeploy. In Portainer, enable image pulling when updating the stack.
3. Open the saved project and re-upload its EPUB so the repaired parser reads it again.
4. Run **Find EPUB chapters**. The saved transcript is reused; retranscription is not required.
5. Review newly recovered chapters and internal audio-only suggestions before accepting them. Repaired section boundaries can change proposal IDs, so existing markers may no longer appear as already accepted. Saved marker times and titles remain intact; check for duplicates.

### Validation and limits

- All 28 local tests passed, including parser recovery, ambiguous-link handling, grouped internal production blocks, false-positive rejection, and the browser workflow.
- A local comparison against a reviewed multi-part GraphicAudio recording recovered three missing chapter entries within approximately four seconds of their reviewed timestamps, and found all five manually marked internal production blocks. Existing suggestions for shared EPUB entries remained unchanged.
- These results do not establish accuracy for every book. Later-passage matches can still be substantially after the actual chapter opening; music-only boundaries and unrecognized announcements need manual placement.

## v1

Initial versioned container release: pause detection, manual chapter editing and preview, searchable local speech recognition, EPUB alignment, opening/closing production suggestions, saved reviews/projects, and approved metadata export. Public Linux AMD64 image with the web UI on port 8765.
