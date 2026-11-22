let WAKE = "Hey Nova";      // the chosen character's wake phrase ("Hey Lucy"), from /api/events
const $ = s => document.querySelector(s);
const log = $("#log"), orb = $("#orb"), input = $("#input");
let voiceOn = true, handsFree = false, busy = false, NAME = "Nova";
document.querySelectorAll("#setupTabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#setupTabs button").forEach(x => x.classList.toggle("on", x === b));
  document.querySelectorAll("#pick .pane").forEach(p => p.classList.toggle("on", p.dataset.pane === b.dataset.tab));
});

/* ---------------- 🎙 mic loudness slider + live meter (⚙ Senses) ---------------- */
const MIC_WORDS = {1: "(catches whispers - may wake on room noise)", 3: "(catches soft speech)", 5: "(normal)",
                   7: "(ignores quiet sounds far away)", 9: "(only a clearly loud voice)"};
function showMicLevel() {
  const v = +$("#micLevel").value;
  $("#micLevelNum").textContent = v;
  $("#micLevelWord").textContent = MIC_WORDS[v] || MIC_WORDS[v - 1] || "";
}
let micSend = null;
$("#micLevel").oninput = () => {
  showMicLevel();
  clearTimeout(micSend);       // apply right away (after the drag settles), so the meter's line moves with it
  micSend = setTimeout(() => api("/api/setup", {mic_level: +$("#micLevel").value}).catch(() => {}), 250);
};
// Meter scale: decibels from 0.00003 (silent) to 0.03 RMS (shouting close up) - mic loudness is far from linear.
const micPos = r => Math.max(0, Math.min(100, (20 * Math.log10(Math.max(r, 3e-5) / 3e-5)) / 60 * 100));
setInterval(async () => {
  const open = $("#setup")?.classList.contains("show") && $('#pick .pane[data-pane="senses"]')?.classList.contains("on");
  if (!open) return;
  try {
    const m = await (await fetch("/api/mic_level")).json();
    $("#micBar").style.width = micPos(m.rms) + "%";
    if (m.start) $("#micMark").style.left = micPos(m.start) + "%";
    $("#micBar").style.background = m.live && m.rms > m.start ? "var(--good, #3c9)" : "var(--accent2)";
    $("#micNote").textContent = m.live ? "Talk and watch the bar: when it crosses the line, she starts listening. Applies right away."
      : `Meter paused: ${m.why}. Turn 👂 ears on to see it.`;
  } catch (e) {}
}, 200);

/* ---------------- capabilities panel ---------------- */
const CAPS = [
  ["🩺 Health check", "CPU, memory, disk, battery, network", ["How is my laptop doing?", "Am I connected to Wi-Fi?"]],
  ["🐢 Speed doctor", "Find & close programs hogging resources", ["What's using all my memory?", "Close Chrome"]],
  ["🚀 Launcher", "Open apps, folders and websites", ["Open VS Code", "Open YouTube", "Search the web for cheap flights to Goa"]],
  ["🗂 File helper", "Search, list, organize, recycle", ["Find my resume", "Show the newest files in Downloads", "Organize my Downloads folder"]],
  ["🧹 Cleaner", "Big files, temp files, Recycle Bin", ["Find files bigger than 500 MB", "How much junk is in temp files?"]],
  ["🎚 Controls", "Volume, media, brightness, lock", ["Turn the volume down", "Set brightness to 40%", "Lock my laptop"]],
  ["⚡ Power", "Sleep, restart, shutdown", ["Shut down in a bit", "Cancel the shutdown"]],
  ["🪟 Windows & keyboard", "Switch, close, type, shortcuts, clipboard", ["What windows are open?", "Open Notepad and type a poem about rain", "Show the desktop"]],
  ["📦 Apps", "Install, uninstall, list (winget)", ["Install VLC", "What apps start with Windows?", "Is Python installed?"]],
  ["🎨 Windows settings", "Dark mode, wallpaper, Wi-Fi, any Settings page", ["Turn on dark mode", "Open Bluetooth settings", "Take a screenshot"]],
  ["📄 Read & write files", "Read, write, move, copy", ["Read my todo.txt on the desktop", "Make a shopping list file on my desktop"]],
  ["📝 Notes & reminders", "I'll nudge you when it's time", ["Note: call mom on Sunday", "Remind me about my meeting in 15 minutes"]],
  ["🖥 Screen eyes", "Reads your screen when you ask (turn on in ⚙ Model)", ["What's on my screen?", "Read this error for me", "What am I looking at?"]],
  ["📷 Camera eyes", "Sees you when you ask (turn on in ⚙ Model)", ["How do I look today?", "What am I holding up?"]],
  ["💬 Companion", "Chat, remember you, keep you company", ["Remember that I'm a student and I love cricket", "Tell me a joke", "I'm bored"]],
  ["🛠 Power user", "Any PowerShell command, even as admin", ["What Windows version am I on?", "List my installed Wi-Fi profiles"]],
];
put($("#caps"), html`${CAPS.map(([t, d, ex]) =>
  html`<div class="cap"><b>${t}</b><small>${d}</small>${ex.map(e => html`<button>“${e}”</button>`)}</div>`)}<p class="mood">How often I ask <b>Allow / No</b> first is up to you - change it under ⚙ Model.</p>`);
document.addEventListener("click", e => {
  const b = e.target.closest(".cap button, .chip");
  if (b) send(b.textContent.replace(/[“”]/g, ""));
});

