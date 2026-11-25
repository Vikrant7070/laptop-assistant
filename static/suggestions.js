/* ⚙ Senses → 🗓 routine suggestions (manager plan Phase 8). Its own endpoint, saved as you change it:
   GET/POST /api/suggestions {on, max, quiet, snooze, allow}. */
(() => {
  const q = s => document.querySelector(s);
  const send = async body => {
    const r = await fetch("/api/suggestions", {method: body ? "POST" : "GET",
      headers: {"Content-Type": "application/json"}, body: body ? JSON.stringify(body) : undefined});
    const j = await r.json().catch(() => ({error: "Server didn't answer."}));
    if (!r.ok) { q("#routineNote").textContent = j.error || r.statusText; return null; }
    return j;
  };
  const show = j => {
    if (!j) return;
    q("#routineOn").checked = !!j.on; q("#routineMax").value = j.max; q("#routineQuiet").value = j.quiet || "";
    const found = (j.routines || []).map(r => `${r.name} (${r.time}: ${r.chain})`).join("; ");
    const until = j.snoozed_until ? ` Snoozed until ${new Date(j.snoozed_until * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}.` : "";
    q("#routineNote").textContent = `Offered today: ${j.shown_today}.${until}` + (found ? ` Routines found: ${found}.` : " No routines found yet.");
    const box = q("#routineNever"); box.replaceChildren();
    if (j.never.length) {
      box.append("Not suggested (you said so): ");
      for (const n of j.never) {
        const b = el("button", {type: "button", textContent: `${n.name} ✕`, title: "Allow it again"});
        b.style.cssText = "font-size:12px;padding:1px 6px;margin:0 4px";
        b.onclick = async () => show(await send({allow: n.id}));
        box.append(b);
      }
    }
  };
  const load = async () => { if (q("#routineBox")) show(await send()); };
  addEventListener("DOMContentLoaded", () => {
    if (!q("#routineBox")) return;
    q("#routineOn").onchange = async e => show(await send({on: e.target.checked}));
    q("#routineMax").onchange = async e => show(await send({max: +e.target.value || 0}));
    q("#routineQuiet").onchange = async e => show(await send({quiet: e.target.value.trim()}));
    q("#routineSnooze").onclick = async () => show(await send({snooze: 120}));
    document.querySelectorAll('[data-tab="senses"]').forEach(n => n.addEventListener("click", load));
    load();
  });
})();
