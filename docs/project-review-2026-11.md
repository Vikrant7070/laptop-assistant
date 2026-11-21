# Nova: what should have been done differently

A whole-project review after Part B (2026-11-21). Part B fixed *how the code is organised*. This looks at the
*design decisions* underneath, the ones that would have made Nova a better project if made earlier, and says which
are still worth making now.

Nova is impressive for one laptop and one person: a voice assistant with a live 3D body, offline speech, local and
cloud brains, a phone link, Gmail, connectors, skills it learns, coding workers, and 1,190 tests. The issues below are
the price of building all of that feature by feature, mostly on a 4B local model, with several AI agents working on
the same repository at once.

Each item: **what it is now** (with evidence), **why it hurts**, **what would have been better**, and **now?**
(worth doing today, and how big). Ordered by impact.

---

## 1. Too many moving parts talk by polling

**Now:** one desktop run starts a Flask server on port 5055, three WebView windows (chat, the ear bubble, and a hidden
3D renderer), a separate avatar process with its own HTTP server on 5056, the tray, and optionally a mobile server.
The hidden renderer streams frames to the avatar process. The chat page polls `/api/thoughts` every 0.6 s, the voice
state every 1 s and `/api/pulse` every 15 s.
**Why it hurts:** every hop is a place for lag and for "it worked yesterday": port clashes, a renderer throttled while
hidden (see the memory notes on CPU and throttling), bubbles shown twice, audio out of step (REVIEW §5). Polling also
burns CPU while idle and makes timing bugs hard to reproduce.
**Better:** one event stream. The server pushes events (thought, speak-start, speak-end, bubble, state) over
Server-Sent Events or a WebSocket, and every view (chat, ear, 3D, phone) is a subscriber. Push the avatar's commands the
same way, rather than one HTTP request per command.
**Now?** Yes, gradually (M). Start with an `/api/events` SSE stream for thoughts and voice state, and drop two of the
three polls. That alone removes a class of timing bugs.

## 2. Concurrency by threads and shared dictionaries

**Now:** 72 places start a thread and there are 40 locks. Module-level dicts hold live state (`state`, `nap`, `convo`,
`offer`, `listen_state`, `help_offer`, ...), read and written by the request thread, the voice threads, watchers and
timers.
**Why it hurts:** the hardest bugs in the history are races: barge-in, naps mid-task, "speech bubble shown twice",
"heard bubble latency". Every new feature adds a thread and a flag, and nobody can say what state the system is in.
**Better:** one owner of state. Either an asyncio event loop (voice, HTTP and timers as tasks), or a single "conductor"
thread with a queue that every other thread posts to. State changes then happen in one place, in order, and can be
logged as events. A small state machine for the voice (idle → listening → thinking → speaking → napping) would replace a
dozen booleans.
**Now?** Partly (L). Start with the voice and conversation state machine, the source of most of the races. A full move to
asyncio isn't worth it now.

## 3. Regexes instead of structure around the model

**Now:** 118 compiled regexes. Many interpret what the *model* said rather than what the user said: `DONE_CLAIM_RE`,
`PROMISE_RE` and `BODY_CLAIM_RE` catch "she said she did it but called no tool"; `FAIL_RE`, `CORRECTION_RE`, `YES_RE`
and `NO_RE` read intent; stage directions are stripped from replies. There's also a text tool-call protocol for models
without native tools.
**Why it hurts:** every regex is a guess about phrasing, in English and Hinglish. Each new model phrases things
differently, so the guesses rot. The golden set (`tests/routing.jsonl`) now pins some of them, but it came late.
**Better:** make the model *say* what it means in a structured way:
- an explicit `finish(summary, done: bool)` tool instead of detecting done-claims in prose;
- JSON-schema / structured outputs for yes/no and intent;
- the Judge's deterministic checks (already there) as the truth.

Keep regexes only for the user's own short commands ("stop", "louder"), all in one `intents.py` with a test set.
**Now?** Yes (M). Add `finish` and route done-detection through it; delete the done/promise regexes once the golden set
and live runs agree.

## 4. A shell as a "core" tool