/* ---------------- avatar life ---------------- */
function face(state) { orb.classList.remove("talking", "thinking", "listening", "happy"); if (state) orb.classList.add(state); body3d(state); }
// Keep the 3D Nova on the desktop in sync with the chat.
let last3d = null;
function body3d(state, say) {
  const s = {talking: "talk", thinking: "think", happy: "happy"}[state] || "idle";
  if (s === last3d && !say) return;
  last3d = s;
  fetch("/api/avatar", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({state: s, say})}).catch(() => {});
}
setInterval(() => { orb.classList.add("blink"); setTimeout(() => orb.classList.remove("blink"), 140); }, 3800);
document.addEventListener("mousemove", e => {
  const r = orb.getBoundingClientRect(), dx = (e.clientX - r.x - r.width / 2) / 120, dy = (e.clientY - r.y - r.height / 2) / 120;
  orb.style.setProperty("--ex", Math.max(-3, Math.min(3, dx)) + "px");
  orb.style.setProperty("--ey", Math.max(-3, Math.min(3, dy)) + "px");
});

/* ---------------- voice ----------------
   Nova speaks from the laptop itself (server side, Windows voice), so she talks even when this window is hidden.
   Things said to her with "Hey Nova" show up here too. */
let speakingNow = false, earsNow = {ears: "off", heard: []}, eventsAfter = null;
function voiceEvent(ev) {
  if (ev.listen) { if (!busy) { window.focus(); setTimeout(winVoice, 300); } return; }  // "Hey Nova" → voice typing
  if (ev.me) {
    const said = add("me", "🎙 " + ev.me);
    if (thoughtBox && said.previousElementSibling === thoughtBox) log.insertBefore(said, thoughtBox);  // what you said, then her thoughts
  }
  if (ev.me && Object.keys(ev).length === 1) return;   // just "what you said", sent before she answers
  ev.error ? (closeThoughts(), add("err", ev.error)) : render(ev);   // also: 🔔 notifications, 💡 tips, ✅ finished jobs
}
function speaking(on) { if (on !== speakingNow) { speakingNow = on; if (!busy) face(on ? "talking" : undefined); } }
function wakePhrase(p) { if (p && p !== WAKE) { WAKE = p; earsLabel(); showEars(); } }
function showEars(st) {
  if (st) earsNow = {ears: st.ears ?? st.status, heard: st.heard || []};
  const e = earsNow.ears;
  $("#handsBtn").title = handsFree ? `Listening for “${WAKE}” - ${e}. Click to stop listening completely.` : `Not listening at all. Click to listen for “${WAKE}” again.`;
  const ear = $("#earLine");
  ear.style.display = handsFree ? "block" : "none";
  ear.textContent = handsFree ? `👂 ${e === "listening" ? `Listening for “${WAKE}”` : e}` +
    (earsNow.heard.length ? ` · last: ${earsNow.heard[0].slice(9)}` : "") : "";
}
function voiceState(j) { speaking(j.speaking); wakePhrase(j.wake_phrase); showEars(j); }
async function pollVoice() {      // the fallback when the stream can't be used
  try {
    const j = await (await fetch("/api/events" + (eventsAfter == null ? "" : "?after=" + eventsAfter))).json();
    eventsAfter = j.last;
    j.events.forEach(voiceEvent);
    voiceState(j);
  } catch {}
}

/* ---------------- speech in ---------------- */
// The desktop app's window (WebView2) *claims* to support browser speech recognition, but it has no speech service:
// it asks for the mic, then stops after a second. So in the desktop app always use Windows voice typing (Win+H).
const inDesktopApp = "pywebview" in window || /WebView2|pywebview/i.test(navigator.userAgent) || location.hostname === "127.0.0.1";
const SR = inDesktopApp ? null : (window.SpeechRecognition || window.webkitSpeechRecognition);
let rec = null, recording = false;
if (SR) {
  rec = new SR(); rec.lang = "en-IN"; rec.interimResults = true;
  rec.onresult = e => { const t = [...e.results].map(r => r[0].transcript).join(""); input.value = t;
    if (e.results[e.results.length - 1].isFinal) { stopRec(); send(t); } };
  rec.onend = stopRec;
  rec.onerror = e => { stopRec(); rec = null; winVoice(); };  // browser speech failed: use Windows voice typing instead
}
// In the desktop window there's no browser speech recognition: use Windows voice typing (Win+H) into the box.
async function winVoice() {
  input.focus();
  await fetch("/api/voice_typing", {method: "POST"});
  $("#mood").textContent = "listening… speak now, then press Enter";
  $("#mic").classList.add("rec"); setTimeout(() => $("#mic").classList.remove("rec"), 8000);
  // Send automatically once you stop talking: when the typed text stops changing for 2.5 s.
  let last = input.value, still = 0;
  const watch = setInterval(() => {
    if (document.activeElement !== input && !input.value) { clearInterval(watch); return; }
    if (input.value && input.value === last) { if (++still >= 5) { clearInterval(watch); send(input.value); } }
    else { still = 0; last = input.value; }
  }, 500);
  setTimeout(() => clearInterval(watch), 60000);
}
function startRec() { fetch("/api/voice_stop", {method: "POST"}); if (!rec) return winVoice(); if (busy) return; recording = true; $("#mic").classList.add("rec"); face("listening"); $("#mood").textContent = "listening…"; try { rec.start(); } catch {} }
function stopRec() { if (!recording) return; recording = false; $("#mic").classList.remove("rec"); face(); setMood(); try { rec.stop(); } catch {} }
$("#mic").onclick = () => recording ? stopRec() : startRec();

/* ---------------- chat ---------------- */
function add(cls, text) { const d = document.createElement("div"); d.className = "msg " + cls; d.textContent = text; log.append(d); log.scrollTop = 1e9; return d; }
function acts(list) { if (!list?.length) return; const d = document.createElement("div"); d.className = "acts"; put(d, html`${list.map(a => html`<span>✓ ${a}</span>`)}`); log.append(d); }
function typing(on) { $("#typing")?.remove(); if (on) { const d = document.createElement("div"); d.id = "typing"; d.className = "typing"; put(d, html`${NAME} is thinking <i></i><i></i><i></i>`); log.append(d); log.scrollTop = 1e9; } }

