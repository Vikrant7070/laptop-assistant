# Architecture

A map of the code, for anyone changing it. `README.md` explains what Nova does and how to run her;
`docs/REVIEW.md` has the review history. This file is about the shape of the code and the rules that
keep it working.

## Processes

| What | Where | Port |
|---|---|---|
| Chat window + tray + hotkey (`pywebview`, `pystray`) | `nova/core/desktop.py`, started by `desktop.py` / `Start Nova.bat` | - |
| Chat server and conversation loop (Flask) | `nova/core/app/` | 5055 |
| 3D avatar window and renderer bridge (Flask) | `nova/avatar/avatar.py` | 5056 |

All three run on the same laptop. The servers answer only their own pages: a foreign `Host` or
`Origin` gets 403 (see `only_this_laptop` in `app/api.py`).

`nova/core/mobile.py` serves the phone page, `nova/core/characters.py` loads the per-character
personas and styles.

## One request, end to end

Module names in brackets are under `nova/core/app/`.

1. `POST /api/chat` (`api`; `/api/ear_said` for speech, `voice_io`) calls `agent_loop` (`conversation`).
2. `agent_loop` starts a task (`new_task`), records the goal in `data/task_log.txt`, and picks a
   brain with `router` / `choose_local_route` (everyday model, coder, deep planner, heavy executor).
3. `nova/brain/toolpick.py` chooses the tools: `CORE` plus the closest matches for this request plus
   the previous request's tools. The set is fixed for the task, so Ollama's prompt cache stays valid
   between steps.
4. `system_prompt` (`prompts`) adds the persona (`PERSONA`), the knowledge summary, the matched skill
   (`skills.prompt_block`) and the chosen tool schemas.
5. `_agent_loop` calls `llm.chat` (local Ollama or a cloud brain) and, for each tool call, runs
   `execute` → `_execute_one` → `tools.run_tool`. Risky calls stop at `is_risky` and wait for the
   user's Allow / No (`/api/confirm`).
6. The answer goes to the chat window and to `voice.Mouth`; `settle`, `check_step` and `final_judge` (`judging`)
   decide whether the task is done: deterministic checks first, at most one advisory screen look.
7. The knowledge bank keeps what is worth remembering for the next request.

The same loop serves skills: `run_skill` (`skillrun`) executes a parsed routine step by step (`_skill_loop`), with
the same approval rules as a direct tool call.

## Modules

