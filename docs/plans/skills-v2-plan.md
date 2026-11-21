Status: done — 2026-11-18, phases 1–9 built; phase 10 (browsing habits) dropped unless the user asks.

# Skills v2 — implementation plan

**Status (2026-11-18): all phases 1–9 built and tested** (phase 10, browsing habits, is not built — ask the user first).
Phases 6–7 live in `nova/actions/recorder.py` (hooks, worker, `events_to_trace`) and the "watch me" / take-over section
of `nova/core/app.py`; tests in `tests/test_recorder.py` and `tests/test_watch_flow.py`. Not yet tried with the user's
own hands on the real laptop.

Hand this whole file to the implementing model. It is written so that each phase can be built, tested and committed
on its own, in order. Do not skip ahead: later phases depend on the data the earlier ones record.

Background and evidence: `reports/Agent skill authoring and learning.md` (the research behind every decision here).
Read its section "A prioritized redesign for Nova" before starting.

---

## 0. Before you start

### What exists today (read these first)

| File | What it does now |
|---|---|
| `nova/actions/skills.py` | Parses `skills/*.md` (frontmatter + body), `match()` picks a skill, `prompt_block()` puts it in the prompt, `save()` / `delete()` / `describe()`. |
| `nova/core/app.py` | `new_task()` (≈line 552) calls `skills.match()`; `run_skill()` (≈line 685) runs a **routine** step by step in code, or hands a **recipe**'s prose to the brain; `is_risky()` (≈line 323); `check_step()` / `look_check()` (≈line 752–805) = the existing look → act → check loop; `_agent_loop()` (≈line 962) incl. `PROMISE_RE` nudge and the "I don't think I finished" honesty fallback; `confirm_pending()` (≈line 2078). |
| `nova/actions/tools.py` | Tool functions + `TOOLS` schemas (skill tools ≈line 2563). Useful helpers: `_scan()`, `_form_boxes()`, `_is_password()`, `page_text()`, `read_page()`, `list_clickable()`, `_find_window()`, `_focus()`, `help_login()` (≈1476), `wait_for_code()` (≈1518), `open_windows_summary()`. |
| `nova/brain/retrieval.py` | `scores(query, texts)` — embedding cosine (bge-small) with a word-overlap fallback; `ready()`. |
| `nova/brain/llm.py` | `chat(model, messages, tools, …)` → `(stored, text, calls)`. |
| `tests/test_skills.py`, `tests/conftest.py` | Skill tests; `brain` fixture = scripted `FakeBrain` + recorded tool calls; `skills.DIR` points at a temp folder. |
| `skills/*.md` | The 10 shipped skills. `skills/.trash/` = deleted ones. |
| `data/task_log.txt` | Step-by-step trace of every request — use it to see real failures. |

### Observed failures this plan must fix (from `data/task_log.txt`, 2026-11-17)

1. "Where you find out?" → started `web-research` (fuzzy match on the trigger "find out about").
2. "Can you check the Crunchyroll latest anime and tell me which one should I watch?" → started `anime-time`
   (dark mode + fullscreen setup) instead of reading the page.
3. `anime-time` is a **recipe** (6 of its 8 lines are tool steps, 2 are prose), so the 4B model got all 8 lines,
   called **no** tool, and said "you're all set on Crunchyroll" (false success).
4. `save_skill` rewrites the whole file, keeps no old version, records no outcomes.

### Ground rules

- **Branch**: work on a new branch `feat/skills-v2`, one commit per phase (or per sub-step if large). Don't commit to
  `master`.
- **Uncommitted work**: the working tree already has the user's uncommitted forms/login work (`nova/actions/forms.py`,
  `tests/test_forms.py`, changes in `tools.py`, `toolpick.py`, `app.py`, `static/index.html`). Ask the user whether
  to commit it first. Never mix it into a skills commit.
