/* The Tasks page's ⏰ Schedules (manager plan 4g): each schedule with its next run, its steps' risk levels and its
   last runs. Read-only: Pause / Resume / Delete open the chat with the words filled in (/?draft=...), where you send
   them yourself. Markup goes through html`...` / put() from markup.js, so every value from the server is escaped. */
(() => {
  const el = id => document.getElementById(id);
  const LEVEL = ["0 · reads", "1 · changes, Undo where it can", "2 · asks you each time"];
  const RUN = {done: "✅ done", blocked: "🙋 needs you", failed: "❌ couldn't finish"};
  function at(t) {
    const d = new Date(t * 1000);
    return d.toLocaleDateString([], {day: "numeric", month: "short"}) + " " + d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
  }
  function item(s) {
    const meta = [s.when, s.paused ? "⏸ paused" : `next: ${s.next_text}`, s.waiting && "waiting - another task is going on",
                  s.source === "phone" && "made from the phone", s.runs.length && `last: ${RUN[s.runs[0].status] || s.runs[0].status}`]
      .filter(Boolean).join(" · ");
    return html`<details class="row" data-sched="${s.id}">
      <summary><span class="when">#${s.id}</span><span class="goal">${s.request}</span>
        <span class="st ${s.paused ? "stopped" : s.waiting ? "waiting" : "running"}">${s.paused ? "paused" : s.waiting ? "waiting" : "on"}</span>
        <span class="meta">${meta}</span></summary>
      <div class="body">
        <span class="k">Steps and their risk levels</span>
        <ol>${s.steps.map(st => html`<li>${st.text} <small>level ${LEVEL[st.level] || st.level}</small></li>`)}</ol>
        <span class="k">Last runs</span>
        ${s.runs.length ? html`<ul>${s.runs.map(r => html`<li>${at(r.at)} - ${RUN[r.status] || r.status}${r.late ? " (late)" : ""}
            ${r.task ? html` · <a href="#t:${r.task}" data-task="t:${r.task}">the task</a>` : ""}<small>${r.said}</small></li>`)}</ul>`
          : html`<div class="empty">Not run yet.</div>`}
        <div class="acts">${s.actions.map(a => html`<button data-chat="${a.say}">${a.label}</button>`)}</div>
      </div></details>`;
  }
  async function loadSchedules() {
    let j;
    try { j = await (await fetch("/api/schedules", {cache: "no-store"})).json(); } catch (e) { return; }
    const open = new Set([...document.querySelectorAll("details[data-sched][open]")].map(d => d.dataset.sched));
    el("sched").hidden = !j.schedules.length;
    put(el("schedules"), j.schedules.map(item));
    open.forEach(id => { const d = document.querySelector(`details[data-sched="${CSS.escape(id)}"]`); if (d) d.open = true; });
  }
  document.addEventListener("click", e => {          // "the task": open that run's row in the list below
    const a = e.target.closest("a[data-task]"); if (!a) return;
    const row = document.querySelector(`details.row[data-id="${CSS.escape(a.dataset.task)}"]`);
    if (row) { e.preventDefault(); row.open = true; row.scrollIntoView({block: "center"}); }
  });
  if (window.EventSource) new EventSource("/api/stream").addEventListener("schedule", loadSchedules);
  setInterval(loadSchedules, 30000);                 // the next-run times move on
  loadSchedules();
})();