**Now:** `run_powershell` is a core tool (offered on every request), described as the catch-all. It's in the risky
tier, so "careful" asks first, but "full" autonomy runs anything, and the model decides what "anything" is. 21 places
start PowerShell.
**Why it hurts:** this is the largest safety surface in the project. A small model plus a prompt injection (an email or
web page Nova reads) plus "full" autonomy equals arbitrary commands. The app guard and approval tiers are good, but
they guard the edges of a door that is open by default.
**Better:** capability-scoped tools first, and the shell last:
- no `run_powershell` in CORE;
- a dangerous tier for the shell whatever the autonomy level, unless the command matches a small allowlist of
  read-only cmdlets;
- tool output that came from outside (mail, web pages) marked as untrusted in the prompt, which `protocol.py` makes
  easy now.

**Now?** Yes (S). Take it out of CORE, add the read-only allowlist, and require an OK for everything else. That's a
small diff for the biggest risk reduction available.

## 5. Tests coupled to internals instead of seams

**Now:** 1,190 tests, 502 `monkeypatch.setattr` calls. Tests patch private helpers (`_windows`, `_focus`, `_clip_save`,
`_screen_allowed`...) to fake the desktop. Part B had to rewrite hundreds of patch targets just to move code.
**Why it hurts:** the tests describe how the code is built, not what it does, so every refactor touches them, and a
patch that misses passes silently (hence the guard tests).
**Better:** one platform interface with a fake, for example `Desktop` with `windows()`, `focus(w)`, `press(keys)`,
`type(text)`, `clipboard`, `screenshot()` and `dark_mode`. Production gets the Win32 implementation and tests get
`FakeDesktop` (the memory notes already mention "fake-desktop tests" for the app guard). Tests then set up a scene
("Notepad is open and in front") instead of patching six functions.
**Now?** Yes, incrementally (M–L). Build `nova/platform/` (see 6) with a `FakeDesktop`, and move new and touched tests
onto it. No big-bang rewrite.

## 6. Windows calls everywhere

