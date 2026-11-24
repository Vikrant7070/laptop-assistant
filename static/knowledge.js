/* The Knowledge page (/knowledge): what Nova knows about you, her lessons, learned sites and waiting questions.
   Markup goes through html`...` / put() from markup.js, so every value from the server is escaped. */
const $ = id => document.getElementById(id);
let flash;
function say(t) { $("msg").textContent = t; clearTimeout(flash); flash = setTimeout(() => $("msg").textContent = "", 4000); }
async function send(body) {
  const r = await fetch("/api/knowledge", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const j = await r.json(); say(j.message || j.error || ""); load();
}
const SOURCE = {auto: " · noticed by herself", user: " · added by you", starter: " · starter lesson"};
async function load() {
  const k = await (await fetch("/api/knowledge")).json();
  put($("profile"), [...k.one, ...k.many].map(f => {
    const one = k.one.includes(f), vals = one ? (k.profile[f] ? [k.profile[f]] : []) : k.profile[f];
    const chips = vals.map(v => html`<span class="chip">${v}<button title="Remove" aria-label="Remove ${v}" data-f="${f}" data-v="${v}">×</button></span>`);
    return html`<div class="field"><div class="name">${k.labels[f]}</div><div class="chips">${chips}
      <form class="add" data-f="${f}"><input placeholder="${one && vals.length ? "Change…" : "Add…"}" aria-label="Add to ${k.labels[f]}"><button>Add</button></form></div></div>`;
  }));
  put($("lessons"), k.lessons.length ? k.lessons.map(l => html`<div class="lesson">
      <div class="what"><span class="tag ${l.kind}">${l.kind}</span>${l.lesson}</div>
      <button class="del" title="Delete this lesson" aria-label="Delete this lesson" data-id="${+l.id}">×</button>
      <div class="meta">${l.situation && `When: ${l.situation} · `}learned ${l.when || ""}${l.hits > 1 && ` · came up ${l.hits} times`}${SOURCE[l.source]}</div>
    </div>`) : html`<div class="empty">No lessons yet. She will write one the first time something goes wrong.</div>`);
  put($("facts"), k.facts.length ? k.facts.map(f => html`<div class="lesson">
      <div class="what">${!f.confirmed && html`<span class="tag improvement">unconfirmed</span>`}${f.text}</div>
      <span>${!f.confirmed && html`<button class="ok" title="This is right" aria-label="Mark this fact as right" data-fact-ok="${+f.id}">✓</button>`}<button class="del" title="Forget this" aria-label="Forget this fact" data-fact="${+f.id}">×</button></span>
      <div class="meta">last used ${f.when}</div>
    </div>`) : html`<div class="empty">Nothing yet. She learns these when a background research job finishes.</div>`);
  put($("questions"), k.questions.length ? k.questions.map(q => html`<div class="lesson">
      <div class="what">${q.urgent && html`<span class="tag mistake">urgent</span>`}${q.text}</div>
      <span>${(q.kind === "lesson" || q.kind === "save_skill") && html`<button class="ok" title="${q.kind === "lesson" ? "Keep this lesson - she uses it from now on" : "Yes, save it"}" aria-label="Yes" data-q-yes="${+q.id}">✓</button>`}<button class="del" title="Don't ask this" aria-label="Don't ask this question" data-q="${+q.id}">×</button></span>
      <div class="meta">Why: ${q.why}${q.asked ? ` · asked ${q.asked} time${q.asked > 1 ? "s" : ""}` : " · not asked yet"}</div>
    </div>`) : html`<div class="empty">No questions right now.</div>`);
  $("where").textContent = "Saved in this file on your laptop: " + k.file + " (learned sites: " + k.facts_file + ", questions: " + k.questions_file + "). When Nova thinks with a cloud brain (Gemini, ChatGPT or Claude), a short summary of this page is sent along with each message so she can use it; with the local brain nothing leaves the laptop.";
}
document.addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.v !== undefined) send({do: "profile_remove", field: b.dataset.f, value: b.dataset.v});
  else if (b.dataset.id) send({do: "lesson_delete", id: +b.dataset.id});
  else if (b.dataset.fact) send({do: "fact_delete", id: +b.dataset.fact});
  else if (b.dataset.factOk) send({do: "fact_confirm", id: +b.dataset.factOk});
  else if (b.dataset.qYes) send({do: "question_yes", id: +b.dataset.qYes});
  else if (b.dataset.q) send({do: "question_drop", id: +b.dataset.q});
});
document.addEventListener("submit", e => {
  e.preventDefault();
  const f = e.target;
  if (f.id === "newlesson") { send({do: "lesson_add", situation: f.situation.value, lesson: f.lesson.value, kind: f.kind.value}); f.reset(); }
  else if (f.dataset.f) { const v = f.querySelector("input").value.trim(); if (v) send({do: "profile_add", field: f.dataset.f, value: v}); }
});
load();
