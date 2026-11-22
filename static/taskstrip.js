/* ---------------- 🧩 the live task strip (manager plan 4-pre c) ----------------
   One line above the chat while Nova works on a task: the goal, step N of M, what she is doing now, how long it has
   been. Amber when she waits for you; Continue / Drop it when a task stopped. Click it for the card behind it: every
   plan step with ✅ ⏳ ❌ ⏭ ·, the result of each step that really ran, where the plan came from, which brain works.
   The server pushes every change as a "task" event (nova.js listen()); GET /api/task is what to show when the page
   opens. The clock is counted here, so nothing is sent every second. Uses $, api, send and stopNova from nova.js. */
const TASK_ICON = {pending: "·", started: "⏳", done: "✅", failed: "❌", skipped: "⏭"};
const TASK_WORD = {waiting: "⏳ Waiting for you", stopped: "⏸ Stopped", interrupted: "⚠ Interrupted", stuck: "🔁 Stuck",
                   done: "✅ Done", failed: "❌ Didn't finish", dropped: "🗑 Dropped"};
const TASK_SHOW_DONE = 8000;          // a finished task stays up this long
let taskNow = null, taskClock = null, taskHide = null;

function taskTime(t) {
  const s = Math.max(0, Math.round((t.status === "running" || t.status === "waiting" ? Date.now() / 1000 : t.updated) - t.started));
  return s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`
                   : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function taskLine(t) {
  const step = t.step && t.steps ? `step ${t.step} of ${t.steps}` : t.done ? `${t.done} step${t.done === 1 ? "" : "s"} done` : "";
  const state = t.status === "running" ? "" : `${TASK_WORD[t.status] || t.status}${t.why ? ": " + t.why : ""}`;
  const stuckAt = t.status === "stuck" && t.step ? ` on step ${t.step}` : "";
  return [state && state.replace(/^(🔁 Stuck)/, "$1" + stuckAt), t.goal, step, t.status === "running" ? t.now : ""]
    .filter(Boolean);
}

function showTask(t) {
  const box = $("#taskStrip");
  if (!box) return;
  clearTimeout(taskHide);
  if (!t || !t.id) { taskNow = null; clearInterval(taskClock); box.hidden = true; return; }
  const wasOpen = box.open;
  taskNow = t;
  box.className = "taskstrip " + t.status;
  const btn = {
    stop: html`<button type="button" class="ts-stop" title="Stop what ${NAME} is doing (Esc)">■ Stop</button>`,
    continue: html`<button type="button" class="ts-go">▶ Continue</button>`,
    drop: html`<button type="button" class="ts-drop">Drop it</button>`,
  };
  // A question card about this task: its buttons right on the line (one tap answers it), in place of Continue / Drop.
  const q = t.question, q1 = q && q.questions.length === 1 && ["yesno", "one"].includes(q.questions[0].kind) ? q.questions[0] : null;
  const acts = q ? t.actions.filter(a => a === "stop") : t.actions;
  const ask = q ? (q1 ? html`<span class="ts-q">${q1.choices.map(c => html`<button type="button" class="ts-qa" data-v="${c.value}">${c.label}</button>`)}</span>`
                      : html`<i>answer in the chat ↓</i>`) : "";
  put(box, html`<summary><span class="ts-icon">🧩</span><span class="ts-text">${taskLine(t).map((p, i) =>
      html`${i ? html`<i> · </i>` : ""}<span>${p}</span>`)}</span><time>${taskTime(t)}</time>${ask}${acts.map(a => btn[a] || "")}</summary>
    <div class="ts-card">
      ${t.plan.length ? html`<ol>${t.plan.map(s => html`<li class="${s.state}"><b>${TASK_ICON[s.state] || "·"}</b> ${s.text}${
          s.result ? html`<small>→ ${s.result}</small>` : ""}</li>`)}</ol>`
        : html`<p class="mood">No written plan - she works it out step by step.</p>`}
      <p class="mood">Plan from: ${t.source === "none" ? "no plan" : t.source}${t.brain ? html` · Brain: ${t.brain}` : ""}${
          t.read_only ? " · saved by a newer Nova (read-only)" : ""} · <a href="/tasks#t:${t.id}">See it on the Tasks page</a></p>
      ${t.runs.length ? html`<p class="mood">Steps that really ran:</p><ul>${t.runs.map(r =>
          html`<li class="${r.state}">${TASK_ICON[r.state] || "·"} ${r.tool}${r.hint ? " " + r.hint : ""}${
            r.result ? html`<small>→ ${r.result}</small>` : ""}</li>`)}</ul>` : ""}
    </div>`);
  box.open = wasOpen;
  box.hidden = false;
  const go = (sel, fn) => { const b = box.querySelector(sel); if (b) b.onclick = e => { e.preventDefault(); e.stopPropagation(); fn(); }; };
  go(".ts-stop", () => stopNova());
  go(".ts-go", async () => { try { await api("/api/task/pick", {id: t.id}); send("continue"); } catch (e) { add("err", e.message); } });
  go(".ts-drop", async () => { try { showTask(await api("/api/task/drop", {id: t.id})); } catch (e) { add("err", e.message); } });
  box.querySelectorAll(".ts-qa").forEach(b => b.onclick = e => {
    e.preventDefault(); e.stopPropagation();
    box.querySelectorAll(".ts-qa").forEach(x => x.disabled = true);
    add("me", "🙋 " + b.textContent);
    handle(api("/api/answer", {id: q.id, answers: {[q1.key]: b.dataset.v}}));
  });
  clearInterval(taskClock);
  if (t.status === "running" || t.status === "waiting") {
    taskClock = setInterval(() => { const c = box.querySelector("time"); if (c && taskNow) c.textContent = taskTime(taskNow); }, 1000);
  } else if (t.status === "done" || t.status === "failed" || t.status === "dropped") {
    taskHide = setTimeout(() => { box.hidden = true; box.open = false; }, t.status === "dropped" ? 1500 : TASK_SHOW_DONE);
  }
}

async function loadTask() { try { showTask(await (await fetch("/api/task", {cache: "no-store"})).json()); } catch {} }
loadTask();