/* ---------------- what Nova is thinking ----------------
   Every step she takes while working on a request (and a thinking model's own reasoning) shows up live in a
   "💭 Thinking…" block under your message, and the newest one in the cloud over her head. When she answers, the block
   folds into "💭 Thought for 4.2s · 6 steps" - click it to read it again. Requests by voice get one too. */
let thoughtAfter = null, thoughtBox = null, cloudTimer = null;
const MINOR = /^(→|⏱|🧰)/;
function serverWorking(on) {
  serverBusy = on; refreshSend();
  if (!on && thoughtBox?.classList.contains("live") && !busy) closeThoughts();   // e.g. a voice request ended
}
async function pollThoughts() {      // the fallback when the stream can't be used
  try {
    const j = await (await fetch("/api/thoughts" + (thoughtAfter == null ? "" : "?after=" + thoughtAfter))).json();
    if (thoughtAfter == null) { thoughtAfter = j.last; serverWorking(j.busy); return; }   // just opened: start from now
    for (const t of j.thoughts) { thoughtAfter = t.id; addThought(t); }
    serverWorking(j.busy);
  } catch {}
}
function addThought(t) {
  if (!thoughtBox || thoughtBox.dataset.turn != t.turn) openThoughts(t);
  const li = document.createElement("li");
  if (MINOR.test(t.text)) li.className = "minor";
  put(li, html`<time>+${(t.t - thoughtBox.dataset.t0).toFixed(1)}s</time><span>${t.text}</span>`);
  thoughtBox.querySelector("ol").append(li);
  thoughtBox.dataset.n = +thoughtBox.dataset.n + 1;
  thoughtBox.dataset.t1 = t.t;
  if (thoughtBox.classList.contains("live")) {
    thoughtBox.querySelector("summary").textContent = `💭 ${NAME} is thinking… (${thoughtBox.dataset.n})`;
    if (!MINOR.test(t.text)) showCloud(t.text);
  }
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 120;
  if (nearBottom) log.scrollTop = 1e9;
}
function openThoughts(t) {
  closeThoughts();
  const d = document.createElement("details");
  d.className = "thoughts live"; d.open = true;
  d.dataset.turn = t.turn; d.dataset.t0 = t.t; d.dataset.t1 = t.t; d.dataset.n = 0;
  d.append(el("summary"), el("ol"));
  const typingEl = $("#typing");
  typingEl ? log.insertBefore(d, typingEl) : log.append(d);
  thoughtBox = d;
}
function closeThoughts() {
  hideCloud();
  const d = thoughtBox;
  if (!d || !d.classList.contains("live")) return;
  d.classList.remove("live"); d.open = false;
  const secs = (d.dataset.t1 - d.dataset.t0).toFixed(1), n = +d.dataset.n;
  d.querySelector("summary").textContent = `💭 Thought for ${secs}s · ${n} step${n === 1 ? "" : "s"}`;
}
function showCloud(text) {
  const c = $("#cloud"); c.textContent = text.length > 110 ? text.slice(0, 107) + "…" : text; c.classList.add("show");
  clearTimeout(cloudTimer); cloudTimer = setTimeout(hideCloud, 12000);
}
function hideCloud() { clearTimeout(cloudTimer); $("#cloud").classList.remove("show"); }

async function api(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
  const j = await r.json().catch(() => ({error: "Server didn't answer."}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function render(j) {
  closeThoughts();
  acts(j.activity);
  for (const b64 of j.images || []) {  // what Nova saw through the camera
    const im = document.createElement("img"); im.className = "seen"; im.src = "data:image/jpeg;base64," + b64;
    im.title = "What Nova saw (not saved) - click to enlarge"; im.onclick = () => im.classList.toggle("big");
    log.append(im); log.scrollTop = 1e9;
  }
  if (j.reply) {
    const m = add("nova", j.reply);
    if (j.kind) m.classList.add(j.kind);                       // notification / suggestion / job
    else { last3d = null; body3d("happy"); }                    // (her bubble follows her voice: server side)
  }
  if (j.offer) showOffer(j.offer);
  if (j.pending) showConfirm(j.pending);
}
// A background job is done: "Show it" brings its window (Gmail, Notepad) to the front.
function showOffer(o) {
  const d = document.createElement("div"); d.className = "confirm";
  put(d, html`<button class="yes">${o.label || "Show it"}</button><button class="no">Not now</button>`);
  const go = async show => { d.querySelectorAll("button").forEach(b => b.disabled = true); d.style.opacity = .6;
    if (o.help && show) { add("me", o.label || "Help me"); return handle(api("/api/help", {yes: true, id: o.id})); }   // 🆘 she works on it
    try { const j = await api(o.help ? "/api/help" : "/api/offer", o.help ? {yes: false, id: o.id} : {show, id: o.id}); add("nova", j.reply); }
    catch (e) { add("err", e.message); } };
  d.querySelector(".yes").onclick = () => go(true); d.querySelector(".no").onclick = () => go(false);
  log.append(d); log.scrollTop = 1e9;
}

async function handle(promise) {
  busy = true; refreshSend(); face("thinking"); typing(true); $("#mood").textContent = "thinking…";
  try {
    const j = await promise;
    await pollThoughts();          // her last thoughts belong above the answer
    typing(false); render(j);
    face("happy"); setTimeout(() => face(), 1500);
    busy = false; refreshSend(); setMood(); loadStatus();
  } catch (e) {
    await pollThoughts(); closeThoughts();
    typing(false); add("err", e.message); face(); busy = false; refreshSend(); setMood();
    if (/Ollama|not found/i.test(e.message)) loadStatus().then(openSetup);
  }
}

function send(text) {
  text = (text || "").trim(); if (!text || busy) return;
  document.querySelectorAll(".confirm button").forEach(b => b.disabled = true);
  add("me", text); input.value = "";
  handle(api("/api/chat", {text}));
}
$("#tipBtn").onclick = () => {
  if (busy) return;
  add("me", "💡 Any suggestions for what I'm doing?");
  handle(api("/api/suggest").then(j => (j.shown ? {activity: j.activity} : j)));   // a tip arrives as an event
};
$("#f").onsubmit = e => { e.preventDefault(); stopMode ? stopNova() : send(input.value); };

/* ---------------- the Stop switch ----------------
   While Nova is working (typed or by voice) the send button turns into a red ■ Stop. Esc does the same. */
let stopMode = false, serverBusy = false;
function refreshSend() {
  const on = busy || serverBusy, b = $("#sendBtn");
  if (on === stopMode) return;
  stopMode = on; b.classList.toggle("stop", on);
  b.textContent = on ? "■" : "➤"; b.title = on ? `Stop ${NAME} (Esc)` : "Send";
}
async function stopNova() {
  $("#sendBtn").title = "Stopping…";
  try { await api("/api/stop"); } catch {}
}
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && stopMode && !document.querySelector(".modal.show")) { e.preventDefault(); stopNova(); }
});