- **Tests**: run `python -m pytest -q tests/test_skills.py` (and any new test files) after every change, and the
  full suite (`python -m pytest -q`) at the end of each phase. Warn the user first: `test_watcher.py` opens real
  message boxes for a few seconds. **Never run tests while Nova is running** (they load Whisper on the 6 GB GPU and
  slow the user's Nova down) — ask the user to quit Nova, or skip the full suite and say so. Set
  `PYTHONIOENCODING=utf-8` for any script that prints Nova's replies.
- **No new pip packages** unless unavoidable. Smart App Control on this laptop blocks some unsigned native DLLs.
  Use `ctypes`, `uiautomation`, and `keyboard` (already installed). If you must add one, `import` it after install to
  prove it loads, and pin it in `requirements.txt`.
- **No extra local models.** The GPU is full (qwen3-vl:4b + Whisper). Anything "smart" that isn't one small decision
  goes to the cloud brain when one is configured (`cloud.parse(model)` / `task_brain()`), else is skipped.
- **Safety is not negotiable**:
  - Every tool step still goes through `is_risky()`. A skill can never bypass "ask first".
  - Never record, store, type, read back or send passwords, OTP codes, card numbers. Reuse `_is_password()`,
    `knowledge.SECRET`, `forms` rules. Logins go through `help_login` / `wait_for_code` only.
  - Never auto-click Pay / Place order / Submit / Send / Save password.
- **Style**: match the codebase — short functions, comments that say *why* (often with the real incident that
  caused the code), plain-English strings for the brain, `task_log()` lines for anything worth debugging later,
  `think()` for what the user should see in the thought log.
- **Backward compatibility**: every existing skill file must keep working until it is converted. Old-style lines
  (`- tool key=value`, `say …`, `wait N`) stay valid.

---

## 1. The v2 skill file format (the target for every phase)

Still Markdown + YAML-ish frontmatter (parsed by the existing `_front()`; keep it simple `key: value`, lists as
`[a, b]` or comma-separated). Add `format: 2` so the parser knows which rules apply.

```markdown
---
name: anime-time
format: 2
version: 1
description: Sets up Crunchyroll for watching in Chrome - dark mode, open the site, sign in if needed, fullscreen. Use only when the user asks to start anime time or set up/open Crunchyroll to watch.
examples: [anime time, set up crunchyroll, crunchyroll fullscreen, let's watch anime on crunchyroll]
not_for: [check the latest anime, what new anime came out, which anime should i watch, recommend an anime, who voices gojo]
requires_any: [anime time, crunchyroll]
risk: disruptive
invoke: confirm_if_fuzzy
status: active
---
s1. set_dark_mode enabled=true
s2. open_website url_or_search=https://www.crunchyroll.com
    expect: window_open "Crunchyroll"
    on_fail: retry 1, then stop "Crunchyroll didn't open"
s3. login_if_needed window="Crunchyroll"
    on_fail: ask "I couldn't sign in to Crunchyroll - want to sign in yourself, then I'll carry on?"
s4. window_action action=focus, title_or_app=Crunchyroll
    expect: front_window "Crunchyroll"
s5. press_keys keys=f
    expect: fullscreen
    on_fail: brain "Make the Crunchyroll video player fullscreen"
s6. say Crunchyroll is ready, boss. Enjoy!
```

### Frontmatter fields

| Field | Meaning | Default |
|---|---|---|
| `name`, `description` | as today. Description = what it does **and when to use it**, third person. | — |
| `format` | `2` for the new rules. Missing = v1 (old file). | 1 |
| `version` | bumped by every save/edit. | 1 |
| `examples` | phrases the user says to start it. **`when:` is read as an alias** for v1 files. | [] |
| `not_for` | near-miss phrases that must **not** start it. A match vetoes the skill. | [] |
| `requires_any` | at least one of these words/phrases must be in the request, or the skill can't fire (exact or fuzzy). | [] |
| `risk` | `read_only` (only looks/reads/says), `disruptive` (changes screen/settings/windows), `consequential` (sends, deletes, installs, pushes, pays). | computed: `consequential` if any step tool is in `tools.RISKY`, else `disruptive` if any is in `app.ACT_TOOLS` or changes settings, else `read_only` |
| `invoke` | `auto`, `confirm_if_fuzzy`, `explicit_only` (fires only on an exact example/name). | `confirm_if_fuzzy`; drafts are forced to `explicit_only` |
| `status` | `draft`, `active`, `deprecated`. Deprecated skills never match. | `active` |

### Body: steps

One step per line: an optional id (`s1.`, `s2.` …; auto-assigned `s1..sN` if missing; old `- ` / `1.` bullets still
accepted), then one of:

| Step | Example | What runs it |
|---|---|---|
| tool call | `open_app name="Visual Studio Code"` or `set_dark_mode {"enabled": true}` | executor, through `is_risky()` |
| `say <text>` | `say Ready, boss!` | executor (spoken at the end) |
| `wait <secs>` | `wait 2` | executor (max 30) |
| `if <check> goto sN` | `if page_has "Log In" goto s4` | executor |
| `confirm "<question>"` | `confirm "Commit and push with message '{message}'?"` | executor pauses the skill and asks Allow/No (see Phase 3.4) |
| `brain "<instruction>"` | `brain "Suggest a short commit message from the changed files" tools: list_clickable` | **one** small model call for this step only (Phase 3.3) |
| `report "<instruction>"` | `report "Give a crisp two-sentence health report with the key numbers"` | last step only: the brain words the final answer from the step results |

Indented lines under a step (any amount of leading spaces):

- `expect: <check>` — postcondition the **executor** checks after the step (Phase 3.2). Without one, the step
  counts as OK when the tool result doesn't start with `Error` / `Not done`.
- `on_fail: <action>` — one of `retry N` · `stop "<msg>"` · `ask "<question>"` · `brain "<instruction>"` · `skip`
  · `goto sN`. Can chain: `retry 1, then stop "…"`. Default: `stop` with an honest message naming the step.
- `note: <text>` — free text for humans/the brain (e.g. the user's own words from a demonstration). Ignored by the
  executor.

Slots: keep `{input}` and `{input_url}` exactly as today (`skills.fill()`, `guess_input()`).

### Sidecar files (written only by code, never by the model)

```
skills/.stats/<name>.json     runs, ok, failed_by_step {s3: 2}, last_error, last_used, last_ok,
                              denied (fuzzy matches the user said no to), learned_not_for [utterances]
skills/.history/<name>.jsonl  one line per change: {ts, version, parent, author: user|local|cloud|recorder,
                              cause: utterance or run id, ops: [...]}
skills/.history/<name>.v<N>.md  full copy of every previous version (so any change can be undone)
```

The parser must ignore dot-folders (`load()` globs `*.md` in the top folder only — keep it that way).

---

## Phase 1 — Matching that can say "none" (P0)

**Goal:** fix failures 1 and 2. A skill only fires when the request really asks for it; uncertain matches ask first.

### 1.1 `skills.py`: new fields + `pick()`

- Parse `examples` (alias `when`), `not_for`, `requires_any`, `risk` (with the computed default), `invoke`,
  `status`, `format`, `version`. Keep `s["when"]` filled (= examples) so old callers keep working.
- Add `pick(query) -> (skill | None, how)` where `how` ∈ `"exact" | "fuzzy" | None`. Keep `match(query)` as
  `return pick(query)[0]` so nothing else breaks.
- Normalise the query: lowercase, strip punctuation, and strip leading fillers
  (`hey nova`, `nova`, `ok`, `okay`, `please`, `can you`, `could you`, `will you`, `so`) — loop until none left.
- Algorithm (in this order):
  1. Candidates = skills that are not `broken` and not `deprecated`. Drafts are `explicit_only`, so they can only
     fire in the exact tier (step 3).
  2. **Veto**: drop a skill if the normalised query equals, or contains (as whole words), any of its `not_for`
     phrases or its `learned_not_for` utterances from `.stats`.
  3. **Exact tier**: for each phrase `p` in [name with `-`→space] + examples:
     - fires if `q == p`, or `q` starts with `p` followed by a space (the rest is the `{input}`), or
       (`p` has **≥ 2 words** and `" p "` is inside `" q "`).
     - A **one-word** phrase (e.g. `research`) fires only when `q == p` or `q` starts with it **and** the skill
       `needs_input`. This alone stops "what is the research on sleep" and "where you find out".
     - `requires_any` (if set) must also be present. Return `(skill, "exact")`. If two skills fire, prefer the
       longest phrase.
  4. **Fuzzy tier** (skip skills with `invoke: explicit_only`): `retrieval.scores()` over each skill's
     description + examples; per skill keep its best score. Accept the top skill only if **all** hold:
     - `best >= FUZZY_FLOOR` (constants at the top of the file: `0.75` when `retrieval.ready()`, `0.6` on the
       word-overlap fallback — the current values),
     - `best - second_best_other_skill >= FUZZY_MARGIN` (start at `0.05`),
     - its `requires_any` is satisfied (if set),
     - for `risk != read_only`: the query shares at least one **content word** (not a stopword; keep a small
       `STOPWORDS` set) with the skill's name/examples.
     Return `(skill, "fuzzy")`.
  5. Else `(None, None)`.
- Log every decision: `task_log(f"SKILL MATCH {how} {name} score={…:.2f} 2nd={…:.2f}")` or
  `SKILL NO MATCH best=… why=…` (veto / floor / margin / requires_any). These lines are what the user will tune from.

### 1.2 `app.py`: use `how`

- `new_task()`: call `skills.pick(clean)`; store `state["task"]["skill"] = name` and `["skill_how"] = how`.
  Keep the `think("📜 This sounds like my … skill")` only for exact; for fuzzy say
  `"📜 This might be my '…' skill."`.
- `skills.prompt_block(query, matched, how)`:
  - exact → as today ("Call run_skill(name=…)").
  - fuzzy → `"The user MIGHT want your skill '<name>' (<description>). Call run_skill only if the request is really
    asking for that. If it's a question or something else, ignore the skill and just help."`
- `is_risky()`: add, before the autonomy checks:
  `run_skill` whose skill is the task's fuzzy match, with `risk != read_only` and `invoke != auto` → `True`.
  The Allow/No question must name the skill: make `ask_label()` (or wherever the pending question text is built —
  find it from `ASK` / `"✋ I need your OK"`) say `Run my 'anime-time' skill (Sets up Crunchyroll…)`.
- `confirm_pending(False, …)` for such a pending `run_skill`: append the request text to that skill's
  `.stats` `learned_not_for` (normalised, max 50 kept) and `denied += 1`. Next time the same sentence is vetoed.
  (Write the small `.stats` helpers now — `skills.stats_get(name)`, `skills.stats_update(name, **changes)` — Phase 4
  extends them.)

### 1.3 Fix the shipped skill files (`skills/*.md`)

- `web-research`: remove bare `research`, `look up`, `find out about`, `google it`, `read up on`. Keep
  `do some research on`, `research about`, `search the web for`. Add
  `not_for: [where did you find out, where you find out, how do you know, what is the research on, look up my]`
  and `requires_any` is not needed (it needs input).
- `anime-time`: `not_for` + `requires_any` exactly as in section 1 (format conversion is Phase 3).
- `morning-check`: `good morning` alone is fine as an exact (whole utterance) trigger; add
  `not_for: [good morning nova how are you]` only if tests show a problem.
- Review the other examples for one-word or very generic triggers; tighten them the same way.

### 1.4 Tests (`tests/test_skills.py` + new `tests/test_skill_matching.py`)

- Every existing test still passes (`"hey nova, movie time!"` and `"Let's watch a movie"` still match exactly).
- New, using copies of the shipped `web-research.md` and `anime-time.md`:
  - `pick("Where you find out?") == (None, None)`
  - `pick("what is the research on sleep")[0] is None`
  - `pick("look up my battery")[0] is None`
  - `pick("Can you check the Crunchyroll latest anime and tell me which one should I watch?")[0] is None`
    (vetoed by `not_for` / no exact phrase; note retrieval may be on the word-overlap fallback in tests)
  - `pick("research quantum batteries")` → `web-research`, exact, and `guess_input` → `quantum batteries`
  - `pick("anime time")` → `anime-time`, exact
- Conversation test: a fuzzy match of a disruptive skill → `out["pending"][0]["tool"] == "run_skill"` and nothing
  ran; `confirm_pending(False)` → the sentence is in `learned_not_for`, and `pick()` of the same sentence returns
  `None` afterwards.

**Done when:** the six cases above pass, old tests pass, and `SKILL MATCH` / `SKILL NO MATCH` lines appear in the
task log.

---

## Phase 2 — Nova can't say "done" unless it was done (P0)

**Goal:** fix failure 3's second half (false success) for every path, not just skills.

### 2.1 Routines end with the executor's words

- In `app.run_skill()` (routine path) build the final line **in code**:
  - all steps OK → the skill's `say` lines (with `{input}` filled), or `"Done - ran my '<name>' skill."` if none.
  - some failed → `"I ran my '<name>' skill, but step <id> (<tool>) didn't work: <first line of result>."` plus
    the `on_fail` message if one stopped it.
- Set `state["skill_final"] = line`. In `_agent_loop()`, right after `execute(...)` returns: if the turn's calls were
  only `run_skill` of routine(s) and `state.pop("skill_final", None)` is set, append
  `{"role": "assistant", "content": line}` to `state["messages"]` and `return result(line)` — **no second brain
  call**. (The tool result message is already in history, so the history stays valid.)
- Update `test_routine_runs_every_step_without_the_brain`: now `len(fake.seen) == 1` and the reply is the
  `say` text. Add a test where one step returns `"Error: …"` and the reply names that step.

### 2.2 Recipe guard (safety net until Phase 3 converts them)

- When `run_skill` returns a recipe's instructions, set `task["recipe"] = {"name": …, "tools": s["tools"],
  "calls_after": 0}`; count every later tool call in that task.
- In `_agent_loop()`, when the model stops (`not calls`) and `task["recipe"]["calls_after"] == 0` and the recipe
  names at least one tool: nudge once — `"[note from Nova's system] You haven't done any step of the skill
  '<name>' yet - nothing has happened. Do its first step now with a tool."` If it stops again with zero calls,
  replace the reply with `"I didn't actually run my '<name>' skill - something went wrong. Want me to try again?"`.

### 2.3 Claimed-but-not-done check

- Add `DONE_CLAIM_RE` near `PROMISE_RE` for past-tense completion claims (`all set`, `is ready`, `i've (opened|
  started|turned|set|closed|pushed|sent)`, `done`, `opened`, `it's on`…). Keep it narrow; test it against the
  replies in `data/task_log.txt`.
- When the model stops with **no tool call in the whole request**, the reply matches `DONE_CLAIM_RE`, and the task had
  a skill match (`task["skill"]`) or the request was an action (toolpick picked an ACT tool for it — use
  `task["skill"]` only if that is too fuzzy): nudge once like `PROMISE_RE` does; if still no call, replace the reply
  with an honest `"I haven't actually done that yet."` + the question. Log `HALLUCINATED DONE` in the task log.

### 2.4 Log raw model output that looks like a tool call written as text

- Open bug ollama#14493: Qwen3-family tool calls sometimes come back as plain text, so nothing runs. In
  `llm.chat()` (local path), when tools were offered, no `tool_calls` came back, and the text contains
  `<tool_call>`, `"arguments"`, starts with `{"name"`, or has an unclosed `<think>`, write
  `task_log("RAW TOOLCALL-AS-TEXT: " + text[:500])`. **Only log** — first check what `got_text` / the existing
  `{`-holding code already does with such text and don't change that behaviour in this phase.
- Tell the user in the phase summary how many such lines appeared after a day of use; if many, the fix is a Modelfile
  template override, which is a separate task.

**Done when:** the anime-time conversation (scripted FakeBrain answering "you're all set" with no calls after a
recipe `run_skill`) ends with the honest line, and routines end without a second brain call.

---

## Phase 3 — Executor v2: checked steps, branches, one-step brain calls, safe login (P1)

**Goal:** routines check every step themselves; recipes disappear (prose becomes single `brain` steps).

### 3.1 Parser (`skills.py`)

- Implement the section-1 body grammar: step ids, `if … goto`, `confirm`, `brain`, `report`, indented
  `expect:` / `on_fail:` / `note:`. Each parsed step: `{"id": "s3", "tool"|"say"|"wait"|"if"|"confirm"|"brain"|
  "report": …, "args": {…}, "expect": <parsed check or None>, "on_fail": [actions], "note": ""}`.
- `format: 2` files must be fully valid or `kind = "broken"` with the exact bad line (as today). A `format: 2` file
  is always a routine; there are no v2 recipes.
- v1 files parse exactly as before (keep the old code path; add v2 alongside).
- `goto` targets must exist and only jump **forward** (no loops). `report` only as the last step. Max 25 steps.

### 3.2 Checks (`nova/actions/checks.py`, new)

A small set of predicates the executor evaluates itself. Each returns `(ok: bool, seen: str)`. Each **polls** for up
to `timeout` seconds (default 8 s, 0.5 s interval) because pages load slowly.

| Check | Implementation hint |
|---|---|
| `window_open "<text>"` | any `_windows()` title/process contains text (case-insensitive) |
| `front_window "<text>"` | foreground window title contains text (`GetForegroundWindow`) |
| `page_has "<text>"` / `page_lacks "<text>"` | `page_text()` of the matching window (or the front one) contains text; also check `_scan()` names so buttons count |
| `fullscreen` | foreground window rect == its monitor rect (ctypes `MonitorFromWindow` + `GetMonitorInfo`) |
| `dark_mode on|off` | read the registry value `set_dark_mode` writes |
| `process_running "<name>"` | psutil |
| `result_has "<text>"` | the step's own tool output contains text |

Parse `expect:` lines into `{"check": name, "arg": …}`; unknown check names make the file `broken`. Write unit tests
with the window/page helpers monkeypatched.

### 3.3 Executor (`app.run_skill`, v2 path)

Keep v1 behaviour for v1 files. For v2:

- State object: `run = {"skill": name, "version": v, "at": "s1", "results": {id: first line}, "slots": {...},
  "ok": [], "failed": [], "said": []}` — kept in `state["skill_run"]` while running (needed for `confirm` resume).
- Loop over steps from `run["at"]`:
  - `check_stop()` before each step (Stop button works mid-skill, as today).
  - tool step → `is_risky` / `tools.run_tool` exactly like today's routine loop (keep the `settle(...)`,
    `activity`, `task["used"]` bookkeeping and the `SKILL STEP` task-log line).
  - then evaluate `expect` (or the default "no Error") → on failure run `on_fail` actions in order
    (`retry N` re-runs the step; `stop` ends with the message; `ask` → like `confirm` below but the answer
    "yes" means "the user fixed it, continue"; `brain` → 3.3b; `skip`; `goto`).
  - `if` → evaluate the check, jump or fall through.
