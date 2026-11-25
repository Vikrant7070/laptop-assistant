/* ---------------- 🔒 Privacy: the activity log (nova/core/app/privacy.py) + forgetting form details ----------------
   Uses $, html, put (markup.js) and api (nova.js). The header chip is the visible "recording" indicator. */
(function () {
  const STATES = {recording: "● Logging apps", away: "● Log: you're away", blocked: "● Log: not this window",
                  paused: "⏸ Log paused"};
  const when = t => new Date(t * 1000).toLocaleString([], {weekday: "short", hour: "2-digit", minute: "2-digit"});
  const mins = s => s < 90 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`;

  function chip(st) {
    const c = $("#actChip");
    if (!c) return;
    c.hidden = !st.on;
    c.textContent = st.paused ? STATES.paused : (STATES[st.state] || STATES.recording);
    c.classList.toggle("on", st.on && !st.paused);
  }
  async function poll() {
    try { chip(await (await fetch("/api/activity/state")).json()); } catch (e) {}
  }

  function show(j) {
    chip(j);
    $("#actOn").checked = j.on; $("#actPaused").checked = j.paused; $("#actForms").checked = j.notice_forms;
    $("#actPaused").disabled = $("#actForms").disabled = !j.on;
    $("#actApps").value = (j.blocked_apps || []).join("\n"); $("#actSites").value = (j.blocked_sites || []).join("\n");
    $("#actState").textContent = !j.on ? "Off - nothing is being recorded." :
      `${j.paused ? "Paused" : "On"} · ${j.spans} entries kept on this laptop · each deleted after ${j.keep_days} days.`;
    put($("#actRoutines"), j.routines.length ? j.routines.map(r => html`<div>• ${r.text} <span style="opacity:.7">(${r.days} days)</span></div>`)
                                             : "None yet - a routine is the same steps on at least 3 days at about the same time.");
    put($("#actRecent"), j.recent.length ? j.recent.map(r => html`<div style="display:flex;gap:6px;align-items:baseline;border-bottom:1px solid var(--line);padding:2px 0">
        <span style="min-width:120px">${when(r.start)}</span><span style="min-width:52px">${mins(r.end - r.start)}</span>
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${r.title}"><b>${r.host || r.app}</b> - ${r.title}</span>
        <button type="button" data-del="${r.id}" title="Delete this entry" style="width:auto;padding:1px 7px">🗑</button>
        <button type="button" data-block="${r.host || r.app}" title="Never record this app / site, and delete what was recorded" style="width:auto;padding:1px 7px">🚫</button></div>`)
      : "Nothing recorded.");
  }
  async function load() { try { show(await (await fetch("/api/activity")).json()); } catch (e) {} }
  async function send(body, note) {
    try { show(await api("/api/activity", body)); $("#actNote").textContent = note || ""; }
    catch (e) { put($("#actNote"), html`<span style="color:var(--bad)">${e.message}</span>`); }
  }
  const lines = s => s.split(/\n/).map(x => x.trim()).filter(Boolean);

  document.addEventListener("DOMContentLoaded", () => {
    const tab = document.querySelector('#setupTabs button[data-tab="privacy"]');
    if (!tab) return;
    tab.addEventListener("click", load);
    $("#actOn").onchange = () => send({on: $("#actOn").checked});
    $("#actPaused").onchange = () => send({paused: $("#actPaused").checked});
    $("#actForms").onchange = () => send({notice_forms: $("#actForms").checked});
    $("#actSave").onclick = () => send({blocked_apps: lines($("#actApps").value), blocked_sites: lines($("#actSites").value)}, "Saved ✓");
    $("#actDeleteAll").onclick = () => { if (confirm("Delete everything the activity log recorded? This can't be undone.")) send({delete_all: true}, "Deleted."); };
    $("#actRecent").onclick = e => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.dataset.del) send({delete_ids: [+b.dataset.del]});
      else if (b.dataset.block) send({block_app: b.dataset.block}, `Won't record ${b.dataset.block} again.`);
    };
    $("#actChip").onclick = () => { $("#keyBtn").click(); setTimeout(() => { tab.click(); }, 50); };
    const forget = $("#formsForget");
    if (forget) forget.onclick = async () => {
      if (!confirm("Forget all your saved form details?")) return;
      try { await api("/api/forms/forget", {}); if (typeof loadForms === "function") loadForms(); $("#formsNote").textContent = "Forgotten."; }
      catch (e) { put($("#formsNote"), html`<span style="color:var(--bad)">${e.message}</span>`); }
    };
    poll(); setInterval(poll, 15000);
  });
})();
