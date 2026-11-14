# Nova code review, test plan and fix plan (2026-11-14)

Scope: every Python module (`app.py`, `tools.py`, `llm.py`, `cloud.py`, `knowledge.py`, `voice.py`, `avatar.py`,
`desktop.py`, `avatar_client.py`, `shared.py`), the four pages in `static/`, `README.md`, `requirements.txt`, and the
git repo itself. Baseline automated run: **42 failed / 65 passed**. After the fixes: see "Results" at the end.

Severity: **Critical** = someone else can run code on the laptop. **High** = data loss, privacy leak, or a crash on
a normal path. **Medium** = wrong behaviour or a hang. **Low** = clean-up / clarity.

## 1. Risks and bugs found

### Security
| # | Sev | Where | Problem | Proof |
|---|---|---|---|---|
| S1 | Critical | `app.py` (all routes), `avatar.py` :5056 | No Host / Origin check. A web page using **DNS rebinding** (its own domain pointed at 127.0.0.1) becomes "same origin" and can POST `/api/setup {"autonomy":"full"}` then `/api/chat "run powershell …"`: remote code execution. Body-less POSTs (`/api/voice_typing` presses Win+H, `/api/reset`, `/api/show_window`) are also open to plain cross-site requests. | `test_api.py::test_foreign_host_header_rejected`, `test_cross_site_post_rejected` |
| S2 | Critical | `tools.py` winget tools, `list_installed_apps`, `delete_to_recycle_bin`, `open_app`, `open_website`; `app.set_autostart` | Model-supplied strings are pasted into PowerShell (`"…$(calc)…"` is executed inside double quotes) or into `cmd /c start` with `shell=True`. Under **trusted** mode `search_installable_apps`, `list_installed_apps`, `open_app` and `open_website` run without asking, and the model reads window titles and screen text, so a page title can steer it (prompt injection → command injection). `shared.ps_escape` existed but was never used. | `test_tools_safety.py` (25 cases) |
| S3 | High | `static/index.html` | `acts()` and the "May I?" box put tool names / labels into `innerHTML` unescaped; `esc()` doesn't escape quotes. A tool name invented by the model (`<img onerror=…>`) runs script inside the Nova window, which can call every `/api/*` route. | code review |
| S4 | High | git | No `.gitignore`. `data/knowledge.json` (name, employer, city, **email**), `data/task_log.txt` (conversation transcripts), `data/settings.json`, `data/keys.json` (DPAPI-encrypted key) and `__pycache__/` are committed. No remote yet, so nothing has leaked, but the first `git push` would publish it. | `git ls-files` |
| S5 | High | `tools.clean_temp_files` | If `%TEMP%` is unset, `Path("")` is the **current folder** and every file older than an hour in it is deleted. | `test_clean_temp_refuses_without_temp` |
| S6 | Medium | `tools.kill_process` | "close python"/"pythonw" kills Nova herself (and the 3D body) mid-answer. | `test_kill_process_never_kills_nova` |
| S7 | Low | `knowledge.SECRET` | Email addresses / phone numbers aren't filtered, and the profile summary is sent to the cloud brain with every message. (Documented in README; left as is, flagged for you.) | review |

### Correctness / reliability
| # | Sev | Where | Problem | Proof |
|---|---|---|---|---|
| B1 | High | `app.answer` / `confirm_pending` | Only `OllamaError` was caught. Any other exception left half a turn in the history (next request sees broken tool-call pairs) and returned an HTML 500; from voice, `heard()` then crashed with `UnboundLocalError`. | `test_unexpected_error_rolls_back_and_returns_500` |
| B2 | High | `app.review_turn` | The background "learn a lesson" call held the global conversation lock **during the LLM call** (up to 25 s cloud / 600 s local), so the user's next message froze until it finished. | review |
| B3 | Medium | `app.state["messages"]`, `app.events` | Grow forever (only the prompt is trimmed). Nova runs for days from the tray. | `test_history_is_capped`, `test_events_queue_bounded` |
| B4 | Medium | `/api/chat`, `/api/setup`, `/api/knowledge` | Wrong JSON types (`{"text":123}`, `{"model":5}`, `{"id":"abc"}`) crash with 500. | `test_chat_rejects_empty_and_non_string`, `test_setup_rejects_bad_types`, `test_knowledge_edit_validation` |
| B5 | Medium | `app.setup` | `settings.json` written in place; a concurrent read (every request reads it) can see a half-written file and silently fall back to defaults (autonomy, provider, voice). | `test_setup_write_is_atomic` |
| B6 | Medium | `tools.open_website` | Search text not URL-encoded: `c++` becomes `c  `, `&` cuts the query, `"` breaks the command line. | `test_open_website_encodes_and_no_shell` |
| B7 | Medium | `cloud.parse` | A malformed id (`cloud:google`) raises `ValueError` in every request. | `test_parse_model_ids` |
| B8 | Low | `tools.copy_path`, `tools.list_folder` | Missing source gives a raw traceback text; one unreadable file breaks the whole listing. | `test_copy_missing_source_is_clear` |
| B9 | Low | `tools.set_reminder` / `due_reminders`, `add_note` | Read-modify-write from two threads (`/api/pulse` and a tool) can drop a reminder. | review |