- **3.3b `brain "<instruction>"` steps**: one bounded mini-loop, not the whole agent loop.
  - model = the cloud brain if this task has one available (`task_brain()` / settings), else the local model.
  - messages = a short system prompt (`"Do exactly this one step of the skill '<name>', then stop: <instruction>.
    Results so far: <run.results>. Use a tool; answer in one sentence when the step is done."`) + no chat history.
  - tools = only the tools named after `tools:` on the step, else the tools the instruction text mentions, else
    the task's picked tools.
  - up to 3 model turns; every call goes through `_execute_one` (approvals, logging). Then evaluate the step's
    `expect`. If it fails twice on the local model and a cloud brain exists, try once on the cloud brain.
- `report` step → one model call with the step results and the instruction; its text is the final reply.
- Final reply: built by code as in Phase 2.1 (or the `report` text). Record the run in `.stats` (Phase 4 adds the
  fields; for now at least `runs`, `ok`, `failed_by_step`, `last_error`).
- **Approval up front** (today's rule in `is_risky` for `run_skill`): keep asking once if any tool step would ask.
  Include tools inside `brain` steps' allowed lists in that check.

### 3.4 `confirm` step = pause and resume

- On a `confirm "<q>"` step: save `state["skill_run"]`, set `state["pending"] = [{"id": "skill-confirm",
  "name": "run_skill", "args": {"name": s, "resume_at": next_id, "input": …}}]`, make the Allow/No question show
  `<q>` (slots filled), and end the turn with the question as the reply.
- `confirm_pending(True)` → `run_skill` with `resume_at` continues from the saved run (approved=True for that
  call). `False` → `"Okay, I stopped my '<name>' skill before: <q>"`; stats record `stopped_by_user`.
- Test both paths with the FakeBrain fixture.

### 3.5 New tool `login_if_needed(window, wait_secs=150)` in `tools.py`

For skills and recordings — **never types or reads a password**:

1. Find the window. If the page shows signs of being signed in (no visible "Log in"/"Sign in" button **and** no
   password box — use `_scan()` / `_form_boxes()`), return `"Already signed in."`.
2. If there's a "Log in"/"Sign in" button but no form yet: click it (`click_element` logic), wait for the form.
3. Form visible (`_form_boxes()`):
   - user box and password box both `filled` (Chrome/password-manager autofill) → click the form's submit button
     (`Log in` / `Sign in` / `Continue` / `Next`; never `Pay`/`Submit order`).
   - otherwise → call `help_login(window, wait_secs)` (it opens the saved-login popup and waits for the user).
4. Afterwards: if a one-time-code box appears → `wait_for_code(window)`. If a `Windows Security` / Windows Hello
   window appears → say "Please confirm with Windows Hello" and wait for it to close.
5. Return a plain result: `"Signed in."`, `"Already signed in."`, or `"Still on the sign-in page - the user
   hasn't finished."`. Never include box values.
6. Don't touch Chrome's "Save password?" bubble.
- Add to `TOOLS`, to `ASK` (`"Sign in on a website"`), to `tools.RISKY` (it clicks), to the right toolpick group, to
  `FRIENDLY`. Tests with `_form_boxes` / `help_login` / the click monkeypatched: filled → submit clicked; empty →
  `help_login` called; already signed in → nothing clicked.

### 3.6 Convert the shipped skills to `format: 2`

| Skill | Conversion |
|---|---|
| `anime-time` | exactly as the section-1 example |
| `laptop-health` | `get_system_status`, `temp_files_size`, `list_top_processes sort_by=memory, limit=3`, then `report "Give a crisp two-sentence health report with the key numbers"`; `risk: read_only` |
| `morning-check` | `get_datetime`, `get_system_status`, `list_notes`, `read_notifications count=5`, `report "Greet the user warmly and sum it all up in at most three short sentences, skipping anything empty"` |
| `vscode-git-push` | steps with checks: open/focus VS Code (`expect: front_window "Visual Studio Code"`), `press_keys keys=ctrl+shift+g`, `brain "List the changed files in the Source Control panel; if none, say there is nothing to push" tools: list_clickable`, `brain "Suggest a short commit message from the changed files" tools: list_clickable`, `confirm "Commit and push with message '{message}'?"` (the brain step's answer fills `{message}` — store the brain step's one-sentence answer in `run.slots["message"]` when the step has `slot: message`), type + `ctrl+enter`, `click_element name="Sync Changes"` with `on_fail: brain "Push with the command palette (Git: Push)"`, final `brain "Look at VS Code: did the push work, fail, or show a sign-in box? Never type credentials" tools: look_at_screen`. Keep `risk: consequential`. |
| the routines (`movie-time`, `focus-mode`, `work-mode`, `youtube-search`, `web-research`, `code-research`) | add ids and the obvious `expect` lines (`window_open "Netflix"`, `front_window "Visual Studio Code"` …) |

`test_shipped_examples_all_parse` must pass and, for v2 files, assert `kind == "routine"`.

**Done when:** all shipped skills are v2 routines, the anime-time run in tests executes s1–s6 in code with checks
(helpers monkeypatched), and a failing check produces the honest final line naming the step.

---

## Phase 4 — Outcome stats, history and small edits (P1)

**Goal:** fix failure 4. Nova improves a skill one step at a time, every change can be undone, and results are
counted.

### 4.1 Stats

- `.stats/<name>.json` fields: `runs`, `ok` (all checks passed), `failed_by_step` `{id: n}`, `last_error`,
  `last_used`, `last_ok`, `denied`, `learned_not_for`, `stopped_by_user`, `corrections`.
- Updated by the executor after every run (one write per run). Corrupt/missing file = start empty.
- `describe()` / `list_skills` shows e.g. `anime-time (routine, 7/9 ok, last failed at s5)`.

### 4.2 History + versions

- Before any write of `<name>.md`: copy the current file to `.history/<name>.v<version>.md`, then write the new one
  with `version + 1`, then append a line to `.history/<name>.jsonl`.
- `save()` (full write): if the skill already exists, refuse unless `replace=true` is passed, with
  `"'<name>' already exists - change one step with edit_skill, or pass replace=true to rewrite it."`.
- New tool `undo_skill_change(name)` → restore the previous version file (and log it as a change).

### 4.3 `edit_skill(name, ops)` tool

`ops` = JSON list; each op is validated, applied in memory, the result re-parsed, and only written if it parses
and passes validation:

```json
[{"op": "replace_step", "id": "s5", "step": "press_keys keys=f", "expect": "fullscreen"},
 {"op": "insert_step", "after": "s2", "step": "set_volume action=up, steps=5"},
 {"op": "delete_step", "id": "s1"},
 {"op": "set_expect", "id": "s4", "expect": "front_window \"Crunchyroll\""},
 {"op": "set_on_fail", "id": "s4", "on_fail": "retry 1"},
 {"op": "add_example", "text": "crunchyroll time"},
 {"op": "add_not_for", "text": "latest anime"},
 {"op": "set_description", "text": "…"}]
```

Rules: tools must exist; `risk`, `invoke`, `status` can't be changed by an op (only by code or the user's settings
page); new ids continue the numbering (`s7`, …) and never reuse an id; max 5 ops per call; secrets refused
(`knowledge.SECRET`); the result message lists what changed and says how to undo. `author` in history = `cloud` /
`local` from the current brain.

