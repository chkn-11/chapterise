"use strict";
const $ = id => document.getElementById(id);
let rows = [], state, busy = false, previewEnd = null, highlightedRow = null, removedRow = null;
let transcript = null;
let omittedSections = [];
const audio = $("audio");
const PREVIEW_SECONDS = 10;

function timestamp(seconds) {
  const ms = Math.round(seconds * 1000);
  return `${String(Math.floor(ms / 3600000)).padStart(2, "0")}:${String(Math.floor(ms / 60000) % 60).padStart(2, "0")}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}.${String(ms % 1000).padStart(3, "0")}`;
}
function parseTime(text) {
  const pieces = text.trim().split(":");
  if (pieces.length > 3 || pieces.some(p => !/^\d+(\.\d+)?$/.test(p))) throw Error("Use seconds or HH:MM:SS.mmm for start times.");
  if (pieces.length > 1 && pieces.slice(1).some(p => Number(p) >= 60)) throw Error("Minutes and seconds must be less than 60.");
  const value = pieces.reduce((sum, p) => sum * 60 + Number(p), 0);
  if (!Number.isFinite(value) || value < 0 || value >= state.duration) throw Error("A marker must be within the audio duration.");
  return value;
}
function message(text, error = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", error);
}
function invalidate() { $("approved").checked = false; updateControls(); queueSave(); }
async function preview(start) {
  previewEnd = Math.min(state.duration, start + PREVIEW_SECONDS);
  audio.currentTime = start;
  try { await audio.play(); } catch (error) { message(`Playback unavailable: ${error.message}`, true); }
}
function manualMessage(text, error = false) {
  $("manual-status").textContent = text;
  $("manual-status").classList.toggle("error", error);
}
function manualTime() {
  const ms = Math.round(parseTime($("manual-time").value) * 1000);
  if (ms <= 0 || ms >= Math.round(state.duration * 1000)) throw Error("Choose a time after the beginning and before the audio ends.");
  return ms / 1000;
}
function updateControls() {
  const unloaded = !state?.filename;
  $("project-controls").disabled = busy;
  $("epub-upload").disabled = unloaded;
  $("settings").disabled = busy || unloaded;
  $("review").disabled = busy || unloaded;
  $("transcription-settings").disabled = busy || unloaded;
  $("align").disabled = busy || unloaded || !book || (!transcript && !state.transcription_available);
  $("pause-job").hidden = !busy || !state?.job?.cancellable;
  $("approved").disabled = busy;
  $("export").disabled = busy || unloaded || !$("approved").checked;
  $("count").textContent = `${rows.filter(r => r.selected).length} chapters selected`;
  $("undo-remove").hidden = removedRow === null;
}
function updateSnippet(row, element) {
  if (!transcript) {
    element.textContent = "Generate a transcript to see speech near this marker.";
    return;
  }
  const nearby = transcript.segments.filter(s => s.end > row.start && s.start < Math.min(state.duration, row.start + PREVIEW_SECONDS));
  element.textContent = nearby.length ? nearby.map(s => s.text).join(" ") : "No speech recognized near this marker.";
}
function renderTranscript() {
  $("transcript-panel").hidden = transcript === null;
  if (!transcript) return;
  const query = $("transcript-query").value.trim().toLocaleLowerCase();
  const matches = transcript.segments.filter(s => s.text.toLocaleLowerCase().includes(query));
  $("transcript-count").textContent = `${matches.length} ${query ? "matching" : "transcribed"} passages${matches.length > 100 ? "; showing the first 100 — narrow your search for more" : ""}`;
  const fragment = document.createDocumentFragment();
  matches.slice(0, 100).forEach(segment => {
    const result = document.createElement("button"); result.className = "transcript-result";
    const time = document.createElement("span"); time.textContent = timestamp(segment.start);
    const text = document.createElement("span"); text.textContent = segment.text;
    result.append(time, text);
    result.onclick = async () => {
      if (!busy) {
        $("manual-time").value = timestamp(segment.start);
        manualMessage("Transcript position selected. Listen and adjust it, then add a chapter break if needed.");
      }
      await preview(segment.start);
    };
    fragment.append(result);
  });
  $("transcript-results").replaceChildren(fragment);
}
function validateTranscript(value) {
  if (value === undefined || value === null) return null;
  if (typeof value !== "object" || !Array.isArray(value.segments) || value.segments.length > 200000) throw Error("Invalid saved transcript.");
  for (const segment of value.segments) {
    if (!segment || !Number.isFinite(segment.start) || !Number.isFinite(segment.end) || segment.start < 0 || segment.end > state.duration + .001 || segment.end <= segment.start || typeof segment.text !== "string" || segment.text.length > 10000) throw Error("Invalid passage in saved transcript.");
  }
  value.segments.sort((a,b) => a.start-b.start);
  return value;
}
function render() {
  const fragment = document.createDocumentFragment();
  let chapterNumber = 0;
  rows.forEach((row, index) => {
    // Keep the adjustment window anchored across edits and saved reviews.
    row.originalStart ??= row.start;
    if (row.selected) chapterNumber++;
    const tr = document.createElement("tr");
    tr.classList.toggle("unselected", !row.selected);
    tr.classList.toggle("new-marker", row === highlightedRow);
    const cells = Array.from({length: 5}, () => tr.appendChild(document.createElement("td")));
    const snippet = document.createElement("p"); snippet.className = "snippet";
    const keep = document.createElement("input");
    keep.type = "checkbox"; keep.checked = row.selected; keep.disabled = row.start === 0;
    keep.setAttribute("aria-label", `Include marker at ${timestamp(row.start)}`);
    keep.onchange = () => { row.selected = keep.checked; invalidate(); render(); };
    cells[0].append(keep);
    const start = document.createElement("input");
    start.value = timestamp(row.start); start.disabled = row.start === 0;
    start.setAttribute("aria-label", "Chapter start time");
    start.onchange = () => {
      invalidate();
      try {
        const next = Math.round(parseTime(start.value) * 1000) / 1000;
        if (next >= state.duration || rows.some(other => other !== row && Math.abs(other.start - next) < .001)) throw Error("Choose a time distinct from the other markers and before the end.");
        row.start = next; rows.sort((a,b) => a.start-b.start); render();
      }
      catch (error) { message(error.message, true); start.value = timestamp(row.start); }
    };
    cells[1].append(start);
    const adjustment = document.createElement("div"); adjustment.className = "adjustment";
    const slider = document.createElement("input"); slider.type = "range";
    const anchorMs = Math.round(row.originalStart * 1000);
    const fixed = row.start === 0;
    slider.min = fixed ? 0 : Math.max(-15000, 1 - anchorMs);
    slider.max = fixed ? 0 : Math.min(15000, Math.round(state.duration * 1000) - 1 - anchorMs);
    slider.step = "1"; slider.disabled = fixed;
    slider.setAttribute("aria-label", `Adjust marker originally at ${timestamp(row.originalStart)} by up to 15 seconds`);
    const offset = document.createElement("output"); offset.className = "adjustment-offset";
    function syncAdjustment() {
      const delta = Math.round(row.start * 1000) - anchorMs;
      slider.value = Math.max(Number(slider.min), Math.min(Number(slider.max), delta));
      const signed = `${delta >= 0 ? "+" : ""}${(delta / 1000).toFixed(3)}s`;
      offset.textContent = fixed ? "Fixed at beginning" : `${signed} from original${Math.abs(delta) > 15000 ? " (outside slider range)" : ""}`;
      slider.setAttribute("aria-valuetext", `${signed}; chapter starts at ${timestamp(row.start)}`);
      start.value = timestamp(row.start);
      updateSnippet(row, snippet);
    }
    slider.oninput = () => {
      const nextMs = anchorMs + Number(slider.value);
      if (rows.some(other => other !== row && Math.round(other.start * 1000) === nextMs)) {
        message("There is already a marker at this time. Move slightly earlier or later.", true);
        syncAdjustment(); return;
      }
      row.start = nextMs / 1000;
      rows.sort((a,b) => a.start-b.start);
      invalidate(); syncAdjustment();
    };
    slider.onchange = () => {
      // Reorder after the drag, keeping the active slider focused for keyboard use.
      const focused = document.activeElement === slider;
      render();
      if (focused) $("rows").children[rows.indexOf(row)].querySelector('input[type="range"]').focus({preventScroll: true});
    };
    const limits = document.createElement("div"); limits.className = "adjustment-limits";
    const lower = document.createElement("span"), upper = document.createElement("span");
    lower.textContent = `${(Number(slider.min) / 1000).toFixed(3)}s`;
    upper.textContent = `+${(Number(slider.max) / 1000).toFixed(3)}s`;
    limits.append(lower, upper);
    syncAdjustment(); adjustment.append(slider, limits, offset); cells[1].append(adjustment);
    const title = document.createElement("input");
    title.value = row.title; title.placeholder = row.selected ? `Chapter ${chapterNumber}` : "Chapter title";
    title.maxLength = 500; title.setAttribute("aria-label", "Chapter title");
    title.oninput = () => { row.title = title.value; invalidate(); };
    cells[2].append(title, snippet);
    cells[3].textContent = row.kind === "pause" ? `${Number(row.pause).toFixed(1)}s pause` : row.kind === "existing" ? "Existing chapter" : row.kind === "start" ? "Beginning" : row.kind === "epub" ? "EPUB match" : "Manual marker";
    const listen = document.createElement("button"); listen.textContent = "▶ Preview";
    listen.onclick = () => preview(row.start);
    const insert = document.createElement("button"); insert.textContent = "Insert after…";
    const nextStart = rows[index + 1]?.start ?? state.duration;
    insert.disabled = Math.round(nextStart * 1000) - Math.round(row.start * 1000) < 2;
    insert.setAttribute("aria-label", `Locate a new chapter between ${timestamp(row.start)} and ${timestamp(nextStart)}`);
    insert.onclick = () => {
      audio.pause(); previewEnd = null;
      const midpoint = Math.floor((Math.round(row.start * 1000) + Math.round(nextStart * 1000)) / 2) / 1000;
      $("manual-time").value = timestamp(midpoint);
      $("manual-title").value = "";
      audio.currentTime = midpoint;
      manualMessage(`Looking between ${timestamp(row.start)} and ${timestamp(nextStart)}. Starting at the midpoint; listen and adjust the time, then add the break.`);
      $("manual-time").focus();
      $("manual-form").scrollIntoView({block: "nearest"});
    };
    cells[4].className = "marker-actions";
    const remove = document.createElement("button"); remove.textContent = "Remove";
    remove.className = "remove-marker"; remove.disabled = row.start === 0;
    remove.setAttribute("aria-label", `Remove chapter marker at ${timestamp(row.start)}`);
    remove.title = row.start === 0 ? "The opening chapter must stay at zero." : "Remove this marker; keep the audio.";
    remove.onclick = () => {
      removedRow = row; rows = rows.filter(other => other !== row);
      invalidate(); render(); renderProposals();
      message(`Removed the chapter marker at ${timestamp(row.start)}. Audio is unchanged. Use Undo removal to restore it.`);
    };
    cells[4].append(listen, insert, remove); fragment.append(tr);
  });
  $("rows").replaceChildren(fragment); updateControls();
}
audio.ontimeupdate = () => { if (previewEnd !== null && audio.currentTime >= previewEnd) { audio.pause(); previewEnd = null; } };
audio.onpause = () => { previewEnd = null; };
audio.onerror = () => message("The browser could not play this audio codec. Scanning and export can still work; use a compatible browser to review playback.", true);

