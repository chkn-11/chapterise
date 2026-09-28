"use strict";
const $ = id => document.getElementById(id);
const player = $("audio");
let bridge, snapshot, queue = [], index = 0, draft = null, saving = false, previewEnd = null;
const drafts = new Map();
function time(value) {
  const ms = Math.round(value * 1000);
  return `${String(Math.floor(ms / 3600000)).padStart(2, '0')}:${String(Math.floor(ms / 60000) % 60).padStart(2, '0')}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}.${String(ms % 1000).padStart(3, '0')}`;
}
function status(text, error = false) { $("status").textContent = text; $("status").classList.toggle('error', error); }
function connected() {
  if (!window.opener || window.opener.closed) throw Error('The main window is closed. Reopen the project there to continue.');
  snapshot = bridge.snapshot();
}
function refreshList() {
  $("chapter-list").replaceChildren(...queue.map((p, i) => {
    const row = snapshot.rows.find(r => r.epubId === p.id);
    const decision = snapshot.omitted.includes(p.id) ? 'Not present' : row?.selected ? 'Added' : 'To review';
    return new Option(`${i + 1}. ${row?.title || p.title} · ${decision}`, String(i));
  }));
  $("chapter-list").value = String(index);
}
function boundary(value) {
  if (value === null) throw Error('No predicted boundary. Enter a time or use the playback position first.');
  const pieces = String(value).trim().split(':');
  if (pieces.length > 3 || pieces.some(p => !/^\d+(\.\d+)?$/.test(p)) || (pieces.length > 1 && pieces.slice(1).some(p => Number(p) >= 60)))
    throw Error('Use seconds or HH:MM:SS.mmm.');
  const result = Math.round(pieces.reduce((n, p) => n * 60 + Number(p), 0) * 1000) / 1000;
  if (!Number.isFinite(result) || result < 0 || result >= snapshot.duration) throw Error('Choose a time within the recording.');
  return result;
}
function readDraft() {
  draft.title = $("title").value;
  draft.start = $("boundary").value.trim() ? boundary($("boundary").value) : null;
}
function show(autoplay = false) {
  connected(); refreshList();
  const p = draft.manual ? {title: 'Audio-only marker', source: 'audio', start: null, confidence: 'review',
    reason: 'Manually located audio-only section. Listen and choose its boundary.'} : queue[index];
  $("position").textContent = draft.manual ? 'Adding an audio-only marker; your current section will remain in the queue.' : `${index + 1} of ${queue.length} · ${p.source === 'audio' ? 'Audio-only suggestion' : 'EPUB section'}`;
  $("title").value = draft.title;
  $("boundary").value = draft.start === null ? '' : time(draft.start);
  $("predicted").textContent = p.start === null ? 'No predicted boundary' : time(p.start);
  $("strength").textContent = p.confidence === 'strong' ? 'Strong opening match' : p.confidence === 'unmatched' ? 'Unmatched' : 'Needs review';
  $("reason").textContent = p.reason || '';
  $("book-text").textContent = p.book_excerpt || (p.source === 'audio' ? 'Audio-only section; no book passage.' : 'No book passage.');
  $("audio-text").textContent = p.audio_excerpt || 'No matched speech. Locate the start using the player.';
  $("decision").textContent = snapshot.omitted.includes(p.id) ? 'Currently marked not present. Accepting a marker will reconsider this decision.' : snapshot.rows.some(r => r.epubId === p.id) ? 'Already added. Accepting again updates the existing marker.' : 'Not yet accepted.';
  $("omit").disabled = draft.manual;
  $("previous").disabled = index === 0 && !draft.manual;
  $("accept").textContent = draft.manual ? 'Save audio-only marker (Enter)' : 'Matched — save & next (Enter)';
  $("add-audio").disabled = draft.manual;
  $("next").textContent = draft.manual ? 'Cancel audio-only marker' : index === queue.length - 1 ? 'Finish review' : 'Skip / next';
  if (autoplay && draft.start !== null) play();
}
function select(next, autoplay = true) {
  if (saving) return;
  player.pause(); previewEnd = null;
  if (draft) { readDraft(); if (!draft.manual) drafts.set(queue[index].id, draft); }
  index = next; connected();
  const p = queue[index], row = snapshot.rows.find(r => r.epubId === p.id);
  draft = drafts.get(p.id) || {id: p.id, start: row?.start ?? p.start, title: row?.title ?? p.title, manual: false};
  show(autoplay); status('Adjust and listen, then press Enter to save this marker.');
}
async function play() {
  try {
    connected(); readDraft(); const start = boundary(draft.start);
    player.currentTime = start; previewEnd = Math.min(snapshot.duration, start + 10);
    await player.play();
  } catch (error) { status(error.message, true); }
}
function nudge(delta) {
  readDraft();
  const start = boundary(draft.start);
  draft.start = Math.max(0, Math.min((Math.ceil(snapshot.duration * 1000) - 1) / 1000, Math.round((start + delta) * 1000) / 1000));
  $("boundary").value = time(draft.start); play();
}
async function decide(action) {
  if (saving) return;
  try {
    connected();
    if (action === 'accept') readDraft();
    else { try { readDraft(); } catch (_) { /* Omission needs no valid boundary. */ } }
    const wasManual = draft.manual;
    saving = true; $("controls").disabled = true; $("close").disabled = true;
    player.pause(); status('Saving decision…');
    await bridge.commit({...draft, action});
    connected();
    if (action === 'omit') drafts.set(queue[index].id, draft);
    else if (!wasManual) drafts.delete(queue[index].id);
    saving = false; $("controls").disabled = false; $("close").disabled = false;
    draft = null;
    if (wasManual) select(index);
    else if (index + 1 < queue.length) select(index + 1);
    else { select(index, false); status('End of review. Decisions saved. Close this window to check the list and approve export.'); }
    $("accept").focus();
  } catch (error) { status(`Decision not saved: ${error.message} Retry before advancing.`, true); }
  finally { saving = false; $("controls").disabled = false; $("close").disabled = false; }
}
function action(fn) { return () => { if (saving) return; try { connected(); fn(); } catch (error) { status(error.message, true); } }; }
$("back").onclick = action(() => nudge(-.25)); $("forward").onclick = action(() => nudge(.25));
$("back-large").onclick = action(() => nudge(-5)); $("forward-large").onclick = action(() => nudge(5));
$("play").onclick = action(() => player.paused ? play() : player.pause());
$("capture").onclick = action(() => { draft.start = boundary(player.currentTime); $("boundary").value = time(draft.start); player.pause(); });
$("accept").onclick = () => decide('accept'); $("omit").onclick = () => decide('omit');
$("previous").onclick = action(() => select(draft.manual ? index : Math.max(0, index - 1)));
$("next").onclick = action(() => {
  if (draft.manual) { draft = null; select(index, false); }
  else if (index + 1 < queue.length) select(index + 1);
  else { player.pause(); status('End of queue. Unaccepted drafts are not saved. Close to return to the main review.'); }
});
$("chapter-list").onchange = action(() => select(Number($("chapter-list").value)));
$("boundary").onchange = action(() => { readDraft(); play(); });
$("add-audio").onclick = action(() => {
  readDraft(); drafts.set(queue[index].id, draft);
  draft = {id: `manual-audio-${Date.now()}`, title: 'Audio-only chapter', start: boundary(player.currentTime), manual: true};
  player.pause(); show(); $("title").focus();
});
$("close").onclick = () => { if (!saving) window.close(); };
player.ontimeupdate = () => { if (previewEnd !== null && player.currentTime >= previewEnd) player.pause(); };
player.onpause = () => { if (player.paused) previewEnd = null; };
player.onerror = () => status('Audio playback failed. Check that your browser supports this recording.', true);
document.addEventListener('keydown', event => {
  if (saving || !draft || event.ctrlKey || event.altKey || event.metaKey || event.isComposing) return;
  if (event.target.closest('input, textarea, select, audio, [contenteditable="true"]')) return;
  const commands = {ArrowLeft: () => nudge(event.shiftKey ? -5 : -.25), ArrowRight: () => nudge(event.shiftKey ? 5 : .25),
    Enter: () => { if (!event.repeat) decide('accept'); }, ' ': () => player.paused ? play() : player.pause(),
    n: () => { if (!event.repeat && !draft.manual) decide('omit'); }, '[': () => $("previous").click(), ']': () => $("next").click()};
  if (commands[event.key]) { event.preventDefault(); action(commands[event.key])(); }
});
window.addEventListener('beforeunload', event => {
  if (saving) { event.preventDefault(); event.returnValue = ''; }
});
try {
  bridge = window.opener?.chapteriseRapid;
  if (!bridge) throw Error('Open rapid review from the main Chapterise window after EPUB matching.');
  connected(); $("file").textContent = snapshot.filename; player.src = snapshot.audioURL;
  const extras = snapshot.proposals.filter(p => p.source === 'audio').sort((a, b) => a.start - b.start);
  for (const p of snapshot.proposals.filter(p => p.source !== 'audio')) {
    while (extras.length && p.start !== null && extras[0].start <= p.start) queue.push(extras.shift());
    queue.push(p);
  }
  queue.push(...extras);
  if (!queue.length) throw Error('Run EPUB matching in the main window first.');
  const first = queue.findIndex(p => !snapshot.omitted.includes(p.id) && !snapshot.rows.some(r => r.epubId === p.id && r.selected));
  select(Math.max(0, first), false); $("controls").disabled = false; $("play").focus();
} catch (error) { status(error.message, true); }