Update the `save_skill` tool description and the system-prompt lines in `app.py` (≈line 159) so the brain uses
`edit_skill` for changes and `save_skill` only for new skills.

### 4.4 Corrections after a run

- After a skill run, remember `state["last_skill"] = {"name", "version", "ts", "failed": [ids]}`.
- If the next request comes within 3 minutes and matches the existing `CORRECTION_RE` (in `app.py`), add to that
  request's prompt facts: `"The user may be correcting your '<name>' skill (last run: <ok/failed at s5>). Its steps:
  <ids + one line each>. If they say how it should work, change it with edit_skill (small ops) after doing what they
  asked now. Don't rewrite the whole skill."` and `corrections += 1` in stats.
- The existing `review_turn()` knowledge-learning must not also save this as a generic lesson (check
  `review_turn`'s triggers and skip when `last_skill` applied).

### 4.5 Tests

Edit ops apply and bump version; invalid op (unknown tool, bad id, changing risk) is refused and the file is
unchanged; undo restores; `save()` of an existing name without `replace` is refused; stats increment after a run.

**Done when:** a correction like "no, also unmute" can become one `insert_step` op, visible in `.history`, undoable.

---

## Phase 5 — Skills from Nova's own successful runs (P2)

**Goal:** "remember that" / "save that as a skill" turns the task Nova just did into a skill, from the real tool
calls — not from the model retyping text.

- Record a trace for every task: in `_execute_one` append to `state["task"]["trace"]` →
  `{"tool", "args", "ok", "out": first line[:160], "check": task["done"] / step verdict if any, "t": ts}`.
  Keep the last finished task's trace in `state["last_trace"]` (with its goal).
- New tool `save_task_as_skill(name, examples, description="")`:
  1. Deterministic clean-up in code: keep OK steps in order; drop failed ones, `more_tools`, `escalate`,
     `list_skills`, pure look-ups that the final answer didn't use (keep it simple: drop `look_at_screen`,
     `list_clickable`, `read_page` unless they were the last step — then make them `report`); collapse identical
     consecutive steps; **refuse `click_at` steps** (screen coordinates break) — replace with
     `brain "<what the click was for: the model's own text before the call, if any>"`.
  2. If a cloud brain is configured: one call asking for JSON
     `{"slots": [{"step": id, "arg": key, "slot": "input"}], "expect": {id: "<check>"}, "not_for": [...],
     "description": "..."}` — validated against the parser; anything invalid is dropped, never fixed by hand.
     The cloud gets only the cleaned steps and the goal, never form values (none are in traces anyway — verify).
  3. Save as `format: 2`, `status: draft`, `invoke: explicit_only`, author `recorder` in history.
  4. Reply: the steps in short words + `"It's a draft - say '<first example>' to try it. After two runs that work
     I'll keep it."`
