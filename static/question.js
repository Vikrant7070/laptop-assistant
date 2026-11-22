/* ---------------- 🙋 the question card (app.asking, manager plan 4-pre e) ----------------
   One renderer for the chat, the task strip and the Tasks page. A card holds 1-2 questions, each with its reason:
     yesno / one  a button per choice - with one question, a tap answers it
     many         checkboxes + Done        text   a text box + Done
   Other… opens a box for your own words; Skip (when she has a guess) lets her choose and say so.
   send(answers) gets {key: value | [values] | {other: text} | {skip: true}} and returns a promise.
   Uses html`` / put() from markup.js (every value escaped). */
function questionCard(card, send) {
  const box = document.createElement("div");
  box.className = "qcard"; box.dataset.id = card.id;
  const qs = card.questions || [];
  const quick = qs.length === 1 && (qs[0].kind === "yesno" || qs[0].kind === "one");
  put(box, html`${card.title ? html`<b class="qt">${card.title}</b>` : ""}${qs.map(q => html`
    <div class="qq" data-key="${q.key}" data-kind="${q.kind}">
      <div class="qtext">🙋 ${q.text}</div>${q.why ? html`<small class="qwhy">Why I ask: ${q.why}</small>` : ""}
      <div class="qopts">${q.kind === "many"
        ? q.choices.map(c => html`<label class="qchk${c.value === q.last ? " last" : ""}"><input type="checkbox" value="${c.value}"> ${c.label}</label>`)
        : q.kind === "text" ? html`<input class="qtextin" maxlength="500" placeholder="Your answer…" aria-label="${q.text}">`
        : q.choices.map(c => html`<button type="button" class="qopt${c.value === q.last ? " last" : ""}" data-v="${c.value}"
            title="${c.value === q.last ? "What you picked last time" : ""}">${c.label}</button>`)}
        ${q.other ? html`<button type="button" class="qother">Other…</button>` : ""}
        ${q.guess != null ? html`<button type="button" class="qskip" title="She uses her best guess and says so">Skip - you choose</button>` : ""}
      </div>
      <div class="qotherbox" hidden><input maxlength="300" placeholder="Your own answer…" aria-label="Your own answer"></div>
    </div>`)}
    ${quick ? "" : html`<button type="button" class="qdone">Done</button>`}<span class="qnote" role="status"></span>`);

  const picked = {};                           // key -> value for yesno / one; {skip:true} / {other:text}
  const note = t => { box.querySelector(".qnote").textContent = t; };
  const close = label => { box.querySelectorAll("button, input").forEach(b => b.disabled = true); box.classList.add("answered"); if (label) note(label); };
  const go = async answers => {
    close("Sending…");
    try { await send(answers); note("Answered ✓"); }
    catch (e) { box.querySelectorAll("button, input").forEach(b => b.disabled = false); box.classList.remove("answered"); note(e.message); }
  };
  const collect = () => {
    const answers = {};
    for (const qe of box.querySelectorAll(".qq")) {
      const key = qe.dataset.key, kind = qe.dataset.kind, other = qe.querySelector(".qotherbox input").value.trim();
      if (picked[key]?.skip) { answers[key] = {skip: true}; continue; }
      if (other && !qe.querySelector(".qotherbox").hidden) { answers[key] = {other}; continue; }
      if (kind === "many") {
        const v = [...qe.querySelectorAll("input[type=checkbox]:checked")].map(i => i.value);
        if (v.length) answers[key] = v;
      } else if (kind === "text") {
        const v = qe.querySelector(".qtextin").value.trim();
        if (v) answers[key] = v;
      } else if (picked[key] !== undefined) answers[key] = picked[key];
    }
    return answers;
  };
  box.addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b || b.disabled) return;
    const qe = b.closest(".qq"), key = qe?.dataset.key;
    if (b.classList.contains("qopt")) {
      picked[key] = b.dataset.v;
      qe.querySelectorAll(".qopt").forEach(x => x.classList.toggle("on", x === b));
      qe.querySelector(".qotherbox").hidden = true;
      if (quick) go({[key]: b.dataset.v});
    } else if (b.classList.contains("qother")) {
      const ob = qe.querySelector(".qotherbox"); ob.hidden = false; ob.querySelector("input").focus();
      delete picked[key];
    } else if (b.classList.contains("qskip")) {
      picked[key] = {skip: true};
      if (quick) go({[key]: {skip: true}});
      else b.classList.add("on");
    } else if (b.classList.contains("qdone")) {
      const answers = collect();
      if (Object.keys(answers).length < qs.length) return note("Answer each question, or skip it.");
      go(answers);
    }
  });
  box.addEventListener("keydown", e => {        // Enter in the Other… box (one question) sends it
    if (e.key !== "Enter" || !e.target.matches("input:not([type=checkbox])")) return;
    e.preventDefault();
    if (quick) { const v = e.target.value.trim(); if (v) go({[qs[0].key]: {other: v}}); }
    else box.querySelector(".qdone").click();
  });
  box.closeCard = close;
  return box;
}