| Path | Lines* | Job |
|---|---|---|
| `nova/core/app/` | ~5k in 15 files (largest ~0.8k) | The server, one module per job: `config` (settings, brain), `prompts` (persona, system prompt, tools offered), `session` (messages, task, live thoughts, labels), `conversation` (Stop switch, tasks, routing, history, `agent_loop`), `skillrun` (skill playback), `judging` (`settle`, `check_step`, `final_judge`), `learning` (episodes, Learner, questions, watch-me, knowledge review), `api` (Flask, `only_this_laptop`, chat routes), `host`, `voice_io` (speaking, ears, sleep, barge-in, naps), `body`, `integrations` (co-worker, notifications, phone, Gmail, presence, watcher, connectors), `background` (suggestions, watchers, quit, pulse); `__main__` runs it |
| `nova/actions/tools/` | ~3.3k in 12 files (largest ~0.6k) | The tools, each declared with `@tool` above its function, one module per area: `system`, `apps`, `files`, `windows` (incl. typing by paste and the app guard), `forms` (clicking, forms, logins), `eyes`, `notes` (incl. Gmail), `routines`, `body`, `terminal`; `common` holds shared helpers; `__init__` the tables and `run_tool` |
| `nova/actions/registry.py` | ~0.1k | `@tool`: one declaration per tool, fed into TOOLS/FUNCS/RISKY, FRIENDLY/ASK and toolpick |
| `nova/audio/voice/` | ~1.8k in 6 files (largest ~0.55k) | A package, one module per job: `speak` (Kokoro/SAPI, `Mouth`), `wording` (text for speech, Hinglish), `whisper` (Whisper listening), `wake` (wake-word ears, `set_wake`), `barge_in`, `windows_ears` (Windows recognizer; its PowerShell in `listen.ps1`/`rt_listen.ps1`). `voice.<name>` reads the home module |
| `nova/actions/skills.py` | ~1.2k | Parse, match, render, run and edit `skills/*.md` |
| `nova/avatar/avatar.py` | ~0.94k | 3D window, frame bridge, speech gestures |
| `nova/actions/coworker.py` | ~0.77k | Background jobs (research, drafts) |
| `nova/actions/recorder.py` | ~0.65k | "Watch me" recording of user actions |
| `nova/actions/learner.py` | ~0.42k | Episode log, learned facts, skill offers from repeated tasks |
| `nova/actions/judge.py` | ~0.19k | Is the task done? Deterministic checks first, one advisory screen look last |
| `nova/actions/questions.py` | ~0.13k | Daily list of questions Nova wants to ask the user |
| `nova/actions/mcp.py` | ~0.52k | MCP connector client (stdio + HTTP JSON-RPC) |
| `nova/avatar/body.py` | ~0.51k | Moves, look, gesture selection |
| `nova/brain/cloud.py` | ~0.47k | Gemini / OpenAI / Anthropic adapters and key storage |
| `nova/brain/llm.py` | ~0.41k | Ollama HTTP client, tool calling, model capabilities |
| `nova/audio/sfx.py` | ~0.35k | Reaction sounds |
| `nova/actions/gmail.py` | ~0.36k | Read-only Gmail |
| `nova/actions/notifications.py` | ~0.35k | Windows notification reading |
| `nova/actions/phone.py` | ~0.33k | Telegram link |
| `nova/core/mobile.py` | ~0.32k | Phone web page |
| `nova/actions/presence.py` | ~0.29k | Here / away detection |
| `nova/actions/knowledge.py` | ~0.29k | Facts, lessons, summaries |
| `nova/brain/router.py` | ~0.24k | Which local model does the task |
| `nova/actions/watcher.py` | ~0.2k | Error boxes and frozen apps |
| `nova/core/desktop.py` | ~0.2k | Window, tray, hotkey, avatar start-up |
| `nova/brain/toolpick.py` | ~0.17k | Tool selection per request |
| `nova/actions/forms.py` | ~0.15k | Form filling plan |
| `nova/actions/checks.py` | ~0.15k | Step checks (`window_open`, `page_has`, ...) |
| `nova/shared.py` | ~0.13k | Paths (`ROOT`, `DATA`), PowerShell quoting, host checks |
| `nova/protocol.py` | ~0.09k | Every text marker: builders, `strip_meta`, vision verdict parsing |
| `nova/store.py` | ~0.1k | Every data path (`store.current().settings`, `.notes`, ...), atomic JSON helpers |
| `nova/log.py` | ~0.06k | Rate-limited exception breadcrumbs in `data/task_log.txt` |
| `nova/__main__.py` | ~0.01k | `python -m nova` entry point; `--version` avoids the desktop stack |
| `nova/brain/retrieval.py` | ~0.13k | Embeddings, with a shared-word fallback |
| `nova/core/characters.py` | ~0.1k | Character folders / personas |
| `nova/avatar/joint_check.py` | ~0.09k | 3D joint sanity check |
| `nova/skill_eval.py` | ~0.08k | Offline skill-matcher evaluation |

\* Approximate on purpose; the point is the relative size. The three biggest files are the ones
Part B splits or shrinks.

`static/index.html` is the chat UI (`nova.css`, `nova.js`; `knowledge.html` + `knowledge.js` is the Knowledge page), `static/nova3d.html` the live 3D renderer. `skills/*.md` are the
user's routines, `config/model_routing.json` the routing knobs, `characters/` the persona folders.

## Invariants

- **The prompt prefix is a cache key.** Ollama re-reads from the first changed token. Keep the tool
  set and the system prompt stable for one task; changing either between steps throws the cache away.