- Promotion: when a draft has `ok >= 2` (all checks passed) and no failures in its last 2 runs, set `status: active`,
  `invoke: confirm_if_fuzzy` (code writes it, history line `author: code`), and tell the user once.
- Add `save_task_as_skill` to the "remember how to …" guidance in the system prompt (≈line 159).
- Tests: a scripted task (open_website + press_keys) → `save_task_as_skill` → a draft with those two steps; a
  trace with `click_at` → that step became a `brain` step; draft promoted after two OK runs.

---

## Phase 6 — "Watch me": learn from what the user does on screen (P2)

**Goal:** the user says "watch me", does the task (desktop apps or browsing), says "done"; Nova turns it into a
draft skill using the Phase 5 pipeline.

### 6.1 Recorder (`nova/actions/recorder.py`, new)

- Tools `start_watching(goal="")` / `stop_watching(name="", examples="")`; voice: "watch me", "watch how I do
  this", "done" / "stop watching" (add to the system prompt guidance). Hotkey `ctrl+alt+w` toggles (use the
  installed `keyboard` lib, like the existing Ctrl+Alt+N).
- Only records between start and stop. Max 10 minutes / 300 events, then stops by itself. Avatar says
  `"👀 Watching - say 'done' when you're finished"`; the thought log shows each captured step.
