/* Local first v2, L6: "does it get better?" on the Brain tab (GET /api/brain/stats, nova/brainstats.py).
   Today's line, the 7-day total, the other days folded away, and each climbed task with a link to it on /tasks. */
(function () {
  const box = document.getElementById("brainStats");
  if (!box) return;

  function el(tag, text, attrs) {
    const e = document.createElement(tag);
    if (text) e.textContent = text;
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    return e;
  }

  function climbRow(c) {
    const li = el("li");
    const when = new Date(c.when * 1000);
    li.append(el("span", (c.rescued ? "✅ " : "❌ ") + when.toLocaleDateString([], {weekday: "short"}) + " "
                         + when.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) + " - "));
    li.append(el("a", c.goal || "(task)", {href: c.link, target: "_blank"}));
    li.append(el("span", " - climbed: " + c.why + (c.rescued ? " (rescued" + (c.rescued_by ? " by " + c.rescued_by : "") + ")" : " (not rescued)")));
    return li;
  }

  function show(s) {
    box.replaceChildren();
    box.append(el("p", "📈 Is local first getting better?", {style: "margin:0;font-weight:bold"}));
    box.append(el("div", s.days[0].line));
    box.append(el("div", s.week.line));
    const days = el("details");
    days.append(el("summary", "Day by day"));
    for (const d of s.days.slice(1)) days.append(el("div", d.line));
    box.append(days);
    if (s.climbs.length) {
      const list = el("details");
      list.append(el("summary", `Climbed tasks (${s.climbs.length})`));
      const ul = el("ul", "", {style: "margin:4px 0;padding-left:18px"});
      for (const c of s.climbs) ul.append(climbRow(c));
      list.append(ul);
      box.append(list);
    }
    box.append(el("div", "Cloud calls come from the free-uses meter, which keeps only today (since midnight Pacific).",
                  {style: "opacity:.7;font-size:12px"}));
  }

  async function load() {
    try {
      const r = await fetch("/api/brain/stats");
      if (!r.ok) throw new Error(r.status);
      show(await r.json());
    } catch (e) {
      box.textContent = "📈 Couldn't count today's tasks (" + e.message + ").";
    }
  }

  load();
  setInterval(() => { if (box.offsetParent !== null) load(); }, 60000);   // only while the Brain tab is showing
})();
