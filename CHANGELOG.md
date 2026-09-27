# Changelog

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