- **Mouse**: a `WH_MOUSE_LL` low-level hook via `ctypes` (`SetWindowsHookExW`) on its own thread with a message
  loop — no new package. On button-up: `uiautomation.ControlFromPoint(x, y)` → control name, `ControlTypeName`,
  automation id, the top-level window title and process. Skip clicks on Nova's own windows (its avatar / desktop
  window — match by process id = this process or its children).
- **Keyboard**: `keyboard.hook`. Group printable keys into one `type_text` per focused control; shortcuts and
  Enter/Tab/Esc → `press_keys`.
- **Injected input**: low-level hook events carry `LLMHF_INJECTED` / `LLKHF_INJECTED`. Ignore injected events —
  that's Nova's own clicking/typing, not the user's.
- **Never record secrets**: if the focused control is a password box (`_is_password()`), or the window is a
  sign-in page (`_form_boxes()` has a password box), drop **all** typing on that page and emit a single
  `login_if_needed window="<title>"` step instead. Also drop typing when the focused control's name matches
  OTP/code/PIN/card/CVV patterns (reuse `forms.CODE_BOX` / `knowledge.SECRET`). Typed text that matches
  `knowledge.SECRET` is replaced by `{input}` and flagged.
- **Browser (Chrome / Edge)**: on each click/Enter in a browser window, read the address bar
  (`uiautomation` Edit control named like "Address and search bar") before and ~1.5 s after. Rules:
  - a click on a `HyperlinkControl` followed by a URL change → becomes `open_website url_or_search=<new URL>` when
    the skill is replayed (direct, robust), keeping the link's text as a `note:`;
  - typing in the address bar + Enter → `open_website url_or_search=<URL or search words>`;
  - in-page button clicks → `click_element name="<control name>", window="<site name>"`;
  - typing into a page box → `fill_in` (box name + text) — never for password/login boxes (above).
