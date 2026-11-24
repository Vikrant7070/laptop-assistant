/* "What Nova knows about me (from this laptop)" on the Knowledge page (manager plan, Phase 6): each history source
   with its switch and what it reads, its facts (✓ / ×), "forget everything from it", the never list and pause.
   Uses $, say (knowledge.js) and html`...` / put() (markup.js), so every value from the server is escaped. */
async function hsend(body) {
  const r = await fetch("/api/laptop_history", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const j = await r.json(); say(j.message || j.error || ""); hload();
}
async function hload() {
  const h = await (await fetch("/api/laptop_history")).json();
  const top = html`<div class="src"><div class="head"><b>${h.paused ? "⏸ Paused: nothing is read" : "Reading is allowed for the sources switched on"}</b>
      <button data-h-do="${h.paused ? "resume" : "pause"}">${h.paused ? "Resume" : "Pause"}</button>
      <button data-h-do="read_now" ${h.paused || h.reading ? "disabled" : ""}>${h.reading ? "Reading…" : "Read now"}</button></div></div>`;
  put($("laptop"), [top, ...h.sources.map(s => html`<div class="src">
      <div class="head"><b>${s.on ? "🟢" : "⚪"} ${s.name}</b>
        <button data-h-do="${s.on ? "source_off" : "source_on"}" data-h-src="${s.id}">${s.on ? "Switch off" : "Switch on"}</button>
        ${s.facts.length ? html`<button data-h-do="forget" data-h-src="${s.id}" title="Delete every fact and count from this source">Forget everything from it</button>` : ""}</div>
      <div class="meta" style="color:var(--muted);font-size:12.5px">What is read: ${s.what} ${s.found ? "" : "(not found on this laptop)"}
        ${s.last ? ` · last read ${s.last}: ${s.read} entries, ${s.kept} kept` : ""}</div>
      ${s.facts.map(f => html`<div class="lesson">
        <div class="what">${!f.confirmed && html`<span class="tag improvement">unconfirmed</span>`}${f.text}</div>
        <span>${!f.confirmed && html`<button class="ok" title="This is right" aria-label="Mark this fact as right" data-h-ok="${+f.id}">✓</button>`}<button class="del" title="Delete" aria-label="Delete this fact" data-h-del="${+f.id}">×</button></span>
        <div class="meta">from ${s.name} · ${f.when}</div></div>`)}
    </div>`)]);
  put($("never"), h.never.length ? h.never.map(n => html`<span class="chip">${n}<button title="Remove" aria-label="Remove ${n} from the never list" data-h-never="${n}">×</button></span>`)
    : html`<span class="empty">Nothing on the list.</span>`);
}
document.addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.hDo === "forget" && !confirm("Delete every fact and count Nova has from this source? It can't be undone.")) return;
  if (b.dataset.hDo) hsend({do: b.dataset.hDo, source: b.dataset.hSrc || ""});
  else if (b.dataset.hOk) hsend({do: "fact_confirm", id: +b.dataset.hOk});
  else if (b.dataset.hDel) hsend({do: "fact_delete", id: +b.dataset.hDel});
  else if (b.dataset.hNever !== undefined) hsend({do: "never_remove", value: b.dataset.hNever});
});
document.addEventListener("submit", e => {
  if (e.target.id !== "neveradd") return;
  e.preventDefault();
  const v = e.target.querySelector("input").value.trim(); if (v) hsend({do: "never_add", value: v}); e.target.reset();
});
hload();