async function api(path, body) {
  const response = await fetch(`api/${path}`, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw Error(data.error || `Request failed (${response.status})`);
  }
  return response.json();
}
async function poll() {
  try {
    state = await api("state");
    $("progress").value = state.job.progress;
    if (state.job.status === "running") {
      message(`${state.job.detail || "Working…"} ${state.job.progress}%`);
      setTimeout(poll, 700); return;
    }
    busy = false; $("progress").hidden = true;
    if (state.job.status === "error") message(state.job.error, true);
    else if (state.job.result?.operation === "scan") {
      rows = state.rows; removedRow = null; render();
      message(`Found ${state.job.result.count} candidate pauses. Listen and adjust the selected markers before approving.`);
    } else if (state.job.result?.operation === "export") {
      message(`Export complete: ${state.job.result.path}`); invalidate();
      $("download-export").hidden = false;
    } else if (state.job.result?.operation === "transcribe") {
      transcript = validateTranscript(await api("transcript"));
      await loadAlignment(); render(); renderTranscript();
      message(`Transcript ready: ${transcript.segments.length} passages. Search the audio or read the text beside each marker.`);
    }
    if (state.job.result?.operation === "align") {
      transcript = validateTranscript(await api("transcript"));
      await loadAlignment(); render(); renderTranscript();
      message("EPUB matching finished. Listen to the evidence and accept proposed markers into your review.");
    }
    updateControls(); renderProposals();
  } catch (error) {
    // A lost connection does not imply that the background export stopped.
    message(`Connection interrupted; retrying… ${error.message}`, true);
    setTimeout(poll, 2000);
  }
}
async function startJob(path, body) {
  if (busy) return;
  try {
    busy = true; state.job.cancellable = ["transcribe", "align"].includes(path);
    updateControls(); renderProposals();
    await persistReview();
    await api(path, body);
    $("progress").hidden = false; $("progress").value = 0;
    message(path === "scan" ? "Scanning the audio for pauses…" : path === "transcribe" || path === "align" ? "Preparing local speech recognition and matching…" : "Writing and verifying approved chapters…");
    setTimeout(poll, 500);
  } catch (error) { busy = false; updateControls(); renderProposals(); message(error.message, true); }
}
$("scan").onclick = () => {
  invalidate();
  startJob("scan", {minimum: Number($("minimum").value), noise: Number($("noise").value), spacing: Number($("spacing").value)});
};
$("approved").onchange = updateControls;
$("transcribe").onclick = () => startJob("transcribe", {model: $("speech-model").value, language: $("speech-language").value.trim().toLowerCase() || null});
$("transcript-query").oninput = renderTranscript;
$("export").onclick = () => {
  const chapters = rows.filter(r => r.selected).map((r, i) => ({start: r.start, title: r.title.trim() || `Chapter ${i+1}`}));
  startJob("export", {approved: $("approved").checked, chapters});
};
$("use-playhead").onclick = () => {
  audio.pause(); previewEnd = null;
  $("manual-time").value = timestamp(audio.currentTime);
  manualMessage("Playback position captured. Adjust the time or preview it, then add the chapter break.");
};
$("preview-manual").onclick = () => {
  try { preview(manualTime()); }
  catch (error) { manualMessage(error.message, true); }
};
$("manual-form").onsubmit = event => {
  event.preventDefault();
  if (busy) return;
  try {
    const start = manualTime();
    if (rows.some(r => Math.round(r.start * 1000) === Math.round(start * 1000))) throw Error("There is already a marker at this time. Select that marker or choose a different time.");
    const row = {start, originalStart: start, title: $("manual-title").value.trim(), kind: "manual", selected: true, pause: null};
    rows.push(row); highlightedRow = row;
    rows.sort((a,b) => a.start-b.start); invalidate(); render();
    audio.pause(); previewEnd = null;
    const notice = `Added a manual chapter at ${timestamp(start)}. It is selected for export; review and approve the updated list.`;
    manualMessage(notice); message(notice);
    const added = $("rows").children[rows.indexOf(row)];
    added.querySelector('input[aria-label="Chapter title"]').focus({preventScroll: true});
    added.scrollIntoView({block: "nearest"});
    $("manual-title").value = "";
  } catch (error) { manualMessage(error.message, true); }
};
$("select").onclick = () => { rows.forEach(r => r.selected = true); invalidate(); render(); };
$("undo-remove").onclick = () => {
  if (!removedRow) return;
  if (rows.some(r => Math.round(r.start * 1000) === Math.round(removedRow.start * 1000))) {
    message("Another marker now occupies that time. Move or remove it before restoring this marker.", true); return;
  }
  rows.push(removedRow); rows.sort((a,b) => a.start-b.start); removedRow = null;
  invalidate(); render(); renderProposals(); message("Removed chapter marker restored. Review and approve the updated list.");
};
$("clear").onclick = () => { rows.forEach(r => r.selected = r.start === 0); invalidate(); render(); };
$("save").onclick = () => {
  const blob = new Blob([JSON.stringify({version: 1, filename: state.filename, duration: state.duration, rows, transcript, omitted_sections: omittedSections}, null, 2)], {type: "application/json"});
  const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `${state.filename}.review.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
};
$("load").onchange = async event => {
  try {
    const file = event.target.files[0]; if (!file) return;
    if (file.size > 100_000_000) throw Error("Review file is too large (maximum 100 MB).");
    const data = JSON.parse(await file.text());
    if (data.version !== 1 || data.filename !== state.filename || !Number.isFinite(data.duration) || Math.abs(data.duration - state.duration) > .01) throw Error("This review belongs to a different audio file.");
    if (!Array.isArray(data.rows) || !data.rows.length || data.rows.length > 10000) throw Error("Invalid review file.");
    for (const row of data.rows) {
      if (!row || !Number.isFinite(row.start) || row.start < 0 || row.start >= state.duration || typeof row.title !== "string" || row.title.length > 500 || typeof row.selected !== "boolean" || !["start","pause","existing","manual","epub"].includes(row.kind)) throw Error("Invalid chapter in review file.");
      if (row.originalStart !== undefined && (!Number.isFinite(row.originalStart) || row.originalStart < 0 || row.originalStart >= state.duration)) throw Error("Invalid original marker time in review file.");
    }
    if (new Set(data.rows.map(r => Math.round(r.start * 1000))).size !== data.rows.length) throw Error("Review contains duplicate marker times.");
    data.rows.sort((a,b) => a.start-b.start);
    if (data.rows[0].start !== 0 || !data.rows[0].selected) throw Error("The opening chapter must be selected at zero.");
    const loadedTranscript = validateTranscript(data.transcript);
    const omitted = data.omitted_sections ?? [];
    if (!Array.isArray(omitted) || omitted.length > 2000 || omitted.some(id => typeof id !== "string" || id.length > 100)) throw Error("Invalid omitted section list.");
    omittedSections = omitted;
    rows = data.rows; transcript = loadedTranscript; removedRow = null;
    invalidate(); render(); renderTranscript(); await persistReview({transcript}); await loadAlignment(); message("Saved review loaded. Review and approve before exporting.");
  } catch (error) { message(error.message, true); }
  event.target.value = "";
};
async function init() {
  try {
    state = await api("state"); rows = state.rows; omittedSections = state.omitted_sections || [];
    $("filename").textContent = state.filename || "Choose an audiobook to begin";
    $("duration").textContent = state.filename ? timestamp(state.duration) : "";
    if (state.filename) { audio.src = "audio"; audio.load(); } else audio.removeAttribute("src");
    $("download-export").hidden = true;
    await loadAlignment(); await refreshProjects();
    $("transcription-help").textContent = state.transcription_available ? "Speech recognition is available. Long recordings may take a while; you can choose a smaller model for speed." : "To enable transcription, follow Transcription setup in README.md and restart the app in that environment. Existing saved transcripts can still be loaded and searched.";
    $("transcribe").disabled = !state.transcription_available;
    if (state.transcript_ready) transcript = validateTranscript(await api("transcript"));
    else transcript = null;
    renderTranscript();
    $("output").textContent = state.output; render(); updateControls();
    if (state.job.status === "running") { busy = true; updateControls(); $("progress").hidden = false; poll(); }
    else if (state.job.status === "done" || state.job.status === "error") poll();
    if (state.notice) message(state.notice, true);
  } catch (error) { message(error.message, true); $("settings").disabled = true; $("review").disabled = true; }
}
let alignment = null, book = null;
let saveTimer = null, saveRevision = 0, savedRevision = 0, saveQueue = Promise.resolve();
function queueSave() {
  saveRevision++;
  $("save-status").textContent = "Saving review…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { if (!busy) persistReview().catch(() => {}); }, 600);
}
async function persistReview(extra = {}) {
  clearTimeout(saveTimer);
  if (!state?.filename) return;
  const version = saveRevision;
  const snapshot = JSON.parse(JSON.stringify({rows, omitted_sections: omittedSections, project_id: state.project_id, ...extra}));
  saveQueue = saveQueue.catch(() => {}).then(() => api("review", snapshot));
  try {
    await saveQueue;
    savedRevision = Math.max(savedRevision, version);
    $("save-status").textContent = "Review saved on this computer. Export approval is never saved.";
    $("save-status").classList.remove("error");
  } catch (error) {
    $("save-status").textContent = `Could not save review: ${error.message}. Use Save review JSON as a backup.`;
    $("save-status").classList.add("error");
    throw error;
  }
}
window.addEventListener("beforeunload", event => {
  if (saveRevision > savedRevision) { event.preventDefault(); event.returnValue = ""; }
});
async function loadAlignment() {
  const data = await api("alignment");
  book = data.book; alignment = data.alignment;
  $("book-status").textContent = book ? `${book.title} · ${book.chapters.length} contents entries. ${(book.warnings || []).join(" ")}` : "Upload a matching EPUB to locate its chapters in the audiobook.";
  renderProposals(); updateControls();
}
function renderProposals() {
  const fragment = document.createDocumentFragment();
  const proposals = alignment?.proposals || [];
  const epub = proposals.filter(p => p.source !== "audio");
  $("alignment-summary").textContent = alignment ? `${epub.filter(p => p.start !== null).length} of ${epub.length} EPUB entries matched; ${proposals.length - epub.length} audio-only suggestions; ${epub.filter(p => omittedSections.includes(p.id)).length} marked not present. Listen before accepting.` : "";
  proposals.forEach(proposal => {
    const card = document.createElement("article"); card.className = "proposal";
    const omitted = omittedSections.includes(proposal.id);
    const title = document.createElement("h3"); title.textContent = proposal.title;
    const confidence = document.createElement("span"); confidence.className = `confidence ${proposal.confidence}`;
    confidence.textContent = `${proposal.confidence === "strong" ? "Strong opening match" : proposal.confidence === "review" ? "Needs review" : "Unmatched"}${proposal.start !== null ? ` · ${timestamp(proposal.start)}` : ""}`;
    if (proposal.source === "audio") confidence.textContent = `Audio-only suggestion · ${confidence.textContent}`;
    if (omitted) confidence.textContent = "Marked not present in this recording";
    const reason = document.createElement("p"); reason.textContent = proposal.reason;
    const evidence = document.createElement("div"); evidence.className = "evidence";
    for (const [label, excerpt] of [["Book passage", proposal.book_excerpt], ["Recognized speech", proposal.audio_excerpt]]) {
      const paragraph = document.createElement("p"), heading = document.createElement("strong");
      heading.textContent = `${label}: `; paragraph.append(heading, document.createTextNode(excerpt || "No match found.")); evidence.append(paragraph);
    }
    const actions = document.createElement("div"); actions.className = "toolbar";
    if (proposal.start !== null) {
      const listen = document.createElement("button"); listen.textContent = "▶ Preview match";
      listen.onclick = () => preview(proposal.start); actions.append(listen);
      const accept = document.createElement("button");
      const accepted = rows.some(r => r.epubId === proposal.id);
      accept.textContent = accepted ? "Added to review" : "Use this marker"; accept.disabled = busy || accepted;
      accept.onclick = () => {
        const existing = rows.find(r => Math.round(r.start * 1000) === Math.round(proposal.start * 1000));
        if (existing && existing.start !== 0) {
          message("A marker already exists at this time. Adjust or remove it before using this proposal.", true); return;
        }
        if (existing) { existing.title = proposal.title; existing.epubId = proposal.id; }
        else rows.push({start: proposal.start, originalStart: proposal.start, title: proposal.title, selected: true,
                        kind: proposal.source === "audio" ? "manual" : "epub", pause: null, epubId: proposal.id, evidence: proposal.reason});
        rows.sort((a,b) => a.start-b.start); invalidate(); render(); renderProposals();
        message("Suggested marker added to your review. Adjust its timestamp if needed and approve the final list before export.");
      };
      actions.append(accept);
    }
    const locate = document.createElement("button"); locate.textContent = "Locate manually"; locate.disabled = busy || omitted;
    locate.onclick = () => {
      $("manual-title").value = proposal.title;
      $("manual-time").value = timestamp(proposal.start ?? audio.currentTime);
      manualMessage("Locate this EPUB chapter in the audio, then add the missing break.");
      $("manual-time").focus(); $("manual-form").scrollIntoView({block: "nearest"});
    };
    actions.append(locate);
    if (proposal.start === null && proposal.source !== "audio") {
      const omit = document.createElement("button"); omit.className = "omit-section";
      omit.textContent = omitted ? "Reconsider section" : "Mark not present in recording";
      omit.disabled = busy;
      omit.onclick = () => {
        omittedSections = omitted ? omittedSections.filter(id => id !== proposal.id) : [...omittedSections, proposal.id];
        invalidate(); renderProposals();
      };
      actions.append(omit);
    }
    card.append(title, confidence, reason, evidence, actions); fragment.append(card);
  });
  $("proposals").replaceChildren(fragment);
}
async function refreshProjects() {
  const projects = await api("projects");
  $("project-list").replaceChildren(new Option("Choose a saved project…", ""));
  for (const project of projects) $("project-list").append(new Option(project.filename, project.id));
  if (state?.project_id) $("project-list").value = state.project_id;
}
async function uploadFile(kind, file) {
  if (!file || busy) return;
  try {
    busy = true; updateControls(); renderProposals(); audio.pause();
    await persistReview();
    $("upload-progress").hidden = false; $("upload-progress").value = 0;
    message(`Uploading ${file.name} to this local app…`);
    await new Promise((resolve, reject) => {
      const request = new XMLHttpRequest(); request.open("POST", `api/upload/${kind}`);
      request.setRequestHeader("Content-Type", "application/octet-stream");
      request.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
      request.upload.onprogress = event => { if (event.lengthComputable) $("upload-progress").value = event.loaded / event.total * 100; };
      request.onerror = () => reject(Error("Upload connection failed. Select the file again to retry."));
      request.onload = () => {
        let data = {}; try { data = JSON.parse(request.responseText); } catch (_) {}
        if (request.status >= 200 && request.status < 300) resolve(data);
        else reject(Error(data.error || `Upload failed (${request.status})`));
      };
      request.send(file);
    });
    busy = false; transcript = null; removedRow = null; $("approved").checked = false;
    await init(); message(`${file.name} loaded. ${kind === "audio" ? "Upload the matching EPUB to begin." : "Choose Find EPUB chapters to begin matching."}`);
  } catch (error) { message(error.message, true); }
  finally { busy = false; $("upload-progress").hidden = true; updateControls(); renderProposals(); }
}
$("audio-upload").onchange = event => { uploadFile("audio", event.target.files[0]); event.target.value = ""; };
$("epub-upload").onchange = event => { uploadFile("epub", event.target.files[0]); event.target.value = ""; };
$("open-project").onclick = async () => {
  if (!$("project-list").value || busy) return;
  try {
    busy = true; updateControls(); renderProposals();
    await persistReview(); await api("open", {id: $("project-list").value});
    audio.pause(); transcript = null; removedRow = null; $("approved").checked = false;
    busy = false;
    await init(); message("Saved project opened. Review and approve before exporting.");
  } catch (error) { message(error.message, true); }
  finally { busy = false; updateControls(); renderProposals(); }
};
$("align").onclick = () => startJob("align", {model: $("speech-model").value, language: $("speech-language").value.trim().toLowerCase() || null});
$("pause-job").onclick = async () => {
  try { await api("cancel", {}); message("Pausing at the next safe point. Completed audio chunks are saved."); }
  catch (error) { message(error.message, true); }
};

init();
