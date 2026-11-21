Status: active — 2026-11-19, B1 (barge-in) done; Part A (A1–A9) and B2–B4 not started.

# Nova upgrade plan: new features and improvements

Written on 2026-11-19 from research on GitHub's `ai-chatbot` topic (jarvis, openai-whisper-talk, ai-chatbot-framework,
better-chatbot and ollama-client) compared with Nova's code.

**Who builds what**

- **Part A is for Sonnet.** Each task is self-contained and has a clear spec, files, tests and a "done when" line.
  Build them in the order listed, one commit each.
- **Part B is for Opus.** These are the real-time and concurrency jobs in `voice.py` and the agent loop. A wrong
  guess there breaks listening or hangs Nova. **Sonnet: do not start Part B.** If a Part A task turns out to need a
  change inside `WhisperEars`, `Mouth`, `_agent_loop`, `answer()` or the `lock`, stop and write a note under
  "Handed to Opus" at the bottom of this file instead.

---

## 0. Before you start (both models)

### Read first

| File | Why |
|---|---|
| `~/.claude/projects/…/memory/MEMORY.md` and the notes it links | Hard-won gotchas: Smart App Control blocks unsigned DLLs, 6 GB GPU limits, pywebview, cloud brains, phone remote. |
| `README.md` | What Nova does today, in the user's words. Update it for every feature you add. |
| `nova/core/app.py` (4,100+ lines) | `settings()` ≈59 (defaults dict), `answer()` ≈2369, `agent_loop()` ≈1439, `stop_now()` ≈597, `announce()` ≈2007, `tell_away()` ≈3452, `is_risky()` ≈383, `/api/setup` ≈1784, `task_log()` ≈445, knowledge page routes ≈2708. |
| `nova/actions/tools.py` | How tools are declared (`_tool(...)`). Reminders are at ≈1859 (`set_reminder`, `due_reminders`), research at ≈2081. |
| `nova/brain/toolpick.py` | Keyword groups that choose which tools the small local model sees. **A new tool needs an entry here too**, or the local brain never sees it. |
| `nova/brain/retrieval.py` | Local ONNX bge embedder: `embed(texts)` returns unit vectors, `ready()`. Reuse it; never add a second embedder. |
| `nova/brain/cloud.py` | `PROVIDERS` dict ≈21. Google and OpenAI already use the OpenAI-compatible `/chat/completions` path. |
| `nova/actions/phone.py` | Pattern for secrets (`_protect()` with DPAPI) and atomic `_save()`. |
| `tests/conftest.py` | `brain` fixture = scripted `FakeBrain`. Use it in tests, never a real model. |
| `static/index.html` | The ⚙ settings UI (toggles post to `/api/setup`). |

### Ground rules

- **Branch:** the user has uncommitted work on `feat/device-network`. **Ask the user** whether to commit it first,
  then create `feat/nova-upgrades` from there. Never revert or stash their work, and never commit to `master`.
- **Tests:** `python -m pytest -q` has 861 tests and must stay green after every task. Add tests for every new piece.
- **No new pip packages** unless a task says so. Use `urllib`, Flask, `sqlite3`, `numpy` and PowerShell, which are
  already there. Any new native wheel can be blocked by Smart App Control, so check the memory notes first.
- **Privacy is a feature.** Nothing new leaves the laptop unless the user turns it on in ⚙. Secrets are
  DPAPI-protected in `data/`, and never go into logs, chat, the prompt or test fixtures. Mail and screenshots
  stay local-brain-only, as they are today.
- **Keep the small local brain working.** Every new tool gets a short description, a `toolpick.py` group and at
  least one trigger test. Cloud brains see all tools; the 4B local model sees only the picked ones.
- **Nova's voice and style:** user-facing text is short and plain, in her "desi dost" voice. Write README sections
  the way the existing ones are written.
- **One commit per task.** Message: what changed for the user, plus the Co-Authored-By line.

---

## Part A: tasks for Sonnet

### A1. Any OpenAI-compatible brain (LM Studio, OpenRouter, Groq, a second Ollama box)

**Why:** cheap or fast cloud models, and other local servers, from one extra choice in ⚙ Model.

- `nova/brain/cloud.py`: add a `"custom"` provider whose `base` URL and label come from `data/keys.json`
  (DPAPI-protected like the other keys). The key is optional, since LM Studio needs none. Reuse the existing
  OpenAI-compatible request path. Don't fork it.
