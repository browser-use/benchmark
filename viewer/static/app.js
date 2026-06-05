"use strict";

const state = {
  runs: [],
  currentRun: null,
  tasks: [],
  currentTaskId: null,
  filter: "",
};

const $ = (sel) => document.querySelector(sel);

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json();
}

function verdictBadge(v) {
  if (v === true) return '<span class="badge pass">PASS</span>';
  if (v === false) return '<span class="badge fail">FAIL</span>';
  return '<span class="badge unknown">?</span>';
}

function fmt(n, digits = 2) {
  if (n === null || n === undefined) return "—";
  if (typeof n !== "number") return String(n);
  return n.toFixed(digits);
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function loadRuns() {
  const data = await fetchJSON("/api/runs");
  $("#results-root").textContent = data.results_root;
  state.runs = data.runs;
  const sel = $("#run-select");
  sel.innerHTML = "";
  if (!data.runs.length) {
    const opt = document.createElement("option");
    opt.textContent = "(no runs found)";
    sel.appendChild(opt);
    return;
  }
  data.runs.sort((a, b) => (b.modified || 0) - (a.modified || 0));
  for (const r of data.runs) {
    const opt = document.createElement("option");
    opt.value = r.name;
    opt.textContent = `${r.name}  (${r.task_count})`;
    sel.appendChild(opt);
  }
  sel.value = data.runs[0].name;
  await selectRun(data.runs[0].name);
}

async function selectRun(name) {
  state.currentRun = name;
  state.currentTaskId = null;
  $("#detail-content").hidden = true;
  $("#detail-placeholder").hidden = false;
  $("#detail-placeholder").textContent = "Loading…";
  const data = await fetchJSON(`/api/runs/${encodeURIComponent(name)}/tasks`);
  state.tasks = data.tasks;
  $("#task-summary").textContent = `${data.summary.passed}✅ / ${data.summary.failed}❌ / ${data.summary.total}`;
  renderTaskList();
  $("#detail-placeholder").textContent = "Select a task to view details.";
}

function renderTaskList() {
  const ul = $("#task-list");
  ul.innerHTML = "";
  const q = state.filter.toLowerCase();
  for (const t of state.tasks) {
    if (q && !(t.task_id || "").toLowerCase().includes(q) && !(t.prompt || "").toLowerCase().includes(q)) {
      continue;
    }
    const li = document.createElement("li");
    if (t.task_id === state.currentTaskId) li.classList.add("active");
    li.innerHTML = `
      <div class="meta">${verdictBadge(t.verdict)} <span>${t.steps ?? "—"} steps</span> <span>${fmt(t.duration, 1)}s</span></div>
      <div class="prompt" title="${escapeHtml(t.prompt || "")}">${escapeHtml(t.prompt || t.task_id)}</div>
      <div class="muted">${t.task_id}</div>
    `;
    li.addEventListener("click", () => selectTask(t.task_id));
    ul.appendChild(li);
  }
}

async function selectTask(taskId) {
  state.currentTaskId = taskId;
  renderTaskList();
  $("#detail-placeholder").hidden = true;
  $("#detail-content").hidden = false;
  $("#detail-header").innerHTML = "<em>Loading…</em>";
  const url = `/api/runs/${encodeURIComponent(state.currentRun)}/tasks/${encodeURIComponent(taskId)}`;
  const data = await fetchJSON(url);
  renderDetail(data);
}

function renderDetail(data) {
  const trace = data.agent_trace || {};
  const j = data.judgement || {};
  const m = data.metrics || {};

  $("#detail-header").innerHTML = `
    <h2>${verdictBadge(j.verdict)} ${escapeHtml(trace.agent_task || data.task_id)}</h2>
    <div class="muted">task_id: ${escapeHtml(data.task_id)}</div>
  `;

  $("#judgement-block").innerHTML = `
    <h3>Judge</h3>
    <div><strong>Verdict:</strong> ${verdictBadge(j.verdict)}</div>
    ${j.failure_reason ? `<div><strong>Failure reason:</strong> ${escapeHtml(j.failure_reason)}</div>` : ""}
    <div><strong>Impossible task:</strong> ${j.impossible_task ?? "—"}</div>
    <div><strong>Reached CAPTCHA:</strong> ${j.reached_captcha ?? "—"}</div>
    <div style="margin-top:8px"><strong>Reasoning:</strong><br><span>${escapeHtml(j.reasoning || "")}</span></div>
  `;

  $("#metrics-block").innerHTML = `
    <h3>Metrics</h3>
    <dl>
      <dt>Steps</dt><dd>${m.steps ?? "—"}</dd>
      <dt>Duration (s)</dt><dd>${fmt(m.duration, 2)}</dd>
      <dt>Cost</dt><dd>${m.cost ?? "—"}</dd>
    </dl>
  `;

  $("#final-result-block").innerHTML = `
    <h3>Final result</h3>
    <pre style="white-space:pre-wrap;font-family:inherit;margin:0">${escapeHtml(trace.final_result || "")}</pre>
  `;

  const steps = trace.agent_steps || [];
  const ol = $("#steps-list");
  ol.innerHTML = "";
  for (const s of steps) {
    const li = document.createElement("li");
    li.innerHTML = `<pre>${escapeHtml(String(s))}</pre>`;
    ol.appendChild(li);
  }

  const shotsDiv = $("#screenshots");
  shotsDiv.innerHTML = "";
  const count = trace.screenshot_count || 0;
  if (!count) {
    shotsDiv.innerHTML = '<div class="muted">No screenshots.</div>';
    return;
  }
  for (let i = 0; i < count; i++) {
    const url = `/api/runs/${encodeURIComponent(state.currentRun)}/tasks/${encodeURIComponent(data.task_id)}/screenshots/${i}.png`;
    const fig = document.createElement("figure");
    fig.innerHTML = `<img loading="lazy" src="${url}" alt="screenshot ${i}"><figcaption>#${i + 1}</figcaption>`;
    fig.querySelector("img").addEventListener("click", () => openLightbox(url));
    shotsDiv.appendChild(fig);
  }
}

function openLightbox(src) {
  const box = document.createElement("div");
  box.className = "lightbox";
  box.innerHTML = `<img src="${src}">`;
  box.addEventListener("click", () => box.remove());
  document.body.appendChild(box);
}

document.addEventListener("DOMContentLoaded", () => {
  $("#run-select").addEventListener("change", (e) => selectRun(e.target.value));
  $("#task-filter").addEventListener("input", (e) => {
    state.filter = e.target.value;
    renderTaskList();
  });
  loadRuns().catch((e) => {
    $("#results-root").textContent = `Error: ${e.message}`;
  });
});
