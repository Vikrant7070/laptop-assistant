Status: being built. L3, L5 part 1 and L6 are merged (2026-11-24). L1+L4, L5 part 2 and L2 are open; see
"Status and hand-off" below. Any session, local or cloud, can pick up an open task from there.

# 💻→☁️ Local first, v2: climb sooner, learn from every rescue, spend free uses wisely (plan)

Follows `docs/plans/local-first-brain.md` (Part A meter PR #63, Part B local first PR #68, C free ladder PR #72 — all
merged). The user asked on 2026-11-24 to make the local-first approach better, and to write the plan down first.

**Goal:** the local brain does more on its own every week; when it can't, Gemini helps **early**, with **as few free
uses as possible**, and what Gemini showed is **kept** so the same task stays local next time.

## Status and hand-off (updated 2026-11-24)

This section is how work on this plan is shared between sessions on the laptop and in the cloud. Keep it up to date in
the same PR as the work: claim a task when you start it, mark it done when it merges.

### Done

| Step | PR | What landed |
|---|---|---|
| Plan review | #81 | Order L3 → L1+L4 → L2 (+L6) → L5; safety details; exam budget |
| L3 check before "done" | #84 (carried #79, manager 4c-4) | Failed read-back after one more go → climb; Notepad/editor reader (save + read, or private copy); "file not found" = failed check; exam `page_text` keeps short documents' text. Exam on the laptop: `type-in-notepad` and `no-such-file` went from false "done" to success (`exam/results/2026-11-24-qwen3-vl-4b-instruct-l3-run2.md`) |
| L5 part 1 | #82 | `usage.route(provider, name, job="hard"\|"light")`, `light_from` rung in `config/cloud_free.json`; nothing calls it with `light` yet |
| L6 Brain tab line | #83 | `nova/brainstats.py`, `/api/brain/stats`, `static/brainstats.js`; reads optional L2 fields `rescued_by`, `hint_used`, `skill_offered` (0 until L2 writes them) |

### Open tasks

Check the "Can start when" column before you begin; if it isn't true on `origin/master`, don't start.

| Task | Can start when | Claimed by |
|---|---|---|
| **T1 — L1 + L4** (climb sooner + coach first) | The manager "leftovers" work (4d: a stuck task's re-plan goes to the cloud first, `watching.replan` / `cloud_planner` in `nova/core/app/watching.py`) is on `master`. Check: `git grep -n cloud_planner origin/master -- nova/core/app/watching.py` finds it. On 2026-11-24 it was **only uncommitted on the laptop**, not on GitHub | — |
| **T2 — L5 part 2** (wire light/hard into the calls) | T1 merged | — |
| **T3 — L2** (learn from rescues) | T1 merged **and** the user has said whether L2 or manager 4f builds the rescue part (they overlap) | — |
| **T4 — full 25-task exam** | Only the user starts it, on the laptop (it drives the real desktop) | the user |

**T1 brief.** Build L1 and L4 as one PR (sections L1 and L4 above), **on top of** the 4d re-plan code, not beside it:
L4's coach call *is* that re-plan, plus a hand-over note of step outcomes with private values replaced by `<hidden>`.
One stuck detector only. Plain chat never climbs; once per task. The stuck numbers and the "waiting tools don't count"
rule are in the L1 table. Use the suggested answers to the open questions (coach first for every climb she starts
herself) and say so in the PR. Exam owed: `rename-file`, `windows-version`, and one task that should stay local.

**T2 brief.** The call sites, listed in PR #82's *Not done*:
1. `nova/brain/cloud.py` `chat()`: take `job="hard"`, pass it to `usage.route` and `usage.unavailable`.
2. `nova/brain/llm.py` `chat()`: take `job`, pass it to `cloud.chat`.
3. `nova/core/app/conversation.py`, the main turn's `llm.chat`:
   - `light` for one-shot cloud questions ("ask Gemini what …", `answer_only`, facts);
   - `hard` when climbed or for multi-step tasks.
4. `nova/core/app/brains.py` `climb()` and the L4 coach call: `hard`, written out. `at_start` (`ASK_CLOUD`) picks light for
   a question, hard for a task.
5. `nova/core/app/integrations.py`, `write_text` and `phone_note`: `light`.
6. `nova/core/app/learning.py`, the after-chat lesson review: `light`.
7. `nova/brain/router.py` planner/probe: leave them alone if they never reach the cloud.

Done when a "what is …" cloud question starts at Flash Lite and a stuck multi-step task at the top.

**T3 brief.** Section L2 above, with the review's rules: a rescue needs passed read-backs (the Judge alone isn't
enough); hint values are slots filled from the new request; risky steps are still asked every time. Write
`rescued_by`, `hint_used` and `skill_offered` on the task record so L6's line counts them.

