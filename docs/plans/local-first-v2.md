Status: plan, written 2026-11-24. Nothing built yet. Waits for the user's go-ahead per step.

# 💻→☁️ Local first, v2: climb sooner, learn from every rescue, spend free uses wisely (plan)

Follows `docs/plans/local-first-brain.md` (Part A meter PR #63, Part B local first PR #68, C free ladder PR #72 — all
merged). The user asked on 2026-11-24 to make the local-first approach better, and to write the plan down first.

**Goal:** the local brain does more on its own every week; when it can't, Gemini helps **early**, with **as few free
uses as possible**, and what Gemini showed is **kept** so the same task stays local next time.

## Where we are (2026-11-24)

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
- **4e risk levels** is being built now in another worktree (`claude/manager-4e-2`, changes `judging.py` and
  `docs/OWNERS.md`). **Don't touch `judging.py` until that merges.** L3 needs it → L3 waits for 4e.

---

## L1 — Climb sooner (the progress check)

- After every batch of steps, code fills a small **progress record** on the task card (`task_state`):
  - `new`: did anything change since the last step? (a read-back passed, a new file or window, a tool result that
    differs from every earlier one)
  - `loop`: the same tool + arguments as an earlier step (not only the last 3 in a row — also A, B, A, B)
  - `stalled`: steps in a row with no `new`
  - `secs`: time since the task started
- New stuck signs (numbers in `config/model_routing.json`, see `router.DEFAULTS`):

| Sign | Default now | New default |
|---|---|---|
| steps in a row with nothing new | — | **3** |
| a loop (same step again, any order) | 3 in a row | **2nd repeat** |
| turns without finishing | 18 | **8** |
| time without finishing | — | **60 s** with no progress |

- Plain chat still never climbs. A climb is still once per task.
- The 💭 log says why: *"☁️ 3 steps with nothing new → asking gemini-4-hydrogen."*
- **Done when:** the exam's `rename-file` and `windows-version` climb within ~60 s instead of after 165–173 s; no
  plain-chat request climbs.

## L2 — Learn from every rescue

When a task that climbed **ends with its read-backs passed** (or the Judge says done), it is a *rescue*:
1. **Rescue record** — `learner.episode_from_task` already writes one record per task; add `rescued_by` (the cloud
   model) and the steps the cloud brain took that passed their read-back. Values the user typed into forms are never
   kept (as today).
2. **Hint next time** — a new request that matches a rescue (same words as `learner._norm_words`, or the retrieval
   match the skills use) gets the rescue's steps on its task card as a *hint*: "Last time this worked: 1. … 2. …".
   The local brain tries with the hint first. (A hint is information, not an order; it never skips a risk-level
   question.)
3. **Skill offer** — one rescue whose steps all passed read-back is offered as a draft skill on the daily question
   list (`skills/drafts.draft_from_steps`), the same way the Learner offers a path that worked twice. Used only after
   the user ticks it (manager plan section 9, answer 4).
4. **Climb straight away** — a request that matches a rescue whose hint was tried and failed again climbs at once
   instead of spending 8 turns first.
- **Done when:** run a task that needs a climb, then the same task again: the second run gets the hint, and either
  stays local (success) or climbs at the first sign; a draft skill shows on the daily list.

## L3 — Check before "done" (waits for 4e to merge)

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

## Order

| PR | Steps | Files | Can start |
|---|---|---|---|
| 1 | **L1 + L2** (+ L6, small) | `brains.py`, `task_state.py`, `router.py`, `conversation.py` (hooks only), `learner.py`, `skills/drafts.py`, UI | after the user's go-ahead (run `who_edits.py` first) |
| 2 | **L4 + L5** | `brains.py`, `cloud.py`, `usage.py`, `router.py`, `config/cloud_free.json` | after PR 1 |
| 3 | **L3** | `judging.py`, `readback.py` | after **4e** (`claude/manager-4e-2`) merges |

Update the manager plan's 4d and 4f lines to point here when PR 1 starts, so nobody builds the same thing twice.
Add a `docs/OWNERS.md` line when each PR starts (not now: 4e's branch is editing that file).

## Open questions for the user

1. L1 numbers: 3 steps with nothing new / 8 turns / 60 s — sooner or later?
2. L2: may a hint from a rescue be used **at once** (it's only a hint), or only after you tick it like a skill?
3. L4: coach first for every climb, or straight take-over for some kinds of task?
4. L5: should side jobs (Judge, notification wording) ever use Flash Lite, or always stay local?

## Not now (written down so it isn't lost)

- Fine-tuning the local model on rescues (the child-brain project's job, `Desktop\child-brain`, not Nova's).
- Asking two cloud models and comparing answers (costs double).
- Gemma 4 as a rung: 14,400 a day, but 16K tokens a minute is about one Nova request a minute. Revisit if the L4
  hand-over gets small enough.