function showConfirm(items) {
  const d = document.createElement("div"); d.className = "confirm";
  put(d, html`<b>May I?</b> ${items.map(i => i.label).join(", ")}<code>${items.map(i => JSON.stringify(i.input)).join("\n")}</code>
    <button class="yes">Allow</button><button class="no">No thanks</button>`);
  const go = ok => { d.querySelectorAll("button").forEach(b => b.disabled = true); d.style.opacity = .6;
    d.querySelector("b").textContent = ok ? "Allowed ✓" : "Declined ✗"; handle(api("/api/confirm", {approved: ok})); };
  d.querySelector(".yes").onclick = () => go(true); d.querySelector(".no").onclick = () => go(false);
  log.append(d); log.scrollTop = 1e9;
}
/* ---------------- header buttons ---------------- */
$("#voiceBtn").onclick = async e => { voiceOn = !voiceOn; e.target.classList.toggle("on", voiceOn); await api("/api/setup", {voice: voiceOn}); };
function earsLabel() { $("#handsBtn").textContent = handsFree ? `👂 ${WAKE}` : "🙉 Ears off"; }
$("#handsBtn").onclick = async e => {
  handsFree = !handsFree; e.target.classList.toggle("on", handsFree); showEars(); await api("/api/setup", {wake: handsFree});
  earsLabel();
  add("nova", handsFree ? `I'm listening for “${WAKE}” now. Say it, wait for my bubble, then tell me what you need!`
    : `🙉 My ears are off - I'm not listening at all now, not even for “${WAKE}”. Click 👂 to turn them back on (the 🎤 button still works when you want to talk).`);
};
$("#resetBtn").onclick = async () => { await api("/api/reset"); log.replaceChildren(); thoughtBox = null; hideCloud(); greet(); };
$("#keyBtn").onclick = async () => openSetup(await loadStatus());
$("#quitBtn").onclick = async () => {
  if (!confirm("Shut Nova down completely? She stops listening, 3D Nova and the tray icon go away. Start her again with \"Start Nova.bat\".")) return;
  try { await api("/api/quit", {}); } catch (e) {}
  add("nova", "Bye! Shutting down completely 👋");
};
$("#setup").onclick = e => { if (e.target.id === "setup" && window.__ready) $("#setup").classList.remove("show"); };
/* ---- brain choice: local Ollama model, or a cloud model with the user's own API key ---- */
let st = null;
const KEY_LINKS = {google: "https://aistudio.google.com/app/apikey", openai: "https://platform.openai.com/api-keys",
                   anthropic: "https://platform.claude.com/settings/keys"};