**Now:** 19 files call ctypes/`user32` directly: tools, checks, watcher, recorder (which keeps "our own copies of
user32" so argtypes don't clash), coworker, presence, avatar, desktop, cloud (DPAPI), speedup...
**Why it hurts:** the same window-enumeration and focus code exists in variants, argtypes collide between modules (the
recorder's comment says so), and nothing can be tested without patching.
**Better:** `nova/platform/windows.py` as the only place that touches Win32 (windows, focus, input, clipboard, DPI,
DPAPI, registry), behind the `Desktop` interface from 5.
**Now?** Yes, together with 5 (M).

## 7. Data as loose JSON files, settings as an untyped dict

**Now:** 20 named files in `data/`, each with its own load/save code and its own idea of a schema (some have a
`version` key). Settings are one 34-key dict with defaults in code and no validation. `task_log.txt` (270 KB here) is
both the human debug log and an input other code mines.
**Why it hurts:** a bad value (a string where a number belongs, a key renamed in a new version) shows up as a wrong
behaviour much later. Records that grow (episodes, jobs, notes, reminders, skill stats) are rewritten whole on every
change, and the logs can't be queried.
**Better:**
- typed settings: one dataclass or pydantic model with defaults, validation and a schema version;
- SQLite (standard library) for things that grow and get queried: episodes, jobs, reminders, skill stats, events;
- structured JSON-lines logs for machines, with `task_log.txt` kept as a readable view.

`nova/store.py` is now the single place to make this change.
**Now?** Typed settings yes (S–M). SQLite only when the next feature needs to query history (M).

## 8. Breadth before depth, and no success metric from day one

**Now:** a voice assistant, 3D companion, PC automation, phone remote, mail reader, MCP client, skill learner, coding
manager and an RL "child brain", on a 4B local model, with heuristics (3, 4) making up for what the model can't do. Task
success has only recently started being measured (the episode log, the Judge's verdicts).
**Why it hurts:** each feature works in the demo path, but the core loop ("do what I asked, correctly, and say so
honestly") stays the bottleneck, and it's hard to know whether a change made Nova better overall.
**Better:** pick 20–30 real tasks early and measure success rate, time and honest-failure rate on every change, local
and cloud. Grow features only when the core number holds. The Judge and the episode log are the start of this;
`tests/routing.jsonl` is the offline half.
**Now?** Yes (M). Turn the episode log plus the Judge into a weekly "task exam" (like the child brain's nightly exam),
and gate model, prompt and heuristic changes on it.

## 9. Several AI agents on one repository without rules

**Now:** OpenCode, Claude sessions and feature sessions worked on the same files at the same time. In one day: the
`refactor/part-b` branch drifted 17 commits behind and conflicted; Phase 4 and Part B had to be sequenced by hand; a
performance session edited `tools.py` in the main folder while it was being restructured; a stray branch appeared; a
worktree was half-deleted. CI only arrived with Part B.
**Why it hurts:** most of the coordination work today wasn't engineering. It was avoiding overwrites.
**Better:**
- CI from the first commit;
- short-lived branches merged within a day or two;
- one rule file the agents read (`CLAUDE.md` / `AGENTS.md`): work only in a worktree, never in the main folder (the
  live Nova runs from there), check `git worktree list` before touching a shared file, one writer per area at a time;
- an `OWNERS`-style map: which session owns `tools/`, `app/`, `childbrain/` this week.

**Now?** Yes (S). Write `CLAUDE.md` / `AGENTS.md` with these rules today. It's the cheapest item on this list.

## 10. Repository hygiene

**Now:** 41 MB of `.vrm` models and voice-sample `.wav` files committed straight to git; `childbrain/` (4.6k lines, its
own requirements with PyTorch) inside the product repository; 10 plan documents in `docs/` plus more outside the repo
(`~/.opencode/plan`), several partly stale.
**Why it hurts:** every clone carries every version of every model forever, the product's CI and tooling see a research
project, and nobody knows which plan is current.
**Better:** Git LFS for `.vrm`/`.wav` (or a download step like the Whisper and Kokoro models); `childbrain` as its own
repository or at least its own package with its own CI job; `docs/plans/` with a status line at the top of each plan
(active / done / dropped), and short ADRs (architecture decision records) for decisions like "packages answer
`tools.<name>` from the home module".
**Now?** LFS and the plans folder yes (S). Moving `childbrain` out is the owner's call.

## 11. Packaging and setup

**Now:** one `requirements.txt` mixing core, GPU (CUDA wheels), voice, camera and cloud packages, plus
`requirements-test.txt`, `requirements-dev.txt` and `requirements-childbrain.txt`. `pyproject.toml` has no dependencies.
Setup is a README walkthrough and `Start Nova.bat`.
**Better:** dependencies in `pyproject.toml` with extras (`nova[voice]`, `nova[gpu]`, `nova[camera]`, `nova[cloud]`,
`nova[dev]`), a lock file, and a first-run check that says what's missing ("no GPU: using the CPU voice model") rather than
failing later.
**Now?** Yes (S–M), when someone else needs to install it.

## 12. Prompts and persona in code

**Now:** the persona, system prompt and check prompts are Python strings in `app/prompts.py` and elsewhere
(`REVIEW_PROMPT`, `RESEARCH_PROMPT`, the Judge's check prompt), while `characters/*/persona.md` already shows the better
pattern.
**Better:** prompts as versioned template files next to the characters, with the golden set and the task exam (8) run
whenever one changes. A prompt change is a behaviour change; it deserves the same review as code.
**Now?** Yes (S) for the system and check prompts.

---

## What was done right (keep doing it)

- **Deterministic checks before vision** (the Judge): the most important reliability decision in the project.
- **Approval tiers and the app guard:** keys never go to the wrong app, and risky steps need an OK.
- **Secrets never reach the brain** (forms, logins, OTP), and API keys are encrypted with DPAPI.
- **Local-first with a cloud option,** with a measured 8K context and model-aware history.
- **Lots of tests, CI, golden sets** and guard tests that encode the project's rules.
- **Memory notes with measured numbers and gotchas,** which turned debugging sessions into reusable knowledge.

## If you do only five things

| # | Item | Why first | Effort |
|---|---|---|---|
| 1 | Shell out of CORE, read-only allowlist, OK for the rest (4) | Biggest safety gain for the smallest diff | S |
| 2 | `CLAUDE.md` / `AGENTS.md` rules for agents and worktrees (9) | Stops the collisions that cost most time today | S |
| 3 | A `finish` tool, then delete the done/promise regexes (3) | The core loop becomes honest by structure, not by phrasing | M |
| 4 | One event stream instead of polling, then a voice state machine (1, 2) | Removes the class of timing bugs | M–L |
| 5 | A task exam on real tasks, gating changes (8) | Know whether a change made Nova better | M |

Then, as touched: `nova/platform` + `FakeDesktop` (5, 6), typed settings (7), Git LFS and the plans folder (10),
packaging extras (11), prompt files (12). The four big-file splits are in `docs/file-split-plan.md`.
