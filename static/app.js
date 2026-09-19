"use strict";
const $ = id => document.getElementById(id);
let rows = [], state, busy = false, previewEnd = null, highlightedRow = null, removedRow = null;
let transcript = null;
const audio = $("audio");

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
function invalidate() { $("approved").checked = false; updateControls(); }
async function preview(start) {
  previewEnd = Math.min(state.duration, start + 8);
  audio.currentTime = Math.max(0, start - 8);
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
  $("settings").disabled = busy;
  $("review").disabled = busy;
  $("transcription-settings").disabled = busy;
  $("approved").disabled = busy;
  $("export").disabled = busy || !$("approved").checked;
  $("count").textContent = `${rows.filter(r => r.selected).length} chapters selected`;
  $("undo-remove").hidden = removedRow === null;
}
function updateSnippet(row, element) {
  if (!transcript) {
    element.textContent = "Generate a transcript to see speech near this marker.";
    return;
  }
  const nearby = transcript.segments.filter(s => s.end > Math.max(0, row.start - 8) && s.start < row.start + 8);
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
      previewEnd = Math.min(state.duration, segment.end + .5);
      audio.currentTime = Math.max(0, segment.start - .5);
      try { await audio.play(); } catch (error) { message(`Playback unavailable: ${error.message}`, true); }
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
    cells[3].textContent = row.kind === "pause" ? `${Number(row.pause).toFixed(1)}s pause` : row.kind === "existing" ? "Existing chapter" : row.kind === "start" ? "Beginning" : "Manual marker";
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
      invalidate(); render();
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
    } else if (state.job.result?.operation === "transcribe") {
      transcript = validateTranscript(await api("transcript"));
      render(); renderTranscript();
      message(`Transcript ready: ${transcript.segments.length} passages. Search the audio or read the text beside each marker.`);
    }
    updateControls();
  } catch (error) {
    // A lost connection does not imply that the background export stopped.
    message(`Connection interrupted; retrying… ${error.message}`, true);
    setTimeout(poll, 2000);
  }
}
async function startJob(path, body) {
  busy = true; updateControls();
  try {
    await api(path, body);
    $("progress").hidden = false; $("progress").value = 0;
    message(path === "scan" ? "Scanning the audio for pauses…" : path === "transcribe" ? "Preparing local speech recognition…" : "Writing and verifying approved chapters…");
    setTimeout(poll, 500);
  } catch (error) { busy = false; updateControls(); message(error.message, true); }
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
  invalidate(); render(); message("Removed chapter marker restored. Review and approve the updated list.");
};
$("clear").onclick = () => { rows.forEach(r => r.selected = r.start === 0); invalidate(); render(); };
$("save").onclick = () => {
  const blob = new Blob([JSON.stringify({version: 1, filename: state.filename, duration: state.duration, rows, transcript}, null, 2)], {type: "application/json"});
  const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `${state.filename}.review.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
};
$("load").onchange = async event => {
  try {
    const file = event.target.files[0]; if (!file) return;
    if (file.size > 20_000_000) throw Error("Review file is too large (maximum 20 MB).");
    const data = JSON.parse(await file.text());
    if (data.version !== 1 || data.filename !== state.filename || !Number.isFinite(data.duration) || Math.abs(data.duration - state.duration) > .01) throw Error("This review belongs to a different audio file.");
    if (!Array.isArray(data.rows) || !data.rows.length || data.rows.length > 10000) throw Error("Invalid review file.");
    for (const row of data.rows) {
      if (!row || !Number.isFinite(row.start) || row.start < 0 || row.start >= state.duration || typeof row.title !== "string" || row.title.length > 500 || typeof row.selected !== "boolean" || !["start","pause","existing","manual"].includes(row.kind)) throw Error("Invalid chapter in review file.");
      if (row.originalStart !== undefined && (!Number.isFinite(row.originalStart) || row.originalStart < 0 || row.originalStart >= state.duration)) throw Error("Invalid original marker time in review file.");
    }
    if (new Set(data.rows.map(r => Math.round(r.start * 1000))).size !== data.rows.length) throw Error("Review contains duplicate marker times.");
    data.rows.sort((a,b) => a.start-b.start);
    if (data.rows[0].start !== 0 || !data.rows[0].selected) throw Error("The opening chapter must be selected at zero.");
    const loadedTranscript = validateTranscript(data.transcript);
    rows = data.rows; transcript = loadedTranscript; removedRow = null;
    invalidate(); render(); renderTranscript(); message("Saved review loaded. Review and approve before exporting.");
  } catch (error) { message(error.message, true); }
  event.target.value = "";
};
async function init() {
  try {
    state = await api("state"); rows = state.rows;
    $("filename").textContent = state.filename; $("duration").textContent = timestamp(state.duration);
    $("transcription-help").textContent = state.transcription_available ? "Speech recognition is available. Long recordings may take a while; you can choose a smaller model for speed." : "To enable transcription, follow Transcription setup in README.md and restart the app in that environment. Existing saved transcripts can still be loaded and searched.";
    $("transcribe").disabled = !state.transcription_available;
    if (state.transcript_ready) transcript = validateTranscript(await api("transcript"));
    renderTranscript();
    $("output").textContent = state.output; render();
    if (state.job.status === "running") { busy = true; updateControls(); $("progress").hidden = false; poll(); }
    else if (state.job.status === "done" || state.job.status === "error") poll();
  } catch (error) { message(error.message, true); $("settings").disabled = true; $("review").disabled = true; }
}
init();
