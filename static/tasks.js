/* The Tasks page (/tasks, manager plan 4-pre d): Nova's own laptop tasks and the worker jobs, newest first.
   Markup goes through html`...` / put() from markup.js, so every value from the server is escaped.
   Nothing here changes anything by itself: Drop it and Continue use /api/task/drop and /api/task/pick; a commit or a
   retry opens the chat with the words filled in (/?draft=...), where her Allow / No asks first. */
const $ = id => document.getElementById(id);
const ICON = {pending: "·", started: "⏳", done: "✅", failed: "❌", skipped: "⏭"};
const WORD = {running: "running", waiting: "waiting for you", stopped: "stopped", interrupted: "interrupted",
              stuck: "stuck", done: "done", failed: "didn't finish", dropped: "dropped"};
const KIND = {laptop: "💻 Laptop", coding: "🧑‍💻 Coding", research: "🔎 Research"};
const filters = {status: "", kind: ""};
const opened = new Set(location.hash ? [decodeURIComponent(location.hash.slice(1))] : []);
let flash, live = false;

function say(t) { $("msg").textContent = t; clearTimeout(flash); flash = setTimeout(() => $("msg").textContent = "", 5000); }
function when(t) {
  const d = new Date(t * 1000), today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})
               : d.toLocaleDateString([], {day: "numeric", month: "short"}) + " " + d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
}
const n = (k, word) => `${k} ${word}${k === 1 ? "" : "s"}`;
function took(s) { return s == null ? "" : s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`; }
function meta(r) {
  return [KIND[r.kind], r.who !== "Research" && r.who, r.type && r.type !== "research" && r.type,
          r.chain && `step ${r.chain} (research → coding)`,
          r.steps_total ? `${r.steps_done} of ${n(r.steps_total, "step")}` : r.steps_done ? n(r.steps_done, "step") : "",
          r.files ? n(r.files, "file") + " changed" : "", r.sources ? n(r.sources, "source") : "",
          r.cost ? `$${(+r.cost).toFixed(2)}` : "", took(r.secs),
          r.verdict === true ? "✓ checks passed" : r.verdict === false ? "✗ a check failed" : "",
          r.why && r.status !== "running" ? `why: ${r.why}` : ""].filter(Boolean).join(" · ");
}
function row(r) {
  return html`<details class="row" data-id="${r.id}" ${opened.has(r.id) ? html`open` : ""}>
    <summary><span class="when">${when(r.when)}</span><span class="goal">${r.goal || "(no goal)"}</span>
      <span class="st ${r.status}">${WORD[r.status] || r.status}</span><span class="meta">${meta(r)}</span></summary>
    <div class="body">${r.can_continue ? html`<div class="acts"><button data-go="${r.id}">▶ Continue</button><button data-drop="${r.id}">Drop it</button></div>` : ""}<div class="more">Loading…</div></div>
  </details>`;
}

function taskBody(d) {
  const c = d.card;
  return html`${c.plan.length ? html`<p class="k">Plan (from ${c.source}):</p><ol>${c.plan.map(s =>
      html`<li class="${s.state}">${ICON[s.state] || "·"} ${s.text}${s.result ? html`<small>→ ${s.result}</small>` : ""}</li>`)}</ol>`
      : html`<p class="k">No written plan - she worked it out step by step.</p>`}
    <p class="k">Steps that really ran:</p>${c.runs.length ? html`<ul>${c.runs.map(r =>
      html`<li class="${r.state}">${ICON[r.state] || "·"} ${r.tool}${r.hint ? " " + r.hint : ""}${r.result ? html`<small>→ ${r.result}</small>` : ""}</li>`)}</ul>`
      : html`<p class="k">None.</p>`}
    ${d.checks.length ? html`<p class="k">The Judge's checks (${d.verdict_source || "checks"}): ${d.checks.join("; ")}</p>` : ""}
    <p class="k">Why it ended: ${d.status === "running" ? "still running" : d.status === "waiting" ? "waiting for you" + (d.why ? ": " + d.why : "")
      : d.why || (d.status === "done" ? "done" : WORD[d.status] || d.status)}${d.card.read_only ? " · saved by a newer Nova (read-only)" : ""}</p>`;
}

function jobBody(d) {
  return html`<p><span class="k">Brief:</span> ${d.brief}</p>
    ${d.folder ? html`<p><span class="k">Folder:</span> ${d.folder}</p>` : ""}
    ${d.files_list.length ? html`<p class="k">Files changed${d.committed ? " (committed)" : " (not committed)"}:</p><ul>${d.files_list.map(f => html`<li>${f}</li>`)}</ul>` : ""}
    ${d.source_list.length ? html`<p class="k">Sources:</p><ul>${d.source_list.map(s => html`<li>${s}</li>`)}</ul>` : ""}
    ${d.result ? html`<p><span class="k">Result:</span> ${d.result}</p>` : ""}
    ${d.why ? html`<p><span class="k">Why it ended:</span> ${d.why}</p>` : ""}
    ${d.actions.length ? html`<div class="acts">${d.actions.map(a => a.do === "chat"
        ? html`<button data-chat="${a.say}" title="Opens the chat with this filled in - she asks before doing it">${a.label}</button>`
        : html`<button data-${a.do}="${d.id}">${a.label}</button>`)}</div>` : ""}
    <pre class="out" hidden></pre>`;
}

async function openRow(det) {
  const id = det.dataset.id, box = det.querySelector(".more");
  try {
    const r = await fetch("/api/tasks/" + encodeURIComponent(id), {cache: "no-store"});
    const d = await r.json();
    if (!r.ok) { box.textContent = d.error || "Can't load it."; return; }
    put(box, id.startsWith("t:") ? taskBody(d) : jobBody(d));
  } catch (e) { box.textContent = "Can't reach Nova."; }
}

// 🙋 a question she is waiting on (question.js): answered here like in the chat; her reply is shown under it.
let shownQ = null;
async function loadQuestion() {
  let q = {};
  try { q = await (await fetch("/api/question", {cache: "no-store"})).json(); } catch (e) { return; }
  $("asking").hidden = !q.id;
  if (!q.id || q.id === shownQ) return;
  shownQ = q.id;
  const box = $("question"); box.replaceChildren();
  box.append(questionCard(q, async answers => {
    const j = await post("/api/answer", {id: q.id, answers});
    const said = document.createElement("div"); said.className = "qreply";
    said.textContent = (j.reply || "") + " (the rest is in the chat)";
    box.append(said); load();
  }));
}

async function load() {
  loadQuestion();
  const q = new URLSearchParams(Object.entries(filters).filter(([, v]) => v));
  let j;
  try { j = await (await fetch("/api/tasks?" + q, {cache: "no-store"})).json(); } catch (e) { say("Can't reach Nova."); return; }
  $("carry").hidden = !j.top.length;
  put($("top"), j.top.map(row));
  put($("rows"), j.rows.length ? j.rows.map(row) : html`<div class="empty">Nothing here yet.</div>`);
  document.querySelectorAll("details.row[open]").forEach(openRow);
  live = j.rows.some(r => r.status === "running" && r.kind !== "laptop");       // a worker job: look again in 5 s
  const first = opened.size && document.querySelector(`details.row[open]`);
  if (first && location.hash && !load.scrolled) { first.scrollIntoView({block: "center"}); load.scrolled = true; }
}

document.addEventListener("toggle", e => {
  const det = e.target;
  if (!det.matches?.("details.row")) return;
  if (det.open) { opened.add(det.dataset.id); openRow(det); } else opened.delete(det.dataset.id);
}, true);

async function post(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
document.addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b) return;
  const group = b.closest(".filters");
  if (group) {
    filters[group.id] = b.dataset.v;
    group.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
    return load();
  }
  try {
    if (b.dataset.go) { await post("/api/task/pick", {id: b.dataset.go.slice(2)}); location.href = "/?draft=continue"; }
    else if (b.dataset.drop) { await post("/api/task/drop", {id: b.dataset.drop.slice(2)}); say("Dropped - she won't carry it on."); load(); }
    else if (b.dataset.chat) location.href = "/?draft=" + encodeURIComponent(b.dataset.chat);
    else if (b.dataset.changes || b.dataset.report) {
      const id = b.dataset.changes || b.dataset.report, what = b.dataset.changes ? "changes" : "report";
      const out = b.closest(".body").querySelector("pre.out");
      out.hidden = false; out.textContent = "Loading…";
      try {
        const r = await fetch(`/api/tasks/${encodeURIComponent(id)}/${what}`, {cache: "no-store"});
        const j = await r.json();
        out.textContent = j.text || j.error || "Nothing to show.";
      } catch (err) { out.textContent = "Can't reach Nova."; }
    }
  } catch (err) { say(err.message); }
});

// Live: every task card change is pushed on the same stream as the chat's; worker jobs are looked at every 5 s.
let again;
function soon() { clearTimeout(again); again = setTimeout(load, 400); }
if (window.EventSource) {
  const es = new EventSource("/api/stream");
  es.addEventListener("task", soon);
  es.addEventListener("question", soon);
}
setInterval(() => { if (live) load(); }, 5000);
load();
