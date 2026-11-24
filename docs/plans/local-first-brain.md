Status: Part A merged 2026-11-23 (PR #63). Part B merged 2026-11-24 (PR #68). Free ladder added 2026-11-24 (section C).

# ☁️ Local first, cloud when stuck — and a free-uses meter (plan)

**Goal:**
1. Nova tries every request on the **local** brains first. She asks the **cloud** brain (Gemini, free tier) only
   when the local brains clearly can't do the job.
2. The app shows **how many free cloud uses are left today**. When they run out, Nova switches to the local brain by
   herself, says so, and goes back to the cloud when the limit resets.

Two parts, two PRs: **Part A** (the meter) can start now. **Part B** (local first) waits for two open branches
(see *Order*).

## How it works today

| What | Today | Where |
|---|---|---|
| Which brain | One main brain: the cloud model if `provider` is a cloud and has a key, otherwise the local model | `config.brain()` (`nova/core/app/config.py:176`) |
| Cloud fails | Local model answers that turn; the cloud is skipped for 60 s | `llm.chat(..., fallback=)` |
| Limit used up (429) | Treated like any cloud error: one failed call, local answers, the cloud is tried again 60 s later and fails again | `cloud._friendly` (`nova/brain/cloud.py:137`) |
| Free uses left | Not tracked anywhere. Google shows it only on the AI Studio rate-limit page | — |
| Local ladder | everyday `qwen3-vl:4b` → planner (`deep_model`) / coder (`coder_model`) by request words or the `escalate` tool | `nova/brain/router.py`, `conversation.route_task` |
| Other cloud users | Judging, learning, notifications, skills also call the cloud brain when it's chosen | `judging.py`, `learning.py`, `integrations.py`, `skillrun.py` |

Every cloud call goes through **one function**, `cloud.chat()` (`nova/brain/cloud.py:442`), which makes the meter
easy to add in one place.

## What Google tells us (checked 2026-11-23)

- Free models (AI Studio pricing page): **Gemini 4 Hydrogen, Gemini 3.8 Flash, Gemini 3.6 Flash, Gemini 3.5
  Flash-Lite, Gemma 4**. Paid only: **Gemini 4 Carbon, Argon, Neon**.
- Limits are per **project** (not per key) and per **model**, and Google no longer publishes most of the numbers.
  The real numbers are on <https://aistudio.google.com/rate-limit>.
- Daily limits reset at **midnight Pacific time**.
- A 429 error message from Gemini usually names the limit it hit, its number and when to retry, e.g. `Quota exceeded
  for metric: ...free_tier_requests, limit: 250 ... Please retry in 33s`. **Step A0 checks this** on the
  OpenAI-style endpoint Nova uses, because it isn't confirmed there.
- On the free tier Google may use what's sent to improve its products. Nova already never sends images or form
  values to the cloud; that stays.

---

## Part A — the free-uses meter

### A0. Check what a 429 looks like (no code change)
- `cloud._http` keeps only the error's `message`, cut to 300 characters. Log the **whole** 429 body to
  `data/task_log.txt` once, so we see its real shape (limit name, number, retry time).
- If the message has no numbers, the meter still works from counting (A1) and the numbers you type in (A2).

### A1. Count every cloud call
- In `cloud.chat()`, after each call (success **or** failure), add one to a counter for that provider + model.
- Kept in `data/cloud_usage.json` (path from `store.current().file(...)`):
  - `day`: the Pacific date the counts belong to. When the Pacific date changes, the day counts start from 0.
  - per model: `requests_today`, `tokens_today` (from the reply's `usage` field), and the times of the calls in the
    last 60 s (for the per-minute limit).
  - per model: `out_until` (see A3) and `last_429` (the message).
- Written atomically, like `settings.json`. A broken or missing file just starts from 0.

### A2. Know the limits
- New file `config/cloud_free.json` (you can edit it):
  - which models are **free** / **paid** (the list above), so the picker can label them.
  - per model: `rpd` (requests per day) and `rpm` (requests per minute), `null` when unknown.
- Nova fills in a `null` number by herself when a 429 message names it (A0), and saves it.
- In the Brain tab you can also type the numbers you see on the AI Studio page.
- If a limit is still unknown, the meter shows the count only: "37 used today (limit unknown — check AI Studio)".

### A3. When the limit runs out
- On a 429:
  - per-day limit → mark the model `out_until` = next midnight Pacific.
  - per-minute limit → `out_until` = now + the retry seconds Google gives (or 60 s).
- While a model is `out_until` a future time, **Nova doesn't call it at all**. She goes straight to the local brain
  (no 25 s wait, no failed call). She says it once: *"My free cloud uses are finished for today, so I'm using my
  local brain. They come back at 1:30 PM."* (time in your own clock).
- When the time passes, the cloud is used again with no action from you.
- **Running low:** below 10 % left (setting, default on), only the main conversation and escalations (Part B) use
  the cloud; side jobs (judging steps, notification wording, learning) use the local brain, so the last uses go to
  what matters. *Not in Part A:* it needs `judging.py`, which PR #61 is changing; it moves to B5.
- *As built:* while a model is out, `cloud.chat` refuses at once with that message, so `llm.chat` falls back to the
  local brain the same way as any cloud failure (the 💭 log says the cloud didn't answer; the CloudError text with the
  time is in `task_log.txt`). A spoken "uses are finished" line needs `conversation.py` and moves to Part B.

### A4. The UI
- **Brain tab**, under the cloud model picker, one line per model you've used today:
  `Gemini 3.8 Flash — 213 of 250 left today · resets in 6 h 12 m` with a thin bar (green → amber under 20 % →
  red when out). A link "See exact limits in AI Studio".
- **Model dropdown:** each model gets **Free** or **💲 Paid** after its name. Picking a paid one asks first: *"This
  model costs money. Use it anyway?"*
- **Main window:** a small chip next to the brain name, e.g. `☁️ 213 left`. When out: `☁️ out → local until 1:30 PM`.
  Click opens the Brain tab.
- New endpoint `GET /api/cloud/usage` returns the meter (counts, limits, `out_until`, reset time); the page polls it
  every 30 s while open and after each reply.

### A5. Tests (hermetic, no real cloud)
- counting: success and failure both count; tokens added from `usage`.
- day rollover at midnight Pacific (fake clock), including the day the clocks change.
- 429 parsing: per-day vs per-minute message → right `out_until`; unknown text → 60 s.
- `out_until` in the future → `config.brain()` returns the local model, no HTTP call made.
- running low → side jobs get the local model, the main chat still gets the cloud.
- `/api/cloud/usage` shape; corrupt `cloud_usage.json` → starts from 0.

---

## Part B — local first, cloud when stuck

### As built (Part B)
- `brain_mode` = `cloud_first` (default, today's behaviour) | `local_first`. It only matters when a cloud provider with
  a key is chosen; provider "Local model" stays local only. The Brain tab shows the choice under the cloud model.
- In `local_first`, `config.brain()` is the local model, so every request and every side job starts local.
  `brains.climb()` puts one task on the cloud model; the agent loop restarts its turn and repeat counters then.
- Stuck signs built: asked in the request, `escalate(kind="cloud")`, planner gave no plan, read-back failures
  (`cloud_after_failed_checks`, 2), the same step 3 times, `cloud_after_turns` (18) turns, the Judge's "not done"
  after one push, "I can't" on a task that used tools. **Not built:** unreadable tool calls (no clean signal yet).
- Mail tasks never climb unless "let the cloud read my mail" is on. Free uses used up → stays local, says so once.
- The "free uses are finished" notice is a 💭 log line (it carries the time they come back), not spoken.

### B1. A new choice in the Brain tab
"How should I use the cloud brain?"
- **Local only** — never the cloud.
- **Local first, cloud when stuck** *(new; becomes your setting)*.
- **Cloud first, local as backup** — today's behaviour.

New setting `brain_mode` = `local` | `local_first` | `cloud_first`. Old settings move over unchanged: `provider` =
`ollama` → `local`, a cloud provider → `cloud_first`.

### B2. The ladder
Each new request starts at the bottom and climbs only when needed:

1. **Everyday local brain** (`qwen3-vl:4b`) — every request.
2. **Local helpers** — the planner (`qwen3.5:4b` once `feat/qwen35-planner` is merged) for complex jobs, the coder
   for code. Same words and `escalate` tool as today.
3. **Cloud brain** (Gemini 3.8 Flash, free) — only when a "stuck" sign below fires.

### B3. What counts as "stuck" (numbers live in `config/model_routing.json`, editable)

| Sign | Default |
|---|---|
| The local planner gives no usable plan (empty, cut off, or no numbered steps) | 1 time |
| A step fails its screen check in a row | 2 times |
| The same tool call with the same arguments, again | 3 times |
| The task uses up most of its turns without finishing | 18 of 24 turns |
| Tool calls that can't be read (bad JSON, unknown tool) | 2 times |
| The local brain says it can't ("I can't", "I'm not able", "I don't know how") on a task | 1 time |
| You ask: "ask Gemini", "use the cloud", "use your cloud brain" | always |
| The local brain calls `escalate` with the new `kind="cloud"` | always |

Plain chat ("what time is it", small talk) never climbs to the cloud on its own.

### B4. Handing over
- The cloud brain gets the same chat history (already in a format every brain reads) plus one short note: what the
  local brain tried, which step failed, and why it was handed over.
- The 💭 log shows it: *"☁️ My local brain failed the same check twice → asking Gemini."*
- The task stays on the cloud until it ends. The next request starts local again.
- If the cloud is out (A3), down, or has no key, she stays local and says so once for that task.

### B5. Side jobs
In `local_first`, judging, learning, notification wording and skill checks use the **local** brain, except while a
task has climbed to the cloud (then its own checks may use the cloud too). This keeps the free uses for the hard
jobs.

### B6. Privacy
Unchanged: images and form values never go to the cloud; the email / iPhone "cloud too" switches still apply.

### B7. Tests (hermetic)
- each stuck sign climbs to the cloud once, and only on a task (not plain chat).
- the task stays on the cloud; the next request starts local.
- `local` never calls the cloud; `cloud_first` behaves as today.
- cloud out / no key / error → stays local, one message.
- settings move-over from old `provider` values.

### B8. Task exam
This changes which brain answers, so (AGENTS.md §3) the user runs `python -m nova.exam` before and after Part B,
and the PR reports both.

---

## Order

| Step | Files | Can start |
|---|---|---|
| A0–A5 meter | `nova/brain/cloud.py`, `nova/core/app/config.py`, `nova/core/app/api.py`, `static/nova.js`, `static/index.html`, `config/cloud_free.json`, tests | now (run `who_edits.py` first) |
| B1–B8 local first | `nova/core/app/conversation.py`, `judging.py`, `config.py`, `nova/brain/router.py`, `llm.py`, `routines.py` (`escalate`), UI, tests | after **PR #61** (`feat/manager-4c-readback`, changes `conversation.py`, `judging.py`) and **`feat/qwen35-planner`** (changes `llm.py`, `router.py`) are merged |

Add a line to `docs/OWNERS.md` when each part starts (not now: both open branches are editing that file too).

## Open questions for you
1. Are the "stuck" numbers in B3 right, or should she climb sooner / later?
2. In "local first", should side jobs (B5) never use the cloud, or use it only while there are many uses left?
3. The "running low" line: 10 % left — OK?

---

## C — the free ladder (added 2026-11-24, user's wish)

Google gives each free model its own limits, so when one runs out the others still have uses left.
- `config/cloud_free.json` → `google.ladder`: the free models, strongest first (`gemini-4-hydrogen`, `gemini-3.8-flash`,
  `gemini-3.6-flash`, `gemini-3.5-flash-lite`). Edit the order freely.
- When the chosen model is on the ladder and the Brain tab's **🪜 Free ladder** switch is on (default), every cloud call
  goes to the strongest rung with uses left (`usage.route`). A 429 marks that rung out (as before) and `cloud.chat`
  tries the next rung in the same call. Only when every rung is out does she use the local brain
  (`usage.unavailable`: "All my free Google models are used up ... until HH:MM"). Other errors (503, no internet) don't
  move down the ladder.
- Each rung keeps its own count and learned limits in the meter; the Brain tab numbers them and marks the one in use;
  the header chip shows that one. `task_log.txt` gets `FREE LADDER ...` lines when another rung answers.
- Thought signatures: each stored tool call remembers the model that wrote it (`by`); a different rung gets the "skip"
  signature, so switching models mid-task doesn't break Gemini's signature check.
- Switch off (or pick a model not on the ladder, e.g. a paid one) to use only the chosen model.