### Duplicates and ambiguity
| # | Where | Problem |
|---|---|---|
| D1 | `tools.REACTIONS`, `avatar.REACTIONS` | Same tuple twice (`shared.py` was started for this but not wired in). |
| D2 | `desktop.work_area`, `avatar.work_area`, inline in `tools.drag_window` | Same Win32 call three times. |
| D3 | `tools._avatar_pos`, `desktop.show_ear` | Both hand-roll `GET http://127.0.0.1:5056/` instead of `avatar_client`. |
| D4 | `app.py` | `re.sub(r"^\[spoken…")` copied 3×; "I'm awake" block copied in `on_wake`; `import avatar_client` in the middle of the file although used earlier; `import re` repeated inside `save_frames`; local variable `state` in `/api/avatar` shadows the global conversation `state`. |
| D5 | `voice.HINGLISH_WORDS` | `wala`, `rahe`, `chal` defined twice. |
| D6 | data folder | `tools.DATA`, `knowledge.FILE`, `cloud.KEYS_FILE` each compute `data/` separately; no way to point Nova (or tests) at another folder. |
| A1 | README vs `desktop.py` | README: "double-click Start Nova.bat, the chat window opens". Code: chat starts hidden unless `--show`; autostart passes `--hidden`, which nothing reads. |
| A2 | README | Says she speaks in Windows' Heera voice and, later, in Kokoro `hf_alpha`. Kokoro is used when its files exist, Heera otherwise. |
| A3 | `requirements.txt` | `comtypes` (used directly by `voice.py`) and `numpy` aren't listed; nothing is version-pinned. `pytest` not listed for development. |

## 2. Test plan

Automated (`python -m pytest -q`, ~110 cases, no real side effects: `data/` is redirected via `NOVA_DATA`, voice,
Whisper, 3D body, keyboard and PowerShell are stubbed, the LLM is scripted):

| Suite | Covers |
|---|---|
| `tests/test_registry.py` | Every tool schema matches its Python function; labels (FRIENDLY / ASK) exist for every tool; DANGEROUS ⊂ RISKY; single REACTIONS source; `run_tool` drops extra args and reports errors |
| `tests/test_tools_safety.py` | Injection payloads (`'`, `"`, `$()`, backticks, `;`) for every tool that builds a command; no `shell=True`; URL encoding; TEMP guard; Nova never kills herself; autostart quoting; notes/reminders round-trip |
| `tests/test_agent.py` | careful / trusted / full approval tiers; decline; moving on = decline; promise-without-tool nudge; turn limit; rollback on brain error and on unexpected error (lock released); history cap and trimming; English/Hinglish fact, video, yes/no shortcuts; spoken text never leaks the hidden window list; reactions; learning triggers |
| `tests/test_api.py` | DNS-rebinding Host check; cross-site Origin check; type validation; atomic settings write; knowledge editing; sprite export can't escape its folder; bounded event queue |
| `tests/test_brains.py` | Knowledge bank (dedupe, secrets refused, lesson cap, corrupt-file recovery); DPAPI key round-trip; cloud history conversion pairs every tool call with a result; cloud → local fallback; text-protocol parsing |

