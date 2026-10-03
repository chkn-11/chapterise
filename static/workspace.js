"use strict";
const workspaceLabels = {projects: "Projects", queue: "Transcription queue", detect: "Detect chapters", transcript: "Search transcript", review: "Review chapters", export: "Approve & export"};
let workspaceView = null;
window.showWorkspace = function(view, focus = false) {
  if (!workspaceLabels[view]) return;
  workspaceView = view;
  for (const panel of document.querySelectorAll("[data-view]")) panel.hidden = panel.dataset.view !== view;
  for (const button of document.querySelectorAll("[data-workspace]")) {
    const selected = button.dataset.workspace === view;
    button.setAttribute("aria-current", selected ? "page" : "false");
  }
  $("workspace-heading").textContent = workspaceLabels[view].toUpperCase();
  history.replaceState(null, "", `#${view}`);
  if (focus) {
    const panel = document.querySelector(`[data-view="${view}"]`);
    panel.tabIndex = -1; panel.focus({preventScroll: true});
    window.scrollTo({top: 0, behavior: "instant"});
  }
};
window.syncWorkspace = function() {
  if (!workspaceView && state) showWorkspace(workspaceLabels[location.hash.slice(1)] ? location.hash.slice(1) : state?.filename ? "review" : "projects");
  $("project-name").textContent = book?.title || state?.filename?.replace(/\.(m4a|m4b)$/i, "") || "Choose an audiobook";
  $("project-summary").textContent = state?.filename ? `${timestamp(state.duration)} · ${book ? "EPUB attached" : "No EPUB"} · ${transcript ? "Transcript ready" : "No transcript"} · ${rows.filter(row => row.selected).length} selected chapters` : "Open a saved project or upload a book to begin.";
  $("transport-title").textContent = state?.filename || "Audio preview";
};
for (const button of document.querySelectorAll("[data-workspace]")) button.onclick = () => showWorkspace(button.dataset.workspace, true);
document.querySelector(".brand").onclick = event => { event.preventDefault(); showWorkspace(state?.filename ? "review" : "projects", true); };
window.addEventListener("hashchange", () => showWorkspace(location.hash.slice(1)));
syncWorkspace();