- Raw recording saved to `data/recordings/<ts>.json`, deleted after conversion unless the user says "keep it".
  Raw recordings never go to the cloud; only the cleaned steps do (Phase 5 step 2).

### 6.2 Conversion

- `stop_watching` turns events into the same step list shape as a Phase 5 trace and runs the same pipeline
  (cleanup → optional cloud slots/expect → draft). Collapse: consecutive `type_text` into one; click on a box then
  type → one `fill_in`; open app via Start menu clicks → `open_app name=<process/app name>`; a click with no
  control name → `brain "<window title>: click what the user clicked here"` with a `note:` (never `click_at`).
- Spoken words while watching (the conversation keeps listening) become `note:` lines on the nearest step
  (e.g. "this is where I pick the show"). Optional; skip if it complicates listening.
- Reply: `"Got it - <n> steps: open Crunchyroll, sign in if needed, click 'Simulcasts', … Say '<example>' to try
  it."` + offer to name it if the user didn't.

### 6.3 Tests

Unit-test the converter with synthetic event lists (no real hooks): link click + URL change → `open_website`;
typing into a password box → no text anywhere in the output, a `login_if_needed` step instead; injected events
ignored; unnamed control → `brain` step. Test hook start/stop only behind a manual flag (don't install real hooks
in CI tests).