Manual / live checks (need the real laptop, mic, GPU; run after the automated suite passes):
1. `Start Nova.bat` → tray icon + 3D Nova appear; Ctrl+Alt+N opens chat; closing hides to tray; tray → Quit ends all processes (check Task Manager for stray `pythonw` / `powershell listen.ps1`).
2. Chat: "what time is it", "how is my battery", "open notepad and type hello" (careful mode asks, trusted doesn't), "close notepad" (window asks to save).
3. Approval: careful → each click asks; trusted → shutdown asks; "cancel shutdown" never asks.
4. Cloud brain: save a bad key (refused), good key (models listed); pull the network → "Used the local brain" appears and the answer still comes.
5. Voice: Hey Nova with the chat hidden; "Nova wake up" → conversation; "go to sleep"; typing interrupts speech.
6. Screen/camera toggles off → tools refuse; on → image shown in chat, nothing written to disk.
7. Security: from a normal browser tab, `fetch("http://127.0.0.1:5055/api/reset",{method:"POST"})` → 403.
8. Soak: leave running 8 h; memory of `pythonw` stays flat; `data/task_log.txt` stays under ~300 KB.

## 3. Fix plan (in order)

1. **S1** – one `before_request` guard in `app.py`: Host must be `127.0.0.1`/`localhost` on our port; any request with an `Origin`/`Referer` from elsewhere is refused (403). Same Host check in `avatar.py`.
2. **S2/B6** – route every PowerShell argument through `shared.ps_escape` inside single quotes; `open_app` / `open_website` / `power_action` stop using `shell=True` (`os.startfile` or an argument list); search text URL-encoded.
3. **S3** – escape everything that goes into `innerHTML` in `index.html`, including quotes.
4. **S4** – add `.gitignore` (data files, logs, `__pycache__`, `.tmp`) and untrack them with `git rm --cached` (files stay on disk).
5. **S5/S6/B8/B9** – TEMP guard; skip Nova's own process tree in `kill_process`; clear messages in `copy_path`/`list_folder`; a lock around notes/reminders.
6. **B1/B2/B3/B4/B5/B7** – catch-all rollback in `answer`/`confirm_pending`; LLM call in `review_turn` outside the lock; cap history and events; validate request types; atomic settings write; tolerant `cloud.parse`.
7. **D1–D6** – use `shared.py` for REACTIONS / work_area / ps_escape; `avatar_client.position()` replaces the two hand-rolled fetches; one `NOVA_DATA` data folder; tidy `app.py` imports and the shadowed name; remove duplicate dictionary keys.
8. **A1–A3** – README matches the start-up behaviour and voice order; `requirements.txt` lists `comtypes`, `numpy`; `requirements-dev.txt` adds `pytest`.

Not changed on purpose: the autonomy design itself (trusted mode lets `run_powershell` run without asking, which is how you configured it), and S7 (would change what Nova remembers; your call).

## 4. Results (after the fixes)

- Automated: **109 passed, 0 failed** (`python -m pytest -q`, about 4 s). Baseline was 42 failed / 65 passed.
- `pyflakes` clean on every module (the only notes are `voice.py`'s deliberate "is this package installed?" imports).
- Live smoke test on a separate server (port 5077, temporary data folder, silent voice) with real HTTP requests:
  status/index 200; foreign Host 403; cross-site and `Origin: null` POSTs 403; same-origin POST 200; `{"text":5}` 400
  with a JSON error; a real chat ("what time is it?") answered through the local Ollama model with the time tool.
- Fixed: S1–S6, B1–B9, D1–D6, A1–A3. Left for you: S7 (email/phone numbers in the profile reach the cloud brain).
- Not yet verified on the real desktop: the manual checks in section 2 (tray, hotkey, voice, clicking, 3D Nova).
  The running copy of Nova still has the old code until it is restarted (tray → Quit Nova, then `Start Nova.bat`).
- Git: `data/` and `__pycache__/` are now untracked (`git rm --cached`, files kept on disk) and ignored. They are
  still inside the two earlier commits; there is no remote, so nothing was published.

## 5. Follow-up: speech bubble shown twice, and audio out of step (2026-11-14)

| Problem | Cause | Fix |
|---|---|---|
| Reply bubble shown twice for voice requests | `heard()` showed the reply, and the chat page showed it again when it got the same event. Same pattern for "go to sleep", "I'm awake", reminders and "May I…?"; "I'm listening" came from three places with three wordings. | The voice is the only thing that shows what she says (`voice.Mouth._bubble`). With the voice off, `app.bubble()` shows the reply once. One `voice.LISTENING` text. The page no longer sends bubbles. |
| Bubble out of step with the audio | The whole reply (cut at 170 characters) appeared when the text was ready, for a guessed time. | Each sentence appears when its sound starts, for exactly as long as it plays, and is taken down when she's interrupted. |
| Lips / next sentence running late | The player waited for a blocking ~45 ms message to 3D Nova before working out when the sound ends. | Messages to 3D Nova go through their own thread (`Mouth._teller`); stale ones from an interrupted reply are dropped. |
| ~0.33 s silence before every sentence | The sound device was opened again for each sentence (`sd.play`). | One output stream stays open; text→sound went from ~1.1 s to ~0.6 s, with no gaps between sentences. |
| Long first sentences slow to start | The whole sentence was synthesized before any sound. | A long first sentence is split at an early comma or a joining word ("and", "without"...). |
| Duplicate listener code | `Ears` and `WhisperEars` each had their own copy of the status notes, "wake up" and "go to sleep" handling. | Shared `_EarsBase`. |

Live check after restart: typed request — sound and bubble 0.65 s after the reply text; voice request — each sentence's
bubble appears with its sound (lips within 0.06 s), shown once, cleared ~0.6 s after it ends. `VOICE starts` lines in
`data/task_log.txt` now record where any delay comes from (synth time, wait, sound device). 120 tests pass.