const PRIVACY = "Our chat (and window titles) goes to this company. Screenshots and camera photos never do - they stay on the local model.";
function fillCloud(ids, sel) {
  put($("#cloudModel"), ids.length ? ids.map(m => html`<option ${m === sel ? "selected" : ""}>${m}</option>`)
    : html`<option value="">Save a key to see the models</option>`);
}
function keyPlaceholder(p) {
  $("#apiKey").value = "";
  $("#apiKey").placeholder = st.keys[p] ? "Key saved ✓ (paste a new one to replace it)" : `Paste your ${st.providers[p]} API key`;
  $("#forgetKey").style.display = st.keys[p] ? "inline" : "none";
}
async function loadCloudModels(p, sel) {
  $("#cloudNote").textContent = "Loading models…";
  try {
    const j = await (await fetch("/api/cloud/models?provider=" + p)).json();
    if (j.error) throw new Error(j.error);
    if ($("#provider").value !== p) return;
    fillCloud(sel && !j.models.includes(sel) ? [sel, ...j.models] : j.models, sel || j.suggested);
    $("#cloudNote").textContent = PRIVACY;
  } catch (e) { if ($("#provider").value === p) $("#cloudNote").textContent = e.message; }
}
function showProvider() {
  const p = $("#provider").value, onCloud = p !== "ollama";
  $("#cloudBox").style.display = onCloud ? "block" : "none";
  put($("#localLabel"), onCloud
    ? html`<b>Backup brain</b> - this local model takes over whenever the cloud fails (no internet, key or credit problem):`
    : "Pick an installed Ollama model. Everything stays on your laptop.");
  if (!onCloud) return;
  $("#keyLink").href = KEY_LINKS[p];
  keyPlaceholder(p);
  const saved = st.cloud_models[p] || st.cloud_defaults[p] || "";
  fillCloud(saved ? [saved] : [], saved);
  $("#cloudNote").textContent = st.keys[p] ? "" : PRIVACY;
  if (st.keys[p]) loadCloudModels(p, saved);
}
$("#provider").onchange = () => { $("#keyErr").textContent = ""; showProvider(); };
$("#saveKey").onclick = async () => {
  const p = $("#provider").value, key = $("#apiKey").value.trim();
  if (!key) { $("#keyErr").textContent = "Paste a key first."; return; }
  $("#keyErr").textContent = ""; $("#cloudNote").textContent = "Checking the key…"; $("#saveKey").disabled = true;
  try {
    const j = await api("/api/cloud/key", {provider: p, key});
    st.keys[p] = true; keyPlaceholder(p);
    const want = st.cloud_models[p] || st.cloud_defaults[p];
    fillCloud(j.models, want && j.models.includes(want) ? want : j.suggested);
    $("#cloudNote").textContent = "✓ Key works. " + PRIVACY;
  } catch (e) { $("#cloudNote").textContent = ""; $("#keyErr").textContent = e.message; }
  $("#saveKey").disabled = false;
};
$("#forgetKey").onclick = async e => {
  e.preventDefault();
  const p = $("#provider").value;
  await api("/api/cloud/key", {provider: p, key: ""});
  st.keys[p] = false; keyPlaceholder(p); fillCloud([], ""); $("#cloudNote").textContent = "Key forgotten.";
};
function fillRouting(s) {
  const r = s.routing || {}, names = s.models || [];
  $("#autoRoute").checked = r.auto_route !== false; $("#heavySteps").value = r.heavy_steps || 6;
  for (const [id, key] of [["coderModel", "coder_model"], ["deepModel", "deep_model"], ["heavyModel", "heavy_model"]]) {
    const want = r[key] || "", list = names.includes(want) || !want ? names : [want, ...names];   // keep a chosen model that isn't installed visible
    put($("#" + id), list.map(m => html`<option ${m === want ? "selected" : ""}>${m}</option>`));
  }
  $("#routeBox").style.opacity = $("#autoRoute").checked ? 1 : .5;
}
$("#autoRoute").onchange = () => { $("#routeBox").style.opacity = $("#autoRoute").checked ? 1 : .5; };
function openSetup(s) {
  st = s;
  $("#keyErr").textContent = "";
  $("#noOllama").style.display = s.ollama ? "none" : "block";
  $("#pick").style.display = "block";
  $("#pullCmd").textContent = "ollama pull " + s.default_model;
  $("#provider").value = s.provider || "ollama"; showProvider();
  if (!s.ollama) put($("#model"), html`<option value="">Ollama isn't running - no local brain right now</option>`);
  if (s.ollama) {
    put($("#model"), s.models.length ? s.models.map(m => html`<option ${m === s.model || m === s.model + ":latest" ? "selected" : ""}>${m}</option>`)
      : html`<option value="">No models yet - run: ollama pull ${s.default_model}</option>`);
    $("#toolNote").textContent = s.ready ? (s.tools ? "✓ This model supports tool calling." : "This model has no native tool calling - I'll use a text-based fallback (works, but less reliably).") : "";
    $("#think").checked = s.think; $("#autostart").checked = s.autostart; $("#camera").checked = s.camera; $("#screen").checked = s.screen;
    $("#suggest").checked = !!s.suggest; $("#notifications").checked = !!s.notifications; $("#notifSleep").checked = !!s.notif_sleep; $("#deepSleep").checked = s.deep_sleep !== false; $("#listenPause").value = s.listen_pause ?? 0.7; $("#listenMax").value = s.listen_max ?? 10; $("#micLevel").value = s.mic_level ?? 5; showMicLevel(); $("#phoneCalls").checked = s.phone_calls !== false; $("#phoneQuiet").checked = s.phone_quiet !== false;$("#phoneCloud").checked = !!s.phone_notes_cloud; $("#emailCloud").checked = !!s.email_cloud;
    $("#errorHelp").checked = s.error_help !== false; $("#bargeIn").checked = s.barge_in !== false; $("#presence").checked = !!s.presence; $("#presenceLock").checked = !!s.presence_lock;
    put($("#visionNote"), s.vision_model ? html`Eyes: <b>${s.vision_model}</b> ✓`
      : html`She needs a vision model to see: run <code>ollama pull qwen3-vl:8b</code>`);
    document.querySelectorAll("[name=aut]").forEach(r => r.checked = r.value === s.autonomy);
  }
  fillRouting(s); $("#avatarRoam").value = s.avatar_roam || "corner";
  put($("#character"), (s.characters || []).map(c => html`<option value="${c.id}">${c.label}</option>`));
  $("#character").value = s.character || "nova";
  $("#character").dataset.was = $("#character").value;   // Save sends her only when you picked another one
  $("#reasoning").value = s.reasoning || "auto";
  $("#sounds").checked = s.reaction_sounds !== false; $("#powerOk").checked = !!s.power_ok;
  $("#setup").classList.add("show");
  loadPhone(); loadGmail(); loadMobile(); loadMcp(); loadForms(); loadWorkers();
}
/* ---------------- 📱 phone + 🔌 connectors (their own Save buttons) ---------------- */
function showPhone(p) {
  const n = $("#phoneNote");
  $("#phoneToken").placeholder = p.configured ? `Bot @${p.bot || "?"} saved ✓ (paste a new token to replace it)` : "Paste your bot token (123456789:ABC...)";
  put(n, html`${!p.configured ? "No bot yet."
    : p.paired ? html`✓ Paired with <b>${p.paired_name || "your account"}</b> via <b>@${p.bot}</b>. ${p.running ? "Listening." : "Not listening."}`
    : p.pair_code ? html`Now open <b>@${p.bot}</b> in Telegram and send it this code: <b style="font-size:18px;letter-spacing:2px">${p.pair_code}</b> (works for 10 minutes)`
    : `The pairing code expired - click "New pairing code".`}${p.note && html`<br><span style="color:var(--warn)">${p.note}</span>`}`);
  $("#phoneNewCode").style.display = p.configured && !p.paired ? "" : "none";
  $("#phoneUnpair").style.display = p.paired ? "" : "none";
  $("#phoneForget").style.display = p.configured ? "" : "none";
  $("#phoneForward").checked = p.forward !== false;
}
async function loadPhone() { try { showPhone(await (await fetch("/api/phone")).json()); } catch (e) {} }
async function phoneDo(body) {
  try { showPhone(await api("/api/phone", body)); $("#phoneToken").value = ""; }
  catch (e) { put($("#phoneNote"), html`<span style="color:var(--bad)">${e.message}</span>`); }
}
$("#phoneSave").onclick = () => { const t = $("#phoneToken").value.trim(); if (t) { $("#phoneNote").textContent = "Checking the token with Telegram…"; phoneDo({token: t}); } };
$("#phoneNewCode").onclick = e => { e.preventDefault(); phoneDo({new_code: true}); };
$("#phoneUnpair").onclick = e => { e.preventDefault(); phoneDo({unpair: true}); };
$("#phoneForget").onclick = e => { e.preventDefault(); phoneDo({token: ""}); };
$("#phoneForward").onchange = () => phoneDo({forward: $("#phoneForward").checked});
/* ---------------- 📧 Gmail ---------------- */
let gmTimer = null;
function showGmail(g) {
  put($("#gmNote"), html`${g.error && html`<span style="color:var(--bad)">${g.error}</span><br>`}${
    g.connected ? html`✓ Connected${g.email && html` as <b>${g.email}</b>`}. Read-only.`
     : g.connecting ? "Finish signing in on Google's page in your browser…"
     : g.configured ? "Saved. Click Connect Gmail." : "Not set up yet."}`);
  $("#gmClient").placeholder = g.configured ? "Client saved ✓ (paste a new ID to replace it)" : "Google client ID (….apps.googleusercontent.com)";
  $("#gmConnect").style.display = g.configured && !g.connected && !g.connecting ? "" : "none";
  $("#gmDisconnect").style.display = g.connected ? "" : "none";
  clearTimeout(gmTimer);
  if (g.connecting) gmTimer = setTimeout(loadGmail, 2000);
}
async function loadGmail() { try { showGmail(await (await fetch("/api/gmail")).json()); } catch (e) {} }
async function gmailDo(body) {
  try { showGmail(await api("/api/gmail", body)); $("#gmSecret").value = ""; $("#gmClient").value = ""; }
  catch (e) { put($("#gmNote"), html`<span style="color:var(--bad)">${e.message}</span>`); }
}
$("#gmSave").onclick = () => { const i = $("#gmClient").value.trim(), s = $("#gmSecret").value.trim(); if (i && s) gmailDo({client_id: i, client_secret: s}); };
$("#gmConnect").onclick = () => gmailDo({connect: true});
$("#gmDisconnect").onclick = e => { e.preventDefault(); gmailDo({disconnect: true}); };
/* ---------------- 📲 Nova on iPhone ---------------- */
let mobTimer = null;
function showMobile(m) {
  $("#mobOn").checked = !!m.on;
  $("#mobBody").style.display = m.on ? "" : "none";
  put($("#mobUrl"), m.url
    ? html`Open <b>${m.url}</b> in Safari on your iPhone (signed in to the same Tailscale account), then Share → Add to Home Screen.`
    : html`Tailscale isn't set up on this laptop yet. Install it, then run <code>${m.serve}</code> once - the address to open on your phone appears here.`);
  $("#mobCodeShow").textContent = m.code ? m.code.replace(/(...)(...)/, "$1 $2") : "";
  put($("#mobDevices"), (m.devices || []).length
    ? m.devices.map((d, i) => html`${i > 0 && html`<br>`}📱 <b>${d.name}</b> <a href="#" data-dev="${d.id}" style="font-size:13px">Remove</a>`)
    : "None yet.");
  document.querySelectorAll("#mobDevices a[data-dev]").forEach(a => a.onclick = e => { e.preventDefault(); mobDo({remove: a.dataset.dev}); });
  clearTimeout(mobTimer);
  if (m.on && m.code) mobTimer = setTimeout(loadMobile, 4000);       // the code disappears once a phone has used it
}
async function loadMobile() { try { showMobile(await (await fetch("/api/mobile")).json()); } catch (e) {} }
async function mobDo(body) {
  try { showMobile(await api("/api/mobile", body)); $("#mobNote").textContent = ""; }
  catch (e) { put($("#mobNote"), html`<span style="color:var(--bad)">${e.message}</span>`); }
}
$("#mobOn").onchange = () => mobDo({on: $("#mobOn").checked});
$("#mobCode").onclick = () => mobDo({new_code: true});
function showMcp(j) {
  const icon = {connected: "🟢", failed: "🔴", starting: "🟡", disabled: "⚪", stopped: "⚪"};
  put($("#mcpStatus"), html`${j.error && html`<span style="color:var(--bad)">${j.error}</span><br>`}${
    j.servers.length ? j.servers.map((s, i) => html`${i > 0 && html`<br>`}${icon[s.status] || "•"} <b>${s.name}</b> - ${s.status}${
      s.tools.length > 0 && html`: ${s.tools.length} tool${s.tools.length === 1 ? "" : "s"} (${s.tools.slice(0, 6).join(", ")}${s.tools.length > 6 ? "…" : ""})`}${
      s.trusted && " · trusted"}${s.error && html`<br><span style="color:var(--bad)">${s.error}</span>`}`) : "No connectors yet."}`);
}
function showForms(j) {
  put($("#formFields"), j.fields.map(f => html`<label class="mood" style="display:flex;flex-direction:column;gap:2px">${f.label}
    <input data-k="${f.key}" value="${f.value}" autocomplete="off" spellcheck="false" ${f.key === "birth_date" && html`placeholder="e.g. 15/08/1998"`}></label>`));
}
async function loadForms() { try { showForms(await (await fetch("/api/forms")).json()); } catch (e) {} }
$("#formsSave").onclick = async () => {
  const details = {}; document.querySelectorAll("#formFields input").forEach(i => details[i.dataset.k] = i.value);
  try { showForms(await api("/api/forms", {details})); $("#formsNote").textContent = "Saved on this laptop ✓"; }
  catch (e) { put($("#formsNote"), html`<span style="color:var(--bad)">${e.message}</span>`); }
};
/* ---------------- 🧑‍💻 workers (workers.py): coding + research ---------------- */
function showWorkers(w) {
  const c = w.config, oc = c.opencode, cc = c.claude;
  const line = (name, ok, extra) => html`<div>${ok ? "✅" : "⚠️"} <b>${name}</b> - ${extra}</div>`;
  const claudeState = !w.exe.claude ? "not found on this laptop" : w.claude_signed_in ? "ready, signed in" :
    w.claude_signed_in === false ? "installed but not signed in" : "installed (couldn't check the sign-in)";
  put($("#wkStatus"), html`${line("OpenCode + DeepSeek", !!w.exe.opencode, w.exe.opencode ? "ready" : "not found on this laptop")}${
    line("Claude Code", !!(w.exe.claude && w.claude_signed_in), claudeState)}${
    w.sign_in_help && html`<div style="margin-top:4px">${w.sign_in_help}</div>`}<div style="margin-top:4px">Claude Code spent today: $${
    (+w.spent_today || 0).toFixed(2)} of $${(+c.daily_budget_usd || 0).toFixed(2)}${
    w.running.length > 0 && ` · working now: ${w.running.length}`}${w.waiting > 0 && ` · waiting in line: ${w.waiting}`}</div>`);
  $("#wkOcModel").value = oc.model || ""; $("#wkOcFallbacks").value = (oc.fallback_models || []).join(", ");
  $("#wkOcTimeout").value = oc.timeout_min; $("#wkCcModel").value = cc.model || "";
  $("#wkCcBudget").value = cc.budget_usd; $("#wkDaily").value = c.daily_budget_usd; $("#wkCcTimeout").value = cc.timeout_min;
  const rs = c.research || {};
  $("#wkRsSources").value = rs.max_sources || 3; $("#wkRsCloud").checked = rs.cloud_report !== false;
  const when = t => t ? new Date(t * 1000).toLocaleString([], {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"}) + " · " : "";
  put($("#wkRecent"), w.recent.length > 0 && html`<b>Recent jobs</b>${w.recent.map(r => html`<div>${when(r.started)}#${r.job} ${r.worker} · ${
    r.status}${r.files > 0 && ` · ${r.files} file${r.files > 1 ? "s" : ""}`}${r.sources > 0 && ` · ${r.sources} source${r.sources > 1 ? "s" : ""}`}${r.committed && " · committed"}${
    r.cost > 0 && ` · $${(+r.cost).toFixed(2)}`} - ${r.brief}</div>`)}`);
}
async function loadWorkers(fresh) { try { showWorkers(await (await fetch("/api/workers" + (fresh ? "?fresh=1" : ""))).json()); } catch (e) {} }
$("#wkRecheck").onclick = (e) => { e.preventDefault(); $("#wkNote").textContent = "Checking…"; loadWorkers(true).then(() => $("#wkNote").textContent = ""); };
$("#wkSave").onclick = async () => {
  try {
    showWorkers(await api("/api/workers", {opencode_model: $("#wkOcModel").value, opencode_fallbacks: $("#wkOcFallbacks").value,
      opencode_timeout_min: $("#wkOcTimeout").value, claude_model: $("#wkCcModel").value, claude_budget_usd: $("#wkCcBudget").value,
      daily_budget_usd: $("#wkDaily").value, claude_timeout_min: $("#wkCcTimeout").value,
      research_max_sources: $("#wkRsSources").value, research_cloud_report: $("#wkRsCloud").checked}));
    $("#wkNote").textContent = "Saved ✓";
  } catch (e) { put($("#wkNote"), html`<span style="color:var(--bad)">${e.message}</span>`); }
};
async function loadMcp() { try { const j = await (await fetch("/api/mcp")).json(); $("#mcpCfg").value = j.config; showMcp(j); } catch (e) {} }
$("#mcpSave").onclick = async () => {
  $("#mcpStatus").textContent = "Connecting… (a server that npx has to download can take ~30 s the first time)";
  try { const j = await api("/api/mcp", {config: $("#mcpCfg").value}); $("#mcpCfg").value = j.config; showMcp(j); }
  catch (e) { put($("#mcpStatus"), html`<span style="color:var(--bad)">${e.message}</span>`); }
};
$("#retry").onclick = async () => { const s = await loadStatus(); s.ollama ? openSetup(s) : ($("#keyErr").textContent = "Still can't reach Ollama on port 11434."); };
$("#saveModel").onclick = async () => {
  const p = $("#provider").value;
  if (p === "ollama" && !$("#model").value) { $("#keyErr").textContent = "Pull a model first, then click Retry."; return; }
  if (p !== "ollama" && !st.keys[p]) { $("#keyErr").textContent = "Save your API key first."; return; }
  if (p !== "ollama" && !$("#cloudModel").value) { $("#keyErr").textContent = "Pick a model."; return; }
  try {
    const s = await api("/api/setup", {model: $("#model").value || undefined, think: $("#think").checked, reasoning: $("#reasoning").value,
      provider: p, cloud_model: p !== "ollama" ? $("#cloudModel").value : undefined,
      autonomy: document.querySelector("[name=aut]:checked")?.value, autostart: $("#autostart").checked,
      camera: $("#camera").checked, screen: $("#screen").checked,
      suggest: $("#suggest").checked, notifications: $("#notifications").checked, notif_sleep: $("#notifSleep").checked, deep_sleep: $("#deepSleep").checked, listen_pause: +$("#listenPause").value || 0.7, listen_max: +$("#listenMax").value || 10, mic_level: +$("#micLevel").value || 5, phone_calls: $("#phoneCalls").checked, phone_quiet: $("#phoneQuiet").checked,phone_notes_cloud: $("#phoneCloud").checked, email_cloud: $("#emailCloud").checked, reaction_sounds: $("#sounds").checked, power_ok: $("#powerOk").checked,
      avatar_roam: $("#avatarRoam").value, character: $("#character").value !== $("#character").dataset.was ? $("#character").value : undefined,
      routing: {auto_route: $("#autoRoute").checked, coder_model: $("#coderModel").value, deep_model: $("#deepModel").value,
        heavy_model: $("#heavyModel").value, heavy_steps: +$("#heavySteps").value || 6},
      error_help: $("#errorHelp").checked, barge_in: $("#bargeIn").checked, presence: $("#presence").checked, presence_lock: $("#presenceLock").checked});
    if (!s.ready) { $("#keyErr").textContent = "That model isn't available."; return; }
    $("#setup").classList.remove("show"); const first = !window.__ready; window.__ready = true; loadStatus(); if (first) greet();
  } catch (e) { $("#keyErr").textContent = e.message; }
};

/* ---------------- status, stats & proactive pulse ---------------- */
let lastStats = null;
function setMood() {
  if (busy || recording) return;
  const s = lastStats, h = new Date().getHours();
  let m = "happy to help ✨";
  if (s?.battery != null && s.battery <= 20 && !s.plugged) m = "feeling sleepy 🔋";
  else if (s?.cpu > 85) m = "working hard 💦";
  else if (h >= 0 && h < 5) m = "it's late… go to sleep? 🌙";
  else if (s?.plugged) m = "charging & cheerful ⚡";
  $("#mood").textContent = m;
  orb.classList.toggle("sleepy", s?.battery != null && s.battery <= 15 && !s.plugged);
}
function bar(id, v, label, invert) {
  $("#" + id + "V").textContent = label; const b = $("#" + id + "B"); b.style.width = (v ?? 0) + "%";
  const bad = invert ? v < 20 : v > 85, warn = invert ? v < 40 : v > 65;
  b.className = bad ? "bad" : warn ? "warn" : "";
}
async function pulse() { try { showPulse(await (await fetch("/api/pulse")).json()); } catch {} }
function showPulse(j) {
  try {
    const s = lastStats = j.stats;
    bar("cpu", s.cpu, s.cpu + "%"); bar("ram", s.ram, s.ram + "%"); bar("disk", s.disk, s.disk + "%");
    if (s.battery == null) bar("bat", 100, "desktop power");
    else bar("bat", s.battery, s.battery + "%" + (s.plugged ? " ⚡" : ""), true);
    $("#upV").textContent = `Awake for ${s.uptime_h} h`;
    setMood();
    for (const line of j.say) { add("nova", line); last3d = null; body3d("happy"); }
  } catch {}
}
async function loadStatus() {
  const s = await (await fetch("/api/status")).json();
  NAME = s.name; $("#nm").textContent = $("#nm2").textContent = NAME; document.title = NAME;
  voiceOn = s.voice; handsFree = s.wake;
  $("#voiceBtn").classList.toggle("on", voiceOn); $("#handsBtn").classList.toggle("on", handsFree); earsLabel(); showEars();
  if (s.voice_name) $("#voiceBtn").title = "Nova's voice: " + s.voice_name;
  $("#keyBtn").title = "Brain: " + (s.brain || "").replace(/^cloud:/, "") + (s.brain !== s.model ? `  (backup: ${s.model})` : "");
  put($("#mem"), s.memories.length ? s.memories.map(m => html`<li>${m}</li>`) : html`<li>Nothing yet - tell me about yourself!</li>`);
  return s;
}
function greet() {
  const h = new Date().getHours();
  const part = h < 5 ? "You're up late" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  const t = `${part}! I'm ${NAME}, I live in your laptop and keep it running smoothly. What can I do for you?`;
  add("nova", t); face("happy"); setTimeout(() => face(), 1500);
}

/* ---------------- one event stream ----------------
   Everything above that changes on its own (her thoughts, voice events, talking, the ears, the pulse) is pushed by
   the server over /api/stream as it happens, so an idle page sends no requests at all. If the stream can't be used,
   the old polling starts instead. */
let polling = false;
function startPolling() {
  if (polling) return; polling = true;
  pollVoice(); pollThoughts();
  setInterval(pollVoice, 1000); setInterval(pollThoughts, 600); setInterval(pulse, 15000); setInterval(loadTask, 2000);
}
function listen() {
  if (!window.EventSource) return startPolling();
  const es = new EventSource("/api/stream?backlog=event");
  let opened = false;
  const on = (kind, fn) => es.addEventListener(kind, e => { try { fn(JSON.parse(e.data)); } catch {} });
  on("state", j => { opened = true; voiceState(j); serverWorking(j.busy); });
  on("event", voiceEvent);
  on("thought", t => { thoughtAfter = t.id; addThought(t); });
  on("busy", j => serverWorking(j.busy));
  on("speak_start", () => speaking(true));
  on("speak_end", () => speaking(false));
  on("ears", showEars);
  on("wake_phrase", j => wakePhrase(j.phrase));
  on("pulse", showPulse);
  on("task", showTask);                  // the live task strip (taskstrip.js)
  // The browser reconnects by itself (and catches up from Last-Event-ID); only a stream that never worked, or that
  // the server refused, falls back to polling.
  es.onerror = () => { if (!opened || es.readyState === EventSource.CLOSED) { es.close(); startPolling(); } };
}

(async () => {
  const s = await loadStatus(); window.__ready = s.ready;
  pulse(); listen();
  if (!s.ready) openSetup(s); else greet();
  input.focus();
})();
