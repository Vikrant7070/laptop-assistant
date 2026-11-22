Status: active — 2026-11-22, phases 1–3 and Phase 4 step 0 built (step 0 merged as PR #24); step 4-pre (task state) added to the plan; the manager loop design waits for the user's go-ahead.

# Nova as a manager — plan

**Status (2026-11-22): phase 1 (Judge, `nova/actions/judge.py`), phase 2 (episode log + Learner,
`nova/actions/learner.py`, `nova/actions/questions.py`), phase 3 (workers, `nova/actions/workers.py`) and Phase 4
step 0 (research as a worker, PR #24) are built. Step 4-pre (task state, section 6), the manager loop (rest of
Phase 4) and phases 5–8 are not.** The user's answers are in
section 9. Build the phases in order, one at a time, each on its own branch/commit with tests, and only after the
user says go.

## 1. The goal (the user's words, summarised)

> Nova should learn **how I work** so she can help me finish my work faster. She should not be an expert in
> everything — she should be an expert in **managing**. Claude Code can write the website, DeepSeek (via OpenCode)
> can do coding jobs, research tools can research; Nova knows **me**, decides **what** needs doing and **who** does
> it, writes a good brief, checks the result and reports back. **A manager for my laptop, not a worker.**

Two kinds of learning, kept apart on purpose:

| | Learn from the user | Learn the best way |
|---|---|---|
| What | routines, apps, preferences, projects, decisions | quality, best practice |
| Source | activity log, existing history, "watch me", chats | expert workers + measured results (tests, scores) |
| Decides | **what** to do and **how the user likes it** | **how well** it is done |

Rule: *"the user did it this way" never means "this is the right way"*. When they differ, Nova suggests, never
silently copies or silently changes.

## 2. The shape

```
            User  ("make a website" / "research X" / "set up my day")
              │
              ▼
   ┌────────────────────────────────────────────┐
   │ NOVA = MANAGER                             │
   │ understand → ask → brief → pick worker →   │
   │ watch → judge → report → remember          │
   └──┬────────────┬────────────┬───────────┬───┘
      ▼            ▼            ▼           ▼
  Claude Code   OpenCode +   Research    Nova's own laptop
  (big code)    DeepSeek     worker      skills (skills v2)
      └────────────┴────────────┴───────────┘
                     │ results
                     ▼
     Judge (deterministic checks, tests, scores; vision only advises)
                     │
                     ▼
     Learner → skills, facts, worker notes, brief lessons
                     ▲
     Know-the-user sources: activity log, Chrome/PowerShell history,
     Claude Code transcripts, form profile (all opt-in, local)
```

What already exists and is reused (see memory note `nova-skills-v2`):

| Piece | Already in Nova | Gap |
|---|---|---|
| Router | `skills.pick()` exact → fuzzy | none for now |
| Skill runner | `run_skill`, format-2 skills, `expect:` checks | none |
| Learning from a run | `save_task_as_skill` (only when the user says "remember that") | automatic offers |
| Learning by watching | recorder "watch me", take-over lessons | always-on (opt-in) logging |
| Judge | `look_check` (vision) gates `task["done"]`; `checks.py` used only in skills | deterministic-first judge for all tasks |
| Facts memory | — | "site X is good for topic Y", user preferences |
| Workers | cloud brains as *chat* models only | workers as *agents* that do whole jobs |

## 3. Phase 1 — Trustworthy Judge (do first)

**Why first:** everything later learns from "did it work?". A wrong judge teaches wrong skills.

Today (`nova/core/app.py`): with the small local brain, `check_step` calls `look_check` after every on-screen step
from `LOOK_FROM_STEP = 2` — a screenshot to `qwen3-vl:4b` (~10 s, and it pushes the brain off the 6 GB GPU) — and
its `TASK DONE: yes/no` sets `task["done"]`, which tells the brain to stop (`≈1228`), pushes it back (`≈1654`) or
overrules its reply (`≈1665`). `nova/actions/checks.py` (`window_open`, `front_window`, `page_has`, `page_lacks`,
`fullscreen`, `dark_mode`, `process_running`, `result_has`, `evaluate()` with wait-and-retry) is the trustworthy
oracle but only skills use it.

Plan:
1. **Goal → checks at task start.** Code for common shapes ("open X" → `window_open X`, "go to site" →
   `page_has <domain>`, "turn on dark mode" → `dark_mode on`); otherwise the brain proposes 1–3 checks from the
   `checks.py` list, validated like skill `expect:` lines (invalid ones dropped, never fixed by hand).
2. **Order of trust:** any deterministic check fails → not done (vision can't overrule). All pass → done, no vision
   look. No check covers the goal (video really playing, email text right) → vision may look, **advisory only**: a
   "no" gives one nudge or a question to the user, never a hard overrule.
3. **Vision as a cheap warning:** at most once at the end, or when a check fails and the reason is unclear — good
   for error popups, sign-in walls, cookie banners.
4. Every task ends with a verdict record `{goal, checks, results, vision_note, verdict, source}` → episode log
   (phase 2).

**Done when:** a 4-step local task makes 0–1 vision calls instead of 3; a failed `window_open` check can't be turned
into "done" by vision; tests cover the trust order.

## 4. Phase 2 — Episode log + automatic Learner

1. **Episode log** (`data/episodes.jsonl`, local): one line per finished task — goal, steps (tool + args, secrets
   already stripped as in traces today), verdict, time taken, which brain/worker. Raw episodes deleted after ~30 days;
   lessons stay.
2. **Learner runs at idle time** (not during a task; the laptop is busy with the avatar and models):
   - Success + not already a skill → offer once: *"That worked — save it as a skill?"* → existing
     `save_task_as_skill` pipeline → draft → promoted after 2 good runs (unchanged).
   - Same kind of goal done 2–3 times → offer a skill with the changing part as an `{input}` slot
     (tea → coffee: compare the episodes, the differing argument becomes the slot).
   - **Facts memory** (`data/facts.json`): learned silently, shown on a viewer page, e.g. "for tea, site A and B were
     used in the final answer, C was opened and ignored". The research skill reads it next time and goes straight
     to the good sites.
3. **Learning without asking:** facts — silent (viewable, deletable). New skills — always asked.

**Done when:** a repeated research task offers a skill; the second "search for tea" uses the remembered sites.

**As built (2026-11-20):**
- Episodes are written when the next request starts, or by the idle Learner (60 s quiet, at most every 5 min). Secret-
  looking arguments are stored as `[hidden]`, and such episodes are never offered as skills.
- "Worked" (`learner.worked`): the Judge said done; or its checks covered only part of the goal but all passed and
  every step worked; or every background job the task started finished. Stopped, failed or unchecked = never offered.
- Offers go on the daily question list (`data/questions.json`, section 9 answer 4), not asked on the spot: one per
  answer at the laptop (never on the phone), with its reason, once a day; skipped 3 times = not asked again. Yes →
  `skills.draft_from_steps` (draft, promoted after 2 good runs). Single tasks need 2+ replayable steps.
- Slot skills: two worked episodes with the same steps, goals sharing their first words ("search for") and one
  argument holding the changed words → `{input}`; the shared words become the trigger phrase.
- Facts (`data/facts.json`): per research topic, hosts cited in the report ("good") vs opened and not cited
  ("ignored"). The next research on a close topic (≥ half the words shared) reads good sites first, skips ignored
  ones, and searches `site:<best>` if it isn't in the results. Shown on the Knowledge page, "unconfirmed" until ✓.

## 5. Phase 3 — Workers and the job tracker

1. **Worker list** (`data/workers.json`, editable on a settings page):

   | Worker | Good for | How it runs | Cost |
   |---|---|---|---|
   | Claude Code | new sites/apps, hard code, Git, PRs | headless CLI (`claude -p … --output-format json`) in the project folder | paid, strongest |
   | OpenCode + DeepSeek | ordinary fixes, bulk/cheap coding | `opencode run …` | cheap |
   | Research worker | deep research with sources | cloud brain + Nova's off-screen Chrome research | medium |
   | Nova's own skills | laptop tasks: apps, settings, forms, routines | skills v2 | free, local |

   *Check before building:* neither `claude` nor `opencode` was on the PATH of the shell checked on 2026-11-20 —
   find where they are installed, and confirm the exact headless flags with `--help` (do not rely on this table).
2. **Job tracker** (`data/jobs.json`): id, worker, brief, folder, status (queued / running / needs-user / checking /
   done / failed), started, cost so far, log file. Workers run as background processes; Nova reads their output,
   never blocks the voice loop.
3. **One writer per folder:** two jobs never touch the same project folder at once (a lock per folder).
4. **Budget + safety rules:** a cost limit per job and per day (settings), and workers may **never** push, publish,
   deploy, delete or pay — they finish, Nova checks, the **user** approves the outward step (same Allow/No flow as
   risky tools today).
5. **Status by voice and phone:** "how's the website going?" → job status; long jobs report through the existing
   Telegram remote when the user is away.

**Done when:** "fix the typo in my README" runs on OpenCode/DeepSeek in the background, Nova says when it's done,
and nothing is committed without the user's OK.

**As built (2026-11-21):** `nova/actions/workers.py`, tools `start_coding_job` / `commit_coding_job` (status and stop
are the existing `background_jobs` / `stop_background_job` - a coding job is a `coworker.py` job of kind "coding").
- **Where the workers are:** OpenCode = `%LOCALAPPDATA%\Programs\ai.opencode.desktop\resources\opencode-cli.exe`;
  Claude Code = the newest `%LOCALAPPDATA%\Claude-Data\*\claude-code\<ver>\*\claude.exe` (the desktop app's copy).
  Neither is on the PATH. `data/workers.json` (written with defaults) can set `exe`, models, budgets, timeouts.
- **Commands:** `opencode-cli run --standalone --format json --archived --title … -m <model> <brief>` (without
  `--standalone` the run goes to OpenCode's background service, which ignores the permissions passed in);
  `claude -p <brief> --output-format json --permission-mode acceptEdits --max-budget-usd N --disallowedTools …`.
- **Models:** OpenCode `opencode/deepseek-v4.1-flash-free`, then `opencode/big-pickle` when the free limit (429) is
  hit. Claude Code: its default model, $1 per job, $3 per day (from the jobs' reported cost).
- **Safety:** OpenCode permissions are deny-by-default with read/edit/search allowed (its v2 `execute` tool runs
  code and `subagent` keeps working after the run - both denied). Claude Code: git commit/push/reset/checkout, rm,
  gh, curl, WebFetch denied. For every process a worker starts, git push goes to a remote helper that doesn't exist
  and gh gets a dead token. Afterwards Nova compares with the "before" snapshot: a commit is undone with
  `git reset --soft` (edits kept) and reported; only the files the job changed are listed and later committed.
  Git folders only; one job per folder. The worker gets `PWD` = the job folder (OpenCode follows an inherited PWD
  over its real working folder - started from Git Bash it worked on Nova's own README).
- **Approvals (app.is_risky, any autonomy level):** OpenCode starts without asking; Claude Code always asks ("Send
  this coding job to Claude Code (paid, up to $1.00)"); `commit_coding_job` always asks; from the phone, every
  coding job asks. A failed OpenCode job offers Claude Code in its message; it never escalates by itself.
- **Reports:** the usual job_done path - spoken + chat "✅ … changed N files … Nothing is committed - say 'commit it'
  … Want to see it?" ("yes" opens the folder), Telegram when away; `background_jobs` shows the live step ("editing
  README.md") and, when done, the changed files. History in `data/jobs.json`, raw output in `data/jobs/<key>.log`.
- **Live check (2026-11-21), real Nova (local qwen3-vl:4b, GPU shared with child-brain training):** "Fix the spelling
  mistakes in the README.md of my project at …" → start_coding_job → DeepSeek free limit → big-pickle, done in 23 s,
  spoken "finished … changed 1 file"; "how's the coding job going?" → background_jobs → correct; "commit it" →
  Allow/No → Allow → only the job's file committed, nothing pushed. Two fixes came out of it:
  1. the tool picker cuts each tool's text to 128 tokens, so start_coding_job's hints never counted ("fix … README of
     my project" ranked it 22nd and she opened Notepad) - `toolpick.CODING` words (readme, repo, bug, refactor, my
     project/code, opencode, commit…) now always bring the coding tools;
  2. "coding job" in a status question routed to the coder brain (269 s, guessed "still running" without a tool) -
     `app.JOB_STATUS_RE` keeps job-status questions on the main brain.
- **Added later the same day:**
  - **⚙ Workers tab** (`/api/workers`, `workers.overview` / `save_settings`): installed / signed-in state with the
    sign-in command, models and fallbacks, Claude Code limits per job and per day, time limits, spent today, recent jobs.
    Only known fields are saved; numbers are clamped; bad model names and missing programs are refused.
  - **Queue instead of refuse:** a job for a busy folder waits in line ("waiting in line behind job #N", stoppable,
    record status "queued"), at most 3 per folder; the record shows "checking" while Nova inspects the changes.
  - **Phone:** coding-job messages sent while the user is away end with what they can reply - "what changed?"
    (`show_coding_changes`: files + short diff), "commit it" (Yes/No first), or for a failed job "try Claude Code"
    (`retry_coding_job`: same brief and folder, Yes/No first). Telegram /status now lists running jobs (it looked for
    "running"; coworker jobs say "working").
  - **Claude Code sign-in:** the desktop app's bundled claude.exe is NOT signed in for use outside the app
    (`auth status` → loggedIn false; ~/.claude/.credentials.json holds no Claude Code login). Nova checks with
    `claude auth status` before a Claude Code job and tells the user the one command to run
    (`& "<claude.exe>" auth login`); a "Not logged in" answer becomes the same message. Workers also get an
    environment without inherited `CLAUDE*` variables or a local `ANTHROPIC_BASE_URL` (Nova started from a Claude desktop
    session handed those on). A real Claude Code job has not run yet - it needs that sign-in first.
- **Still open:** a real Claude Code job, left for later (2026-11-21). The research worker was moved to Phase 4,
  step 0, and is now built (see section 6).

## 6. Phase 4 — The manager loop (brief writer)

**Step 0 (first, before the manager loop): research becomes a worker.** Research today runs on its own path
(`research_in_background` in coworker.py): it works, but it isn't in the worker system Phase 3 built. Move it in, so
the manager can pick from one list:
- listed in ⚙ Workers with its own settings (how many sources, whether the cloud brain may write the report);
- recorded in `data/jobs.json` like coding jobs (shown under "Recent jobs", findable later: "the research from Tuesday");
- the same status / stop / retry tools and phone hints as coding jobs;
- `pick_worker` can choose it, so "research the best library, then add it to my project" becomes research → coding.
What the user sees when they say "research X" stays the same.

**Step 0 as built (2026-11-21, branch `feat/manager-phase4`):**
- **The research worker:** `workers.research()` is the way in. `research_in_background` calls it, and it wraps
  `coworker.research` (which still does the searching, reading and writing). Its `start_job` hook records the job.
- **Settings:** `data/workers.json` → `"research": {"max_sources": 3, "cloud_report": true}`.
  - Edited in ⚙ Workers → 🔎 Research: pages to read (1–5) and "the cloud brain may write the report".
  - When that box is off, the report goes to `write_local`, which is the laptop's own model with no cloud fallback.
  - A number of pages the user asks for themselves still wins (at most 5).
- **Job history:** each research job is saved in `data/jobs.json` as worker `"research"`, with its topic, focus, report
  path, sources, and whether the local brain wrote it. It shows under ⚙ Workers → Recent jobs ("· 3 sources").
  - Coding-only lookups (`commit`, `show_coding_changes`) skip research records, so "commit it" never picks one up.
  - `last_record(which)` finds a job by number, by words, or by day ("the research from Tuesday", "yesterday").
- **Status / stop / retry:**
  - Status and stop work through `background_jobs` / `stop_background_job`, as before.
  - `retry_coding_job` (name kept) now retries any job: with no worker named, the newest job runs again as research
    (free, no ask) or goes to Claude Code (asks). `worker="research"` finds the newest research.
  - New tool `research_report` reads the saved report back, short enough for a text message.
  - After a restart, `show_finished_work` opens a past report from the job history.
- **Phone:** a finished research job's away message ends with *Reply "send the report"*; a failed one ends with
  *Reply "try again"*.
- **`pick_worker(worker, size, kind)`** returns `"research"` for `worker="research"` or `kind="research"`.
- **Where the code is (after master's package refactor, merged 2026-11-21):**
  - Tools: `nova/actions/tools/notes.py` (`@tool`).
  - Writer and phone hints: `nova/core/app/integrations.py`.
  - Retry approval: `nova/core/app/session.py`.
  - `research_report` is also listed in the `notes` table in `tools/__init__.py`.
- **Tests:** `tests/test_research_worker.py`.
- **Live check (2026-11-21):** the ⚙ Workers tab showed the Research settings (3 pages, cloud on) on the real Nova,
  started from the worktree.
  - "research the health benefits of green tea" matched the user's `web-research` skill, and the brain called
    `research_in_background` (34 s).
  - Nova's reply was the same as before.
  - Google → DuckDuckGo fallback; 3 pages read, 2 usable; report written; "done, from 2 sources" spoken after 64 s.
  - `data/jobs.json` got a `research` record (done, report path, 2 sources). ⚙ Workers → Recent jobs showed
    "#1 Research · done · 2 sources".
  - Gotcha: while a child-brain training run held the GPU at 100%, the local brain never answered. Two tries ended
    with "can't reach Ollama" after 20 min. The run was paused for the test (process suspended), then resumed.
- **Live re-check after merging master's packages (2026-11-21):**
  - On the merged branch, with the GPU free, "research …" worked 2 out of 2 times: done in 83 s and 118 s, recorded,
    and shown in Recent jobs.
  - The brain first took stray steps: `list_windows`, and `window_action focus` on the user's Netflix Chrome window.
  - With the GPU busy, it only called `list_windows`, twice.
  - Plain master went straight to research (1 out of 1).
  - The brain is offered the same tools as before the merge. Master's refactor changed their order, and the branch
    adds 2 tools (`research_report`, `retry_coding_job`) to the co-worker group.
  - Too few runs to blame either change.
- **Fix (2026-11-21): an exact routine skill match runs without a brain turn.**
  - When the request names an active routine skill and nothing more, `skills.direct_args` builds the `run_skill`
    call. A request with "and then …" or "also …" still goes to the brain, and so does a needed `{input}` that can't
    be read from the request.
  - `skillrun.plan_direct` puts that call on the task. On the first turn, `_agent_loop` uses it through
    `skillrun.direct_call` instead of asking the brain.
  - The usual approvals and checks still apply, and the call is stored in the history like the brain's own call.
  - `conversation.py` was exactly at the 800-line guard. Its `nova.brain` imports were merged into one line to make
    room.
  - **Live:** "research the best budget mechanical keyboards" got its reply in 3 s (it was 30–230 s), with no
    brain call and no window steps. The job was done in 58 s.

For any request that is bigger than a laptop skill:

1. **Understand** — what is the real goal; is it a job for a worker or a skill?
2. **Ask** — at most 2–3 short questions, only when the answer changes the work ("landing page or with docs?").
   Questions answered before (facts memory) are not asked again.
3. **Brief** — a fixed template: goal; context about the user (stack, style, where projects live, past decisions —
   from facts/profile, secrets never included); requirements; files/folder; **what done means** (the checks from
   phase 5); what the worker must not do.
4. **Pick the worker** — from the worker list + worker notes (phase 2 Learner also records "worker X did job type Y
   in N tries"). Big/new → strongest; small/routine → cheapest that has worked before.
5. **Watch** → **Judge** (phase 5) → send back once or twice with the exact failing check → **Report**: a short
   summary, a screenshot/link, and the one decision the user has to make.
6. **Remember** — which brief worked, which worker, what the user picked.

**Done when:** "make a website for my Nova project" goes ask → brief → Claude Code → checks → one fix round →
"it's ready in `nova-site/`, publish?" without the user writing the brief.

### Manager loop — proposed design (2026-11-21, waiting for the user's go-ahead)

**The main idea:** code does the managing, and the small local brain only fills small gaps. The local 4B brain on a
shared GPU takes 20–90 s per answer and can't write a good brief, so the steps below are code wherever they can be.

A new module, `nova/actions/manager.py`, and one new tool, `hand_off_job(request)`. Fixed word rules bring the tool
in, the same way `toolpick.CODING` does: build/make/create + site/app/page, "research … then …", and "my project".

1. **Understand (triage)**
   - Word rules sort the request into one of four kinds: `research`, `coding`, `research→coding`, or `laptop`.
   - `laptop` keeps today's path (skills and tools).
   - The brain is asked only when the rules can't tell, and then with a one-word answer.
2. **Ask (at most 2 questions)**
   - Each kind has a fixed question list. Each question has a fact key, for example:
     - coding: which folder? new or existing project? language or stack?
     - website: one page or with docs? a style to follow?
   - A question is skipped when the request already answered it, or when `facts.json` / the profile already knows
     the answer.
   - The answers are saved as facts, marked unconfirmed, so they are never asked again.
   - Questions are asked in the chat (and on the phone when the user is away). One message holds up to 2 questions.
3. **Brief**
   - A fixed template, filled by code:
     - Goal: the user's own words.
     - About the user: stack, style, and where projects live, from the profile and facts. Anything that looks like a
       secret is dropped.
     - Requirements: the request plus the answers.
     - Folder.
     - Done means: the checks from step 5.
     - Must not: today's `BRIEF_RULES`.
   - A cloud brain, if there is one, may tidy the wording of the requirements. The local brain never writes the brief.
   - The brief is shown in the chat (collapsed) and stored with the job.
4. **Pick the worker**
   - `pick_worker` also reads the job history: how often each worker succeeded at this kind of job.
   - Small or routine jobs go to the cheapest worker that has done this kind of job before. Big or new jobs go to
     Claude Code, which still asks and keeps its budgets.
   - For `research→coding`, research runs first. Its headline and sources go into the coding brief, then the coding
     job starts.
5. **Watch → check → send back → report**
   - When a job ends, quick checks run (a small first version of Phase 5):
     - coding: the files changed are only inside the folder, and the project's tests pass when a known command
       exists (`pytest`, `npm test`), run with the same safety guard as the worker;
     - research: at least 2 sources are cited.
   - If a check fails, the job goes back once to the same worker with the exact failing check ("tests fail:
     test_x …"). If it still fails, the user is told plainly.
   - The report is one line, plus the one decision the user has to make (commit? see it? publish?).
6. **Remember**
   - The job record stores the brief, worker, rounds, check results, and what the user chose (committed or not).
   - The Learner turns that into worker notes ("OpenCode did small README fixes 3/3"), which step 4 uses.

**Build order (each its own commit and live check):**
- **4-pre:** the task state (below): one working memory for Nova's own laptop tasks and worker jobs, plus a live
  task strip and a `/tasks` page to watch them. 4a's brief is built on it.
- **4a:** triage, questions, brief. For now it shows the brief and the user says "go".
- **4b:** worker pick from history, plus the research→coding chain.
- **4c:** checks, one send-back round, report.
- **4d:** remember and worker notes.

**The user's answers (2026-11-21):** for the first phases, **every** manager job shows its brief and waits for the
user's "go", cheap OpenCode jobs included. The user will relax this later, once they have seen how the manager
performs. Build it as a setting (default: ask for every job) so it can be loosened without a code change. Direct
`start_coding_job` calls keep today's rules: OpenCode doesn't ask, Claude Code asks.

### Step 4-pre — Task state: one working memory for every task (added 2026-11-22, waiting for the go-ahead)

**Why (what happened on 2026-11-21, from `data/task_log.txt`):**
- 21:33 "Is my laptop clean?" She started her answer. At 21:33:46 barge-in stopped her on her **own echo** (it
  "heard" *"Not too bad, boss. R.E.M. is a big…"*, her own words).
- The user said "continue, you were saying something". She answered "I'm here and ready". Asked "do you remember
  what you were saying?", she said "I remember you said 'Going on'".
- From 21:35, Nova was quit and restarted about once a minute (24 `QUIT (asked)` lines in the log). Each restart
  starts her with no memory.

**The gaps this shows:**
1. **The chat lives only in memory** (`session.state["messages"]`). A restart, a crash or a quit wipes it. A
   character switch clears it on purpose.
2. **An unfinished laptop task isn't recorded anywhere.** After a Stop or a restart there's no "I was doing X, I got to
   step 3". Worker jobs already survive a restart through `jobs.json`; Nova's own tasks don't.
3. **A cut-off reply isn't marked as cut off.** The whole reply goes into the history as if she said all of it, and
   "continue" has no code path. It goes to the small brain, which didn't make the connection, even with a learned
   lesson about "keep going".
4. **Barge-in can take her own echo for the user** (see the barge-in notes). This is a separate fix on the voice
   side; listed here because it started the chain.

**What exists today:**
- `trimmed_history` keeps the request in front of a long task, so the goal never scrolls away.
- `task["did"]` lists the tool calls that really ran. The brain sees it only in the one "not finished yet" push
  (`conversation.py`, near the `nudged` check).
- The deep brain (`router.make_plan`, `deepseek-r1:8b`) writes plans in a good shape
  (`step - tool - how to tell it worked`, `RISKS`, `DONE WHEN`). It runs only on trigger words, a long request, or
  `route_task`. It ran **0 times** in the current task log. Its plan is pasted into the instructions as plain text;
  nothing tracks which step is done.

**The design: a task card, kept by code, shown to the brain on every step.**

```
TASK: clean my laptop and tell me how much space was freed
PLAN: 1 check disk ✅  2 clean temp files ✅  3 empty recycle bin ⏳  4 report
DONE (really): get_system_status → disk 77%; clean_temp_files → 937 MB freed
NOW: step 3
```

1. **One record per task** (`task_state`): goal, plan steps, steps done (with a one-line result each), the current
   step, checks, and status (`running` / `stopped` / `interrupted` / `done` / `failed`).
   - For a **laptop task** it's the card the small brain sees.
   - For a **worker job** it's the job record, and 4a's brief is the worker version of the same card. One system,
     not two.
2. **Code writes the card, never the brain.** A step is ✅ only when its tool really ran and its check passed (the
   Phase 1 Judge). The brain's own "done" is never enough.
3. **Where the plan comes from, in order:** a skill's own steps → a cloud brain, if one is on (seconds) → the deep
   brain (`deepseek-r1:8b`, about a minute with model swapping on the 6 GB GPU) → no plan (only goal + done + now).
   The small 4B brain never writes the plan. Code drops any step that names a tool Nova doesn't have.
4. **When to plan:** not for one-step requests or a skill with fixed steps. Plan when code sees a real multi-step task:
   several actions joined by "and", no matching skill, or the task has already taken 3 or more steps.
5. **Re-plan:** if a step fails twice, the planner gets the card ("steps 1–2 done, step 3 failed: X") and fixes the
   rest of the plan. At most once per task.
6. **Small:** the card stays under ~250 tokens. Older tool results it summarises can be cut from what the brain sees,
   which frees room in the 8K window (~5300 tokens already go to instructions and tools).
7. **Saved to disk** (through `nova/store.py`) when it changes. After a restart, a Stop or an interruption Nova says
   "Before the restart I was on step 3 of cleaning your laptop. Should I continue?" She never carries on by herself;
   the user says go.
8. **Cut-off replies:** when barge-in stops her, the stored reply is cut to what was really spoken, marked as cut off,
   and the rest is kept on the card. "Continue" / "go on" / "you were saying" are caught by code and finish the
   unsaid part, with no brain turn.
9. **Chat after a restart:** the last few turns are saved with the card and loaded back at start-up, so a restart
   doesn't leave her blank. (This is the "reload" half; the searchable diary is the upgrade plan's A2.)

**How to measure it:** run the task exam (`python -m nova.exam`, the user starts it) before and after, and compare
the long tasks (3+ steps). Expect the gain there, not on one-step jobs.

**Tests:** card built from real tool results only; brain claims don't tick a step; unknown tools dropped from a plan;
card saved and reloaded after a restart; "continue" after a cut-off finishes the unsaid part; the card stays under its
size limit.

**Related:** upgrade plan A2 (saved, searchable chat history) and B3 (steering a running task) are planned and not
built. The task state is what B3 would steer, so build it first.

#### 4-pre UI — see what Nova is doing (the user's request, 2026-11-22)

The user wants to **track what is going on**, so the task state needs a screen. Today what's shown is spread out:
the thought cloud (`/api/thoughts`, gone after a few seconds), ⚙ Workers → Recent jobs (worker jobs only), and
`data/task_log.txt` (raw, for developers). Nothing shows Nova's own tasks, their plan, or why one stopped.

**1. A live task strip in the main window** (`static/index.html` / `nova.js`, always visible while a task runs):
- One line: the goal, `step 3 of 4`, the current step, and how long it has been running.
- Click to open the card: the plan with ✅ / ⏳ / ❌ / ⏭ (skipped) per step, the one-line result of each done step,
  and which brain is working (4B / planner / cloud / a worker).
- Buttons: **Stop**, and when stopped or interrupted, **Continue** and **Drop it**.
- When a step waits for the user (an approval, a "go" on a brief), the strip says so plainly, in a different colour.

**2. A Tasks page at `/tasks`** (copy the `/childbrain` and `/knowledge` page pattern), one list for **both** kinds:
Nova's own laptop tasks and worker jobs (coding, research), newest first.
- Filter: running / waiting for me / stopped / done / failed; laptop tasks / workers.
- Each row: when, goal, who did it, status, steps done / total, time taken, and the Judge's verdict.
- Open a row: the full card, the plan and where it came from (skill / cloud / deep brain), every step with its tool,
  result and check, the brief (for worker jobs), and **why it ended**: done, Stop pressed, barge-in, restart, a check
  failed, out of steps. This answers "what happened?" without reading the task log.
- "Interrupted" tasks after a restart are at the top with **Continue** / **Drop it**.
- Worker jobs link to their existing actions (show changes, commit, retry, report).

**3. Phone:** `/tasks` must work at phone width, for the mobile view. Telegram stays "done or blocked only" (the
user's answer in section 9); its message gets a short "see it on the Tasks page" line, no new message types.

**Data:** the page reads the saved task-state records (point 7 above) through one endpoint, `GET /api/tasks`
(list) and `GET /api/tasks/<id>` (one card), plus the existing thoughts poll for live updates. No new storage. Old
records are trimmed after N days (default 30), like other logs. Secrets are never shown: the card holds the same
cleaned text as the task log (`SECRETY` filter), never form values or codes.

**Tests:** the endpoints return the saved cards; filters work; a restart-interrupted task shows Continue; nothing
secret in a card; the page loads at 375 px wide.

**Build order inside 4-pre:** (a) task state + card for the brain, (b) save and resume, (c) the live strip, (d) the
`/tasks` page. Each its own commit; the UI can't come before the data it shows.

## 7. Phase 5 — Judges for workers' results

Quality comes from measurement, not from the user's habits or the worker's own claim:

- **Websites:** page opens, no console errors, works at phone width, Lighthouse (performance / accessibility / SEO)
  above a threshold, links work.
- **Code:** project tests pass, lint/build clean, the diff touches only the expected folder.
- **ML models:** score on a held-out test set, speed, memory — compared with the last run.
- **Research:** claims backed by 2+ independent sources, disagreements listed, citations present.

Each judge gives a pass/fail list Nova can quote back to the worker ("mobile layout overflows at 375 px").

## 8. Phase 6–8 — Knowing the user (all opt-in, local, viewable, deletable)

**Principle: learn from meaning, not raw input.** No keylogger, no screenshots of everything.

- **Phase 6 — read what is already on the laptop:** Chrome history (sites, search queries from `?q=`; skip incognito
  and a "never" list), PowerShell history (commands), Claude Code transcripts in `~/.claude/projects/` (projects,
  preferences, decisions). Summarised **locally** into facts; raw text never sent to a cloud brain; secret filter
  (`SECRETY` and friends) before anything is kept.
- **Phase 7 — activity log level 1 + form profile:** apps, window in front, sites, time of day (the old skills-v2
  "Phase 10"); routines found by plain code ("weekdays ~9 am: Gmail → Calendar → VS Code"). **Form profile:** when
  the user fills name/email/phone/address, Nova asks once to save it; stored encrypted (DPAPI, like `phone._protect`),
  used by `fill_form`, values never reach the brain. Never saved: passwords, OTPs, card/bank numbers, Aadhaar/PAN/
  passport.
- **Phase 8 — level 2 + suggestions (outline only, ask first):** clicks by control name in apps the user allows
  (the existing recorder), whole sentences only in allowed apps and only at send time (style learned, raw text
  deleted after ~30 days); suggestions at the right moment ("open your morning setup?"), later "prepare ahead" for
  safe things only.

Always: a pause switch with a visible indicator, a blocked-apps list, a "what Nova knows about me" page with delete,
recording only when the user is the one at the laptop (YuNet presence can help), raw events auto-deleted.

## 9. The user's answers (2026-11-20)

1. **Main work:** **coding / web development** and **research**. → First workers: Claude Code, OpenCode + DeepSeek,
   research worker. Office/study skills later.
2. **Workers installed and signed in:** **Claude Code CLI** and **OpenCode + DeepSeek** (plus the Claude desktop
   app). Not found on the bash PATH on 2026-11-20 — locate them (PowerShell `Get-Command claude, opencode`) before
   phase 3.
3. **Budget: cheap first, ask for big.** Small/routine jobs → OpenCode + DeepSeek without asking. A big job (new
   site/app, multi-file feature) → Nova asks before sending it to Claude Code. A job that keeps failing on the cheap
   worker escalates only with the user's OK.
4. **Questions while learning: a daily question list.** Nova keeps every non-urgent question (save this as a skill?
   is this fact right? which way do you prefer?) in a queue (`data/questions.json`) and asks them **once a day**, at
   a time the user is at the laptop. **Urgent** questions (a worker is blocked, an approval is needed to continue)
   are asked **as soon as the user is available**: at the laptop when presence (YuNet) sees them, otherwise on
   **Telegram**. Facts may still be stored meanwhile, but marked "unconfirmed" until the daily list.
5. **Logging: existing history first.** Phase 6 only (Chrome history, PowerShell history, Claude Code transcripts).
   Phases 7–8 (live activity log, recorder) wait for a later go-ahead.
6. **Away reports: Telegram — done + blocked only.** A message when a job finishes or is blocked and needs the user;
   no progress updates.

## 9b. Separate track — the "child brain" (not part of this plan)

The first discussion: a new brain structure built for transistors (not copying the human brain) that learns from
its own experience, AlphaZero-style. This plan only borrows the practical part (slow reasoning → fast saved skills,
cheaper with experience). A real experiment (e.g. evolving small network structures scored on accuracy **and** GPU
speed, or a self-play agent from zero) is its own research project — now written up in
[child-brain-plan.md](child-brain-plan.md).

## 10. Never

- Never a keylogger; never store passwords, OTPs, money or ID numbers anywhere.
- Never send raw logs, history or transcripts to a cloud brain — only cleaned briefs and lessons.
- Never let a worker push, publish, deploy, delete or pay without the user's Allow.
- Never let vision overrule a failed deterministic check.
- Never treat the user's habit as the quality standard; suggest improvements instead.