---

## Phase 7 — Refine by doing it again: take-over and "show me this step" (P2)

**Goal:** each replay gets better from the user's corrections, one step at a time.

- During a skill run, keep the low-level hooks from Phase 6 in a light "listen for the user" mode (only
  non-injected mouse/keyboard events; no capture). If the user starts using mouse/keyboard mid-skill:
  pause before the next step and ask `"You took over at step <id> (<what it does>) - want to show me how to do that
  step? Say 'done' when you're finished."`
  - yes → record just that segment (Phase 6), convert it, and propose `replace_step` (or `insert_step`) ops for the
    current step; apply them as a new version after the user's OK; history `author: recorder`, cause = the run id.
  - no → continue or stop as the user says.
- After a failed step (check failed, `on_fail` exhausted): offer `"Step <id> didn't work - want to show me that
  step?"` with the same flow.
- When two fixes or recordings of the same step differ only in a part that changes each time (a show name, a search
  word), the cloud brain may propose turning that part into `{input}` (an `edit_skill` op, confirmed by the user).
- Tests: simulate a take-over event during a scripted run → pause + question; a recorded segment → correct
  `replace_step` op proposed with the right id.

---

## Phase 8 — Trigger test set and tuning (P2)

- `tests/skill_triggers.jsonl`: one line per example — `{"skill": "anime-time" | null, "text": "...",
  "kind": "positive|near_miss|chitchat"}`. Seed with the failures from section 0, every `examples` and `not_for`
  phrase of the shipped skills, 20+ chit-chat lines from `data/task_log.txt` REQUEST lines (no personal content).
- A test runs `skills.pick()` over all lines and asserts: zero near-miss/chit-chat lines fire a skill; every positive
  fires the right one. (Runs on the word-overlap fallback in CI.)
- Script `python -m nova.tools.skill_eval` (new, small): loads the real embedding model, prints false-trigger and
  missed-trigger rates and the best/2nd-best scores per line, and a suggested `FUZZY_FLOOR` / `FUZZY_MARGIN`. Also
  reads `SKILL MATCH` / `SKILL NO MATCH` lines and `learned_not_for` from `.stats` and adds them. The user tunes the
  constants from its output.
- When a skill is saved or edited (Phases 4–7), re-run its own lines from the file in code and warn in the reply if
  a near-miss now fires (don't block the save).

---

## Phase 9 — Long-term health (P3)

- **Screenshot critic**: for steps with no structured `expect` whose tool is in `ACT_TOOLS`, optionally (setting
  `skill_screen_checks`, default on when a cloud brain is set) reuse `look_check()` with a prompt that states what
  success looks like for that step and defaults to "not done" if unsure.
- **Retire**: a skill with `runs >= 5` and `ok/runs < 0.4` over its last 5 runs → `status: deprecated` (code writes
  it), tell the user once: `"My '<name>' skill keeps failing at step <id>, so I've stopped using it. Want to show me
  how it should work?"`. Deprecated skills still listed; `undo_skill_change` or the user can restore.
- **Duplicates**: on save, if a new skill's description+examples scores `>= 0.9` against an existing one, reply
  with `"That looks like my '<other>' skill - shall I change that one instead?"` and don't save until the user
  answers.
- **Cap**: max 40 active skills; when full, suggest retiring the least-used one.

---

## Phase 10 (only with the user's go-ahead) — learning browsing habits

Not part of this build. Sketch only: an opt-in setting (off by default) that logs **which sites** (domain only) and
apps the user opens and **when**, never page content or typed text, kept locally, with a page to view and clear it.
Weekly, Nova may offer: `"You open Gmail then YouTube subscriptions most mornings - want a skill for that?"` →
Phase 5 pipeline. Ask the user before building any of this.

---

## Final checklist for each phase

- [ ] Branch `feat/skills-v2`, one commit per phase, message says what and why. End each commit message with the
      attribution line the user's environment asks for.
- [ ] New and old tests pass (`tests/test_skills.py` + new files; full suite at the end of the phase, with the
      warnings in section 0).
- [ ] No passwords, codes or form values can reach the brain, the history files, stats, recordings or the cloud.
- [ ] Every skill tool step still passes through `is_risky()`.
- [ ] `task_log` lines for: match decisions, every step with its check verdict, edits, promotions, retirements.
- [ ] Shipped `skills/*.md` all parse; the format docstring at the top of `nova/actions/skills.py` and the short
      skills mention in `README.md` describe the v2 format.
- [ ] Phase summary for the user: what changed, what was tested, what was **not** verified on the real laptop
      (e.g. real hooks, real Crunchyroll login) and how they can try it.