### Rules for any session (local or cloud)

- Follow `AGENTS.md`. Branch from fresh `origin/master`, one task = one branch = one PR, and the user merges.
- Claim before you start: in your first commit, put your branch name in **Claimed by** above and add your line to
  `docs/OWNERS.md`. Push that commit early so others see the claim. If a task is already claimed, pick another.
- `python scripts/who_edits.py` only sees worktrees on the laptop. A cloud session can't see them, so check open PRs
  (`gh pr list`) and the **Can start when** column instead.
- The gate is `ruff` + `pytest` (`scripts\check.cmd` on Windows; CI runs `python -m ruff check .` and
  `python -m pytest -q` on `windows-latest`). Tests are hermetic: no real keyboard, clipboard, voice, browser or cloud.
- A cloud session can't run the task exam, the live Nova or anything on the real desktop. Say in the PR which exam
  tasks are owed; the user runs them on the laptop.
- When your PR merges: move the task to **Done** and clear your **Claimed by** and OWNERS line.

## Where we were when the plan was written (2026-11-24, before L3/L5/L6)

| What | Today |
|---|---|
| Brain mode | `local_first`: qwen3-vl:4b-instruct answers first; planner qwen3.5:4b for complex jobs |
| Climb to Gemini | once per task, on a stuck sign (`nova/core/app/brains.py`) |
| Stuck signs | user asks; `escalate(kind="cloud")`; no usable plan; 2 failed read-backs; same step 3×; **18 turns**; "I can't" after tools; Judge "not done" after one push |
| Cloud models | free ladder of 8 (Hydrogen, 3.8/3.6/3 Flash, 2.5 Flash: **20 a day, 5 a minute**; 3.5/3.1 Flash Lite: 500 a day) |
| Live since switch | 23 local brain calls, 0 climbs |

**Exam, local first (2026-11-23, 25 tasks, `exam/results/2026-11-23-qwen3-vl-4b-instruct-local-first.md`):** 80 %
success, 8 % false "done", 4 % honest failure. What it shows:

| Problem | Evidence | Fix below |
|---|---|---|
| Climbs come late; minutes lost going in circles | `rename-file` 21 steps / 165 s; `windows-version` 18 steps / 173 s, still not answered | L1 |
| Wrong "done" with no stuck sign | `type-in-notepad` (typed text can't be read back → *skipped*), `no-such-file` (said done on a missing file) | L3 |
| Nothing learned from a rescue | each climb starts from zero; the same task would climb again tomorrow | L2 |
| Every climb spends the scarce models | a whole climbed task runs on Hydrogen: 5–15 calls of its 20 a day | L4, L5 |
| No way to see if it gets better | only `task_log.txt` lines | L6 |

## How this fits the manager plan (`docs/plans/nova-manager-plan.md`)

These overlap on purpose; build them **once**, shared:
- **4c-1 read-back** (merged, PR #61) is what L3 builds on: `nova/actions/readback.py`, `judging.readback_verdict`,
  `task_state.failing/left`.
- **4d "watch progress"** (not built) = L1. Its *progress check* after every step (done? loop? anything new?) is the
  same thing L1 needs; its "stuck → one new plan (the deep or cloud brain)" is L4. **Build L1 as 4d's progress check**
  so there is one stuck detector, not two.
- **4f "remember and learn"** (not built) = L2. Rescue lessons go into the same task record and the Learner's daily
  question list; app notes are shared.
- **4e risk levels** merged (PR #75, `a1cd2e8`). `judging.py` and `docs/OWNERS.md` are free again, so **L3 no longer
  waits** (run `who_edits.py` first all the same).

---

## L1 — Climb sooner (the progress check)

- After every batch of steps, code fills a small **progress record** on the task card (`task_state`):
  - `new`: did anything change since the last step? (a read-back passed, a new file or window, a tool result that
    differs from every earlier one **after normalising it**: drop timestamps, screenshot bytes, cursor/coordinates
    and other noise that differs every call — otherwise almost every result looks new and `stalled` never fires)
  - `loop`: the same tool + arguments as an earlier step (not only the last 3 in a row — also A, B, A, B)
  - `stalled`: steps in a row with no `new`
  - `secs`: time since the task started, **not counting time spent inside a tool that is meant to wait** (installs,
    downloads, `wait_for_code`, background research in Chrome, a `wait` step). A slow-but-working task must not climb.
- New stuck signs (numbers in `config/model_routing.json`, see `router.DEFAULTS`):

| Sign | Default now | New default |
|---|---|---|
| steps in a row with nothing new | — | **3** |
| a loop (same step again, any order) | 3 in a row | **2nd repeat** |
| turns without finishing | 18 | **8**, or plan steps + 4 when the planner made a plan (whichever is larger) |
| time without finishing | — | **60 s** with no progress (waiting tools excluded) |

- These numbers are a first guess from two exam tasks (and 0 live climbs so far). Tune them from L6's daily line,
  not from one run.
- Plain chat still never climbs. A climb is still once per task.
- **L1 ships together with L4**: climbing sooner means climbing more often, so the first climb must be the cheap
  coach call (1 call), not a full take-over (5–15 calls). L1 alone would burn the 20-a-day models faster.
- The 💭 log says why: *"☁️ 3 steps with nothing new → asking gemini-4-hydrogen."*
- **Done when:** the exam's `rename-file` and `windows-version` climb within ~60 s instead of after 165–173 s; no
  plain-chat request climbs.

## L2 — Learn from every rescue

When a task that climbed **ends with its read-backs passed**, it is a *rescue*. The Judge saying "done" is **not
enough on its own**: the Judge runs on the local brain, which is exactly where the false "done" answers come from, and
a rescue judged only by it could teach a wrong lesson. No passed read-back → no rescue record, no hint, no skill offer.
1. **Rescue record** — `learner.episode_from_task` already writes one record per task; add `rescued_by` (the cloud
   model) and the steps the cloud brain took that passed their read-back. Values the user typed into forms are never
   kept (as today).
2. **Hint next time** — a new request that matches a rescue (same words as `learner._norm_words`, or the retrieval
   match the skills use) gets the rescue's steps on its task card as a *hint*: "Last time this worked: 1. … 2. …".
   The local brain tries with the hint first. (A hint is information, not an order; it never skips a risk-level
   question.)
   - **Values in a hint are slots, not copies.** "Rename report.txt to final.txt" and "rename notes.txt to x.txt"
     share almost all their words. The rescue keeps file names, paths, app names and typed text as slots
     (`<from>`, `<to>`) filled from the new request; when a slot can't be filled from it, the hint isn't used. A hint
     never replays another task's path.
   - Steps that delete, send, buy or change settings are shown in the hint but never run because of it — they go
     through the normal risk-level question every time.
3. **Skill offer** — one rescue whose steps all passed read-back is offered as a draft skill on the daily question
   list (`skills/drafts.draft_from_steps`), the same way the Learner offers a path that worked twice. Used only after
   the user ticks it (manager plan section 9, answer 4).
4. **Climb straight away** — a request that matches a rescue whose hint was tried and failed again climbs at once
   instead of spending 8 turns first.
- **Done when:** run a task that needs a climb, then the same task again: the second run gets the hint, and either
  stays local (success) or climbs at the first sign; a draft skill shows on the daily list.

## L3 — Check before "done" (first: 4e has merged, and false "done" is the worst failure)

- At answer time, a read-back that **failed** already makes her try once more (4c-1). New: in local first, a failed
  read-back after that one more go **climbs** instead of saying "I don't think I finished that".
- **More readers** so fewer steps are *skipped*:
  - `type_text` into Notepad / a text editor: when the window has a file, read the saved file after Ctrl+S; when it
    has none, select-all + copy into a private clipboard read and restore the clipboard (the paste-typing code already
    saves and restores it, `nova-typing-paste`).
  - "file not found" in a tool result while she says done → a failed check, not a pass (`no-such-file`).
- A task with only *skipped* checks that she calls done is shown as "done, not checked" (as today) — no climb, to
  save free uses.
- **Done when:** the exam's `type-in-notepad` and `no-such-file` are no longer false "done".

## L4 — Gemini as coach first (fewer cloud calls per rescue)

Today a climb hands the **whole task** to the cloud brain, which spends a call on every step (5–15 calls).
New, two levels:
1. **Coach (1 call):** on the first stuck sign, send Gemini the goal, the steps tried with their outcomes, and the
   tool list, and ask for a short plan ("numbered steps, tool names, why it failed"). The local brain carries on with
   that plan on its card. Uses the router's existing "plan" format (`router.make_plan` output shape).
2. **Take over (as today):** if the local brain is still stuck after the coach's plan (the L1 signs again), the task
   climbs fully, as now.
- The hand-over note gets shorter: step outcomes only, not the whole chat history (fewer tokens; also helps the
  per-minute token limits).
- **Nothing private in the coach note.** Step outcomes can hold screen text and form fields. The same rule as
  `fill_form` applies: values the user typed into forms, codes from `wait_for_code` and anything marked secret are
  replaced with `<hidden>` before the note leaves the laptop. A test checks it.
- `escalate(kind="cloud")` and "ask Gemini" still go straight to take-over (the user or the brain asked for it).
- **Done when:** in the exam, a rescued task costs 1–3 cloud calls on average instead of 5–15, with the same
  success.

## L5 — The right free model for the job

- The ladder stays (strongest first), but each cloud call says **what kind of job** it is:
  - `coach` (L4) and take-over of a multi-step task → start at the top (Hydrogen).
  - one-shot answers (the user asked "ask Gemini what …", a fact, a summary) and side jobs that may use the cloud →
    start at **3.5 Flash Lite** (500 a day), so the 20-a-day models are kept for hard work.
- `usage.route(provider, name, job="hard"|"light")`; the ladder file gets a `light_from` rung.
- **Done when:** a "what is …" cloud question uses Flash Lite; a stuck multi-step task uses Hydrogen.

## L6 — See whether it gets better (Brain tab)

- One line per day and a 7-day total, from the task records (no new file):
  *"Today: 31 tasks — 27 local, 4 climbed (3 rescued, 1 not). Cloud calls: 9. Learned: 1 hint, 1 skill offer."*
- Each rescue listed with its reason and a link to its task on `/tasks`.
- **Done when:** the line matches the task log for a test day.

---

## Tests (hermetic, no real cloud)

- L1: each new sign climbs (fake clock for the 60 s); A-B-A-B counts as a loop; plain chat never climbs; numbers from
  `model_routing.json` are used.
- L2: a passed climbed task writes a rescue; a matching request gets the hint; a failed hint → instant climb; a draft
  skill is offered, not used, until ticked; form values never in a rescue record.
- L3: failed read-back after one more go → climb; skipped-only → no climb; the new readers pass/fail/skip correctly.
- L4: the first stuck sign makes exactly one coach call and the task stays local; still stuck → take-over; the
  hand-over note has outcomes only.
- L5: light jobs start at Flash Lite, hard ones at the top; a used-up rung still moves down.
- L6: counts from a fixture of task records.

## Task exam

These change when and how the brain climbs (AGENTS.md §3), so each PR reports a before/after `python -m nova.exam`
run (on a copy of the data with `NOVA_DATA`, Nova and Notepad closed; Nova runs it only when the user says go). The
exam's cloud calls come from the user's free uses: about 25 tasks × a few calls each.

**Cloud budget:** a full before/after run per PR could use more than the 20-a-day top models by itself. So the full
25-task run is done once at the start (the baseline) and once at the end; each PR in between runs only the tasks that
climb or were false "done" (about 5: `rename-file`, `windows-version`, `type-in-notepad`, `no-such-file`, plus one
that should stay local), and runs on different days if needed.

## Order

| PR | Steps | Files | Can start |
|---|---|---|---|
| 1 | **L3** | `judging.py`, `readback.py` | now, after the user's go-ahead (4e merged; run `who_edits.py` first) |
| 2 | **L1 + L4** | `brains.py`, `task_state.py`, `router.py`, `conversation.py` (hooks only), `cloud.py` | after PR 1 (both touch the answer-time path) |
| 3 | **L2** (+ L6, small) | `learner.py`, `skills/drafts.py`, `brains.py` (hint hook), UI | after PR 2 (needs L1's progress record) |
| 4 | **L5** | `usage.py`, `cloud.py`, `config/cloud_free.json` | after PR 2; can run beside PR 3 (no shared files) |

Why this order: L3 is small, stands alone and fixes the worst result (a false "done" the user believes). L1 and L4
go together so climbing sooner is cheap from day one. L2 needs the progress record and the coach's plan to learn
from. L5 and L6 are small and last.

Update the manager plan's 4d and 4f lines to point here when PR 2 starts, so nobody builds the same thing twice.
Note: 4f already has worktrees (`claude/manager-4f-c45edf`) — check with `who_edits.py` and the user before L2, as
L2 = 4f's rescue part. Add a `docs/OWNERS.md` line when each PR starts.

## Open questions for the user

Each has a suggested answer from the review; the user decides.

1. L1 numbers: 3 steps with nothing new / 8 turns / 60 s — sooner or later?
   *Suggested:* keep 3 / 8 (or plan steps + 4) / 60 s with waiting tools excluded, then tune from L6.
2. L2: may a hint from a rescue be used **at once** (it's only a hint), or only after you tick it like a skill?
   *Suggested:* at once — but with slots for values, and risky steps still asked every time.
3. L4: coach first for every climb, or straight take-over for some kinds of task?
   *Suggested:* coach first for every climb she starts herself; straight take-over only when the user asks or the
   brain calls `escalate(kind="cloud")`.
4. L5: should side jobs (Judge, notification wording) ever use Flash Lite, or always stay local?
   *Suggested:* the Judge stays local (sending it out would spend a call on every task and undo local first);
   notification wording may use Flash Lite but isn't worth much.

## Not now (written down so it isn't lost)

- Fine-tuning the local model on rescues (the child-brain project's job, `Desktop\child-brain`, not Nova's).
- Asking two cloud models and comparing answers (costs double).
- Gemma 4 as a rung: 14,400 a day, but 16K tokens a minute is about one Nova request a minute. Revisit if the L4
  hand-over gets small enough.

## Review changes (2026-11-24)

- 4e has merged → L3 no longer waits and goes **first**.
- Order is now L3 → L1+L4 → L2 (+L6) → L5, so climbing sooner never costs more free uses.
- L1: "new" compares normalised results; waiting tools don't count toward 60 s; turn limit grows with the plan.
- L2: a rescue needs passed read-backs (Judge alone isn't enough); hint values are slots; risky steps still asked.
- L4: the coach note hides form values, codes and secrets.
- Exam: full run at start and end only; per-PR runs use the ~5 tasks that climb or were false "done".
- Open questions carry suggested answers.
