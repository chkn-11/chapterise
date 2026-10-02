"use strict";
let queueUploading = false;
function queueSettings() {
  const value = $("queue-start").value;
  const notBefore = value ? new Date(value).getTime() / 1000 : 0;
  if (!Number.isFinite(notBefore)) throw Error("Choose a valid start time.");
  return {align: $("queue-align").checked, model: $("queue-model").value, language: $("queue-language").value.trim().toLowerCase() || null, not_before: notBefore};
}
function queueMessage(text) { $("queue-status").textContent = text; }
async function refreshQueue() {
  try {
    const queue = await api("queue");
    $("queue-pause").disabled = queue.paused;
    $("queue-resume").disabled = !queue.paused;
    $("queue-current").disabled = !state?.project_id || queueUploading;
    const fragment = document.createDocumentFragment();
    for (const task of queue.tasks) {
      const card = document.createElement("div"); card.className = "proposal";
      const name = document.createElement("strong"); name.textContent = task.filename;
      const detail = document.createElement("p");
      detail.textContent = `${task.status} · ${task.progress}% · ${task.model} · ${task.language || "automatic language"}${task.not_before ? ` · earliest start ${new Date(task.not_before * 1000).toLocaleString()}` : ""}${task.detail ? ` · ${task.detail}` : ""}`;
      const actions = document.createElement("div"); actions.className = "toolbar";
      if (task.status !== "running") {
        for (const [action, label] of [["up", "Move earlier"], ["retry", "Retry / transcribe again"], ["remove", "Remove task"]]) {
          if (action === "retry" && !["done", "error"].includes(task.status)) continue;
          const button = document.createElement("button"); button.textContent = label;
          button.onclick = () => queueAction(action, task.id); actions.append(button);
        }
      }
      card.append(name, detail, actions); fragment.append(card);
    }
    $("queue-tasks").replaceChildren(fragment);
    if (!queueUploading) queueMessage(`${queue.paused ? "Queue paused" : "Queue enabled"}. ${queue.tasks.filter(t => t.status === "pending").length} waiting, ${queue.tasks.filter(t => t.status === "done").length} completed.`);
  } catch (error) { queueMessage(error.message); }
}
async function queueAction(action, id) {
  try { await api("queue", {action, id}); await refreshQueue(); }
  catch (error) { queueMessage(error.message); }
}
$("queue-pause").onclick = () => queueAction("pause");
$("queue-resume").onclick = () => queueAction("resume");
$("queue-current").onclick = async () => {
  try {
    await persistReview();
    await api("queue", {action: "add", project: state.project_id, ...queueSettings()});
    await refreshQueue();
  } catch (error) { queueMessage(error.message); }
};
$("queue-files").onchange = async event => {
  const files = [...event.target.files]; event.target.value = "";
  if (queueUploading) return;
  queueUploading = true; $("queue-files").disabled = true;
  try {
    const settings = queueSettings();
    for (const file of files) {
      queueMessage(`Uploading ${file.name}…`);
      const response = await fetch("api/upload/queue", {method: "POST", headers: {"Content-Type": "application/octet-stream", "X-File-Name": encodeURIComponent(file.name)}, body: file});
      const result = await response.json();
      if (!response.ok) throw Error(result.error || "Upload failed.");
      await api("queue", {action: "add", project: result.project, ...settings});
    }
    await refreshProjects();
    queueUploading = false; await refreshQueue();
  } catch (error) { queueMessage(`${error.message} Books already uploaded remain in saved projects.`); }
  finally { queueUploading = false; $("queue-files").disabled = false; }
};
$("queue-refresh-transcript").onclick = async () => {
  try {
    if (busy || rapidWindow && !rapidWindow.closed) throw Error("Finish the current operation or close rapid review first.");
    await persistReview();
    transcript = validateTranscript(await api("transcript"));
    renderTranscript(); await loadAlignment();
    queueMessage(transcript ? "Saved transcript loaded for the open project." : "No completed transcript for this project yet.");
  } catch (error) { queueMessage(error.message); }
};
refreshQueue();
setInterval(() => { if (!queueUploading) refreshQueue(); }, 3000);