- **Tool selection is deterministic.** `toolpick.pick` returns the same names in `tools.TOOLS` order,
  so two identical requests produce byte-identical prompts. `tests/test_routing_golden.py` pins this.
- **Tools never bypass approval.** `is_risky` runs for direct calls and for every skill step.
- **What a person hears has been through `protocol.strip_meta`.** Hidden notes, window lists and
  check markers are prompt text; never speak or show them raw.
- **`data/` is personal and git-ignored.** Every path in it comes from `store.current()`, asked for when it is
  needed: no module keeps a data path in a global (`tests/test_store.py` checks). `NOVA_DATA` moves all of it;
  each test installs its own `Store` in a temp folder, and the suite stubs the voice, the keyboard, the browser, PowerShell and the 3D body, so the suite is safe on any machine.
- **Heavy imports stay lazy.** `faster-whisper`, `kokoro`, `cv2`, `uiautomation`, `comtypes` and
  `sounddevice` are imported inside functions, which is why `requirements-test.txt` can be small.
- **Page markup is escaped by default.** `static/markup.js` gives `` html`...` `` (every `${value}` escaped unless it
  is itself `html`) and `put()`, the only place that sets `innerHTML`. Don't build markup any other way.
- **History is sized to the model.** `llm.context_size` knows the local 8K window and the cloud
  providers' windows; `app.history_limit` keeps local at 12 messages and gives a cloud brain more
  (bounded, because a long prompt costs money and time).
- **Failure is visible.** A handler that swallows an exception either calls `log.log_exception(where)`
  (rate-limited per place) or carries `# intentional: <why>`; `tests/test_no_silent_failures.py`
  fails on anything else.

## Adding things

- **A tool:** one function with `@tool(...)` in the area module under `nova/actions/tools/` it belongs to (or a
  module of its own, imported from `tools/__init__.py`). The decorator holds the schema, labels, risk and `hints=`;
  if it belongs with other tools, add it to `toolpick.GROUPS`. Add a case to `tests/routing.jsonl` for the sentence
  a user would say. Tests patch a tool helper in its home module (`tools.windows._windows`), never on `tools`
  itself - `tests/test_tools_package.py` checks that.
- **A skill:** drop a `.md` in `skills/`. `nova/actions/skills.py` parses and matches it; a skill
  that should only run when named uses `invoke: explicit_only`.
- **A route trigger:** edit `config/model_routing.json` (defaults live in `nova/brain/router.py`).
  Add the sentence to the `route` cases in `tests/routing.jsonl`.

## Tests

`python -m pytest -q` (~990 tests). `tests/conftest.py` is the harness: a fresh temp data folder per
test, `FakeMouth`, a scripted brain (`brain` fixture), and stubs for anything that would touch the
real laptop. `tests/test_routing_golden.py` + `tests/routing.jsonl` are the contract for tool
selection, skill matching and model routing; they pin the lexical fallback so CI does not need the
embedding model, and re-check the tool cases with embeddings when the model is present.

`scripts/check.ps1` runs the same two commands as CI: `ruff check .` then `pytest -q`.

## Packages and patching

`nova/actions/tools/` and `nova/core/app/` are packages whose `__init__` answers `tools.<name>` /
`app.<name>` from the name's home module on every access, so callers can keep the short form. To
**patch or assign** a name, use its home module (`tools.home_of(name)`, `app.home_of(name)`):
setting it on the package only shadows it, and the code that uses it never sees the change.
`tests/test_tools_package.py` and `tests/test_app_package.py` fail on that, on a name that no longer
resolves, and on a module over 800 lines.

## Part B

Done (plan: `docs/plans/part-b-plan-v3.md`; summary: `docs/REVIEW.md` §6): CI, the protocol module,
model-aware history, exception logging, the data store, the chat UI split, the vision switch, `@tool`
and the tools package, the app package, and these docs.

Smaller known gaps found while writing the golden set:

- `"put chrome in the top-left corner"` reaches `window_action` but not `drag_window`; the hint for
  `drag_window` only knows "left/right side" wording.