- The model list comes from `GET {base}/models`. If that fails, let the user type a model name.
- `static/index.html` ⚙ Model: a fourth option, "Other (OpenAI-compatible)", with Base URL, optional key and
  "Save & load models".
- The backup brain works as it does today: on failure, use local Ollama for 60 s.
- Only accept `http://127.0.0.1`, `http://localhost` or `https://` URLs (a plain-http remote URL would send the
  chat in clear text). Say so in the error.
- **Tests:** fake HTTP server (see `tests/fake_mcp_server.py` for the pattern). Cover the model list, a chat round
  trip with a tool call, the URL check, and the fallback.
- **Done when:** LM Studio on `http://localhost:1234/v1` can be picked and answers with tools.

### A2. Saved, searchable chat history

**Why:** "what did we talk about last Tuesday?" Today history lives in `state["messages"]` and is capped by
`_cap_history()`.

- New `nova/actions/history.py`: SQLite file `data/history.db` with
  `turns(id, ts, via, user_text, reply, tools_used)` and an FTS5 table on `user_text` and `reply`. Write one row per
  finished turn from `after_turn()` in `app.py`. That's a single call, so it's safe; do not touch `answer()`.
- Don't store: screenshots, camera images, mail bodies, or anything `SECRETY` / `_hide_codes()` would hide. Run
  `_hide_codes()` on both texts before saving.
- Tools: `search_history(query, days=30)` and `history_on(date)`. Results are short snippets with dates. If
  `retrieval.ready()`, re-rank the FTS hits by meaning.
- ⚙ toggle "📜 Keep chat history" (on by default), "Delete all history", and auto-delete after N days (default 90).
- A history page at `/history` (copy the `/knowledge` page pattern) with search and an "Export as Markdown" button.
- **Tests:** saving, the secret filter, search, auto-delete, toggle off = nothing written.

### A3. Routines: skills on a schedule

**Why:** "every weekday at 9, check my mail and the laptop's health and tell me." Reminders today are one-shot
(`set_reminder(text, minutes_from_now)`).

- New `nova/actions/routines.py`, stored in `data/routines.json`:
  `{id, name, when: {days: [mon..sun], time: "09:00"} | {every_minutes}, do: {skill: name} | {ask: "text"}, last_run, enabled}`.
