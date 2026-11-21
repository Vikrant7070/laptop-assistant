# Architecture

A map of the code, for anyone changing it. `README.md` explains what Nova does and how to run her;
`docs/REVIEW.md` has the review history. This file is about the shape of the code and the rules that
keep it working.

## Processes

| What | Where | Port |
|---|---|---|
| Chat window + tray + hotkey (`pywebview`, `pystray`) | `nova/core/desktop.py`, started by `desktop.py` / `Start Nova.bat` | - |
| Chat server and conversation loop (Flask) | `nova/core/app.py` | 5055 |
| 3D avatar window and renderer bridge (Flask) | `nova/avatar/avatar.py` | 5056 |

All three run on the same laptop. The servers answer only their own pages: a foreign `Host` or
`Origin` gets 403 (see `only_this_laptop` in `app.py`).

`nova/core/mobile.py` serves the phone page, `nova/core/characters.py` loads the per-character
personas and styles.

## One request, end to end

1. `POST /api/chat` (or `/api/ear_said` for speech) calls `agent_loop`.
2. `agent_loop` starts a task (`new_task`), records the goal in `data/task_log.txt`, and picks a
   brain with `router` / `choose_local_route` (everyday model, coder, deep planner, heavy executor).
3. `nova/brain/toolpick.py` chooses the tools: `CORE` plus the closest matches for this request plus
   the previous request's tools. The set is fixed for the task, so Ollama's prompt cache stays valid
   between steps.
4. `system_prompt` adds the persona (`PERSONA`), the knowledge summary, the matched skill
   (`skills.prompt_block`) and the chosen tool schemas.
5. `_agent_loop` calls `llm.chat` (local Ollama or a cloud brain) and, for each tool call, runs
   `execute` → `_execute_one` → `tools.run_tool`. Risky calls stop at `is_risky` and wait for the
   user's Allow / No (`/api/confirm`).
6. The answer goes to the chat window and to `voice.Mouth`; `settle`, `look_check` and `check_step`
   add the small verification steps (did the window open, did the step work).
7. The knowledge bank keeps what is worth remembering for the next request.

The same loop serves skills: `run_skill` executes a parsed routine step by step (`_skill_loop`), with
the same approval rules as a direct tool call.

## Modules

| Path | Lines* | Job |
|---|---|---|
| `nova/core/app.py` | ~4.6k | Flask routes, prompt, task loop, tool execution, approvals, skills playback, settings, Judge glue (`check_step`, `final_judge`) |
| `nova/actions/tools.py` | ~3.1k | Every tool schema and implementation (101 tools), app guard, typing by paste |
| `nova/audio/voice.py` | ~1.8k | Whisper listening, Kokoro/SAPI speaking, barge-in, wake word |
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
| `nova/log.py` | ~0.06k | Rate-limited exception breadcrumbs in `data/task_log.txt` |
| `nova/__main__.py` | ~0.01k | `python -m nova` entry point; `--version` avoids the desktop stack |
| `nova/brain/retrieval.py` | ~0.13k | Embeddings, with a shared-word fallback |
| `nova/core/characters.py` | ~0.1k | Character folders / personas |
| `nova/avatar/joint_check.py` | ~0.09k | 3D joint sanity check |
| `nova/skill_eval.py` | ~0.08k | Offline skill-matcher evaluation |

\* Approximate on purpose; the point is the relative size. The three biggest files are the ones
Part B splits or shrinks.

`static/index.html` is the chat UI, `static/nova3d.html` the live 3D renderer. `skills/*.md` are the
user's routines, `config/model_routing.json` the routing knobs, `characters/` the persona folders.

## Invariants

- **The prompt prefix is a cache key.** Ollama re-reads from the first changed token. Keep the tool
  set and the system prompt stable for one task; changing either between steps throws the cache away.
- **Tool selection is deterministic.** `toolpick.pick` returns the same names in `tools.TOOLS` order,
  so two identical requests produce byte-identical prompts. `tests/test_routing_golden.py` pins this.
- **Tools never bypass approval.** `is_risky` runs for direct calls and for every skill step.
- **What a person hears has been through `protocol.strip_meta`.** Hidden notes, window lists and
  check markers are prompt text; never speak or show them raw.
- **`data/` is personal and git-ignored.** Tests redirect it (`NOVA_DATA`) and stub the voice, the
  keyboard, the browser, PowerShell and the 3D body, so the suite is safe on any machine.
- **Heavy imports stay lazy.** `faster-whisper`, `kokoro`, `cv2`, `uiautomation`, `comtypes` and
  `sounddevice` are imported inside functions, which is why `requirements-test.txt` can be small.
- **History is sized to the model.** `llm.context_size` knows the local 8K window and the cloud
  providers' windows; `app.history_limit` keeps local at 12 messages and gives a cloud brain more
  (bounded, because a long prompt costs money and time).
- **Failure is visible.** A handler that swallows an exception either calls `log.log_exception(where)`
  (rate-limited per place) or carries `# intentional: <why>`; `tests/test_no_silent_failures.py`
  fails on anything else.

## Adding things

- **A tool:** add the schema and the implementation to `nova/actions/tools.py`. If the name is not
  self-explanatory, add a line to `toolpick.HINTS` and, if it belongs with other tools, to
  `toolpick.GROUPS`. Add a case to `tests/routing.jsonl` for the sentence a user would say.
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

## Known follow-ups (Part B)

The plan is `docs/part-b-plan-v3.md`. What is left is large and higher-risk, so it was deliberately
not attempted in the first pass:

1. `store.py` for settings / notes / reminders instead of module-level file paths.
2. UI split of `static/index.html`.
3. Tool registry: one place that declares schema + handler + risk, and `tools.py` as a package.
4. `app.py` split: HTTP, prompts, session, voice glue, Judge glue, conversation loop, background.
5. Vision close-out: an on/off switch for the one screen look. (Vision is already advisory: the
   Judge in `nova/actions/judge.py` trusts deterministic checks first.)

Already done: the protocol module, model-aware history, exception logging, and the packaging/docs
pass (`python -m nova`, this file, `README.md`, `docs/REVIEW.md` §6).

Smaller known gaps found while writing the golden set:

- `"put chrome in the top-left corner"` reaches `window_action` but not `drag_window`; the hint for
  `drag_window` only knows "left/right side" wording.