- A once-a-minute check fits next to the existing reminder check (`due_reminders()` is polled in app.py ≈4133).
  When due: run the skill or the request **as if the user had asked**, through the same entry point typed requests
  use. If Nova is busy, wait until the lock is free (don't take it yourself; poll `lock.locked()`). If the user is
  away (`user_away()`), send the result with `tell_away()`.
- **Safety:** a routine that would make a risky call (`is_risky`) always asks first, even on Full access, the same
  rule as the phone. If nobody answers in 10 minutes, skip it and log it.
- Tools: `add_routine`, `list_routines`, `remove_routine`, `pause_routine`. The brain turns "every weekday at 9"
  into the `when` dict; validate it in code.
- Missed runs (the laptop was asleep): run once at the next wake if less than 2 hours late, otherwise skip it and
  say so in the welcome-back summary.
- **Tests:** schedule maths (weekdays, DST-free local time), catch-up rule, busy wait, risky = asks.

### A4. Fast web answers through a search API

**Why:** research in off-screen Chrome (`coworker.research`) is slow and breaks easily. Quick facts don't need a
browser.

- New tool `web_search(query, n=5)` in `nova/actions/websearch.py` with backends: **Brave Search API** (key),
  **Tavily** (key), **SearXNG** (URL to the user's own instance). Keys are DPAPI-protected in `data/keys.json`.
- ⚙ Model: "🔎 Quick web search" with backend picker and key. Off, or no key: the tool says so and the brain falls
  back to `research_in_background`, as today.
- Return titles, URLs and snippets only (short). The brain answers from snippets and names its source.
- `toolpick.py`: put `web_search` before `research_in_background` for short factual questions ("who won", "price
  of", "weather in", "what is"). Keep research for "research", "compare" and "find me the best".
- **Tests:** each backend against a fake server, missing key, toolpick routing.

### A5. Talk to Nova from the phone with voice notes

**Why:** typing on the phone is slow. Nova already has Whisper and Kokoro.

- `nova/actions/phone.py`: accept Telegram `voice` messages from the paired account only. Download the `.oga` file
  through the Bot API, convert it with the `ffmpeg` Nova already uses (check; if there is none, use `soundfile`
  if it reads OGG/Opus, otherwise tell the user it's needed), and transcribe with the **existing** Whisper object
  (`whisper` in app.py, through a small function app.py hands to phone.py). Never load a second Whisper model.
- If Whisper is busy listening, queue the clip. Don't fight for the GPU.
- Reply: text as today. Optional ⚙ switch "🔊 Reply with voice notes": synth with `NeuralVoice.synth`, encode to
  OGG/Opus and `sendVoice`.
- Show "🎙 (voice note): <what she heard>" in the chat so the user sees the transcription.
- **Tests:** fake Bot API: unpaired voice note ignored, paired voice note transcribed (stub the transcriber),
  queueing while busy.

### A6. "What do you remember about me?" plus memory clean-up

**Why:** the Knowledge page (`/knowledge`) already exists, but nobody can find it, and lessons pile up.

- New tool `show_memory()`: opens the `/knowledge` page in the chat window and reads out a 3-line summary
  (name, top preferences, number of lessons).
- "Forget that I …" already maps to `forget_fact`. Add trigger tests for Hinglish ("ye bhool jao").
- A weekly clean-up job (use A3's scheduler if done, else the idle watcher): merge lessons whose embeddings are
  more than 0.92 similar and drop lessons unused for 90 days that came from her own guesses (`source != "user"`).
  **Never** delete `source == "user"` entries. Write a line to `task_log` with what was merged.
- **Tests:** merge, keep user entries, no-op when retrieval isn't ready.

### A7. Chat with my files (local document search)

**Why:** "what did my notes say about the exam pattern?", "find the PDF about the internship offer."

- New `nova/actions/docs_index.py`. The user picks folders in ⚙ ("📚 Folders Nova can search"). Nothing is indexed
  by default. Never index `data/`, `AppData`, `.git`, or anything over 20 MB.
- Read `.txt`, `.md`, `.pdf` (check whether `pypdf` or another PDF reader is already installed; if none, ask the user
  before adding `pypdf` to `requirements.txt`), `.docx` (zip and XML, no new package) and `.pptx` (same).
- Split into chunks of about 800 characters with overlap, embed with `retrieval.embed()`, and store vectors plus
  text in `data/docs_index.db` (SQLite, vectors as blobs). Re-index only files whose mtime changed. Run it in a
  low-priority background thread, at most every 30 minutes, and never while Nova is answering (`lock.locked()`).
- Tool `search_my_files(question, k=5)`: hybrid score (FTS5 keyword plus cosine) and return
  `file name, page or section, snippet`. The brain answers and names the file. Mail-style privacy: if a cloud
  brain is active, only snippets are sent, and ⚙ has "Let the cloud brain read my files too" (off by default).
- Chat page: a "📄 open" link on each cited file.
- **Tests:** chunking, re-index on change, hybrid ranking on a tiny fixture folder, excluded folders, the cloud
  privacy rule.

### A8. Connector gallery

**Why:** pasting `mcpServers` JSON is hard.

- `static/index.html` 🔌 Connectors: a list of ready entries (Filesystem, GitHub, Google Calendar, Spotify, SQLite,
  Fetch) that fill the JSON for the user and show which keys or folders they need. Keep the raw JSON box for
  experts.
- Check whether `node` / `npx` and `uv` / `uvx` exist (`shutil.which`). If they're missing, the entry says "Needs
  Node.js. Install it?" and uses the existing `find_app` / winget flow, **which asks before installing**.
- **Tests:** the generated JSON is valid for `mcp.py`, and the missing-runtime message.

### A9. Import skills in the open Agent Skills format

- `nova/actions/skills.py`: "import skill from <file or folder>" reads a `SKILL.md` with frontmatter (`name`,
  `description`) and turns its steps into a Nova draft skill. It is **always a draft** until it has worked twice,
  like `save_task_as_skill`. Never run scripts that come with an imported skill; list them and say they were skipped.
- **Tests:** frontmatter parse, draft status, scripts skipped.

---

## Part B: tasks for Opus (Sonnet: do not start these)

These need live tuning on the real laptop, or they change threading that 861 tests only partly cover.

### B1. Voice barge-in: talk over her and she stops — **done 2026-11-19 (Opus), not committed yet**

Built as `voice.BargeIn` plus `Mouth.played` / `said_lines` / `duck()` and `app.on_barge`, with tests in
`tests/test_barge_in.py`. What was measured on this laptop decided the design:
- Her echo on the mic is as loud as the user's voice (0.006 RMS each). Windows' communications-mode echo
  cancelling changed nothing. A frequency-domain adaptive filter got at most ~10 dB with perfect offline
  alignment and ~0 dB live (the speaker is nonlinear and the timing jitters), and spectral masking got 2–3 dB.
- So: a strict "sound her voice can't explain" trigger, then a 1.2–2.5 s **dip** to 0.3 loudness, then
  Whisper on the dip. She stops only on barge words (or 4+ new words) that aren't in her own sentence.
- Live with a stand-in voice through the same speakers: 0 false stops. About 1 in 3 caught at equal
  loudness and 2 in 3 at twice her loudness, mostly when the words land in her pauses. With headphones it should
  be near-certain (not tested: no headphones on the test rig).
- Ideas not done: record through Windows' Voice Capture DSP (the AEC DMO, via COM), or play her voice inside WebView2 and capture with Chromium's AEC3. Both are big changes, and both need
  measuring before they can be trusted.

Original plan:

Today `WhisperEars._loop` drops anything heard while `mouth.busy()`, because it would be her own voice. Only typing
or 🎤 interrupts her. Plan: during playback, compare the mic signal with what `Mouth._player` is playing (her
speaker output is known, so subtract or correlate it), and treat speech that is well above her own echo plus the
`VoiceProfile` level as the user. On a short "Nova stop / ruko / bas" or any sustained speech: `mouth.stop()`, then
listen to the rest. This needs real measurements on this laptop's speakers and mic, headphones vs. speakers, and
must not break the "Hey Nova" timing fixed on 2026-11-18 (see the comment ≈voice.py:977).

### B2. Cleaner capture before Whisper

Strip leading and trailing silence and drop very short low-energy clips before decoding, to cut "heard something I
didn't say". It's small, but it lives in the same tuned code as B1 (`_record`, `_transcribe`), so do it together with
B1 and re-run the listen-timing tests (`tests/test_listen_timing.py`, `tests/test_heard_latency.py`).

### B3. Steering: change a task while she's doing it

Today `answer()` holds `lock` for the whole task, so a new message waits, and Stop drops everything. Plan: while a
task runs, a new typed or spoken message that isn't "stop" goes into a `steer` queue. `_agent_loop` checks the queue
between tool calls and adds it as a user message ("also mute it", "no, the second video"), then carries on. Needs
care with `state["pending"]` confirmations, skills (`_skill_loop` pauses), the phone path (`remote=True`) and the
look-check step.

### B4. Know that it's *you* at the laptop (optional, ask the user first)

Presence finds *a* face (YuNet). Recognising the owner needs a face-embedding model (e.g. SFace from the OpenCV zoo,
loaded through the same OpenCV that already passes Smart App Control), an enrolment flow and a clear privacy story
(embedding stored DPAPI-protected, never a photo). Then: stay quiet or lock when someone else is there. **Ask the
user before building.** It's a sensitive feature, and they may not want it.

---

## Order and size

| Order | Task | Who | Size |
|---|---|---|---|
| 1 | B1 + B2 Voice barge-in and cleaner capture | Opus | L |
| 2 | A1 OpenAI-compatible brain | Sonnet | S |
| 3 | A2 Chat history | Sonnet | M |
| 4 | B3 Steering | Opus | L |
| 5 | A3 Routines | Sonnet | M |
| 6 | A4 Quick web search | Sonnet | S |
| 7 | A7 Chat with my files | Sonnet | L |
| 8 | A5 Phone voice notes | Sonnet | M |
| 9 | A6 Memory clean-up | Sonnet | S |
| 10 | A8 Connector gallery | Sonnet | S |
| 11 | A9 Skill import | Sonnet | S |
| 12 | B4 Owner recognition (ask first) | Opus | M |

The Sonnet and Opus tracks can run at the same time **only if** they don't touch the same file in the same week:
B1/B2 own `nova/audio/voice.py`, and B3 owns `answer()` / `_agent_loop` in `app.py`. A2, A3 and A5 touch `app.py`
only through small hooks. Keep those to a few lines and rebase onto Opus's work.

## Handed to Opus

(Sonnet: add a line here when a Part A task needs a change in the Part B areas, then carry on with the next task.)
