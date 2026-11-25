Status: active — 2026-11-24. Handoff board for the parallel manager build. Any Claude session (or person) may pick
up a row marked **open**. Update this file in the same PR as your work.

# Manager build — handoff board

The plan is `docs/plans/nova-manager-plan.md`. On 2026-11-24 one *managing* session started nine worker sessions in
parallel, each in its own worktree and branch, each told to open a PR and **not** merge it. Seven of them hit the
Claude usage limit before finishing; their unfinished work was saved as a `WIP:` commit and pushed so anyone can
carry on.

## How to pick up a task

1. Pick a row below whose state is **open** and write your name / session in its "Taken by" cell (in your PR).
2. Work in a worktree on that branch (`git worktree add .claude/worktrees/<name> <branch>`), never in the main
   folder: it runs the live Nova (`AGENTS.md`).
3. `git fetch origin && git merge origin/master` first: every branch is ~20 commits behind master.
4. Read the branch's own diff (`git diff origin/master...HEAD`) and the plan section named in the row. The `WIP:`
   commit was **not** checked: tests may fail, and the "as built" note in the plan may be missing or half written.
5. Run `python scripts/who_edits.py <paths>` before editing. The other rows here are expected to show up; stop and
   ask only for anything else.
6. Finish: `scripts\check.cmd` green, an "as built" section in the plan doc, push, open a PR to master, and set the
   row to **PR #n**. Don't merge: the user (or the managing session, with the user's OK) merges, one PR at a time.
7. Live checks only on a separate copy of Nova (throwaway `NOVA_DATA` / `NOVA_SKILLS`, the row's port, `autoPort`
   false). Never quit or restart the real Nova.

## Board

| Task | Plan section | Branch | State | Port | Taken by |
|---|---|---|---|---|---|
| Barge-in: her own echo no longer counts as the user | improvement-roadmap R10; manager plan 4-pre rule 4 | `claude/bargein-echo-4ec2d0` | **merged** (PR #78) | — | done |
| 4c-4 read-back gaps: downloads, sent Gmail, form submits, wallpaper; Wi-Fi + wallpaper undo | 6, "4c-4 … as built" | `claude/manager-readback-gaps-6fe565` | **merged** (PR #79) | 5095 | done |
| Phase 6: read Chrome / PowerShell / Claude Code history into unconfirmed facts (opt-in per source) | 8 + 9 answer 5 | `claude/manager-phase6-8b25a2` | **PR #80**, CI green, **conflicts with master** — merge `origin/master` in and fix | 5096 | **open** |
| 4f: one record per task/job, app notes, skill offers, lessons from corrections | 6, "4f" | `claude/manager-4f-c45edf` | PR #88. Merged with master after #87/#80 (`learning.py`: both sides kept); `check.cmd` green. Live check on 5099 done (see "4f as built"). New: `nova/actions/appnotes.py`, `nova/actions/lessons.py`, `tests/test_remember_learn.py` | 5099 | **PR #88** |
| 4g: scheduled and repeating jobs (`data/schedules.json`, `/tasks` Pause / Delete, confirm card, missed run once) | 6, "4g" | `claude/manager-4g-7b7ac5` | WIP pushed (d92e4ec), early: `nova/schedules.py`, `nova/core/app/scheduling.py`, `static/schedules.js`, tests. No plan-doc note yet | 5098 | **open** |
| Phase 5: judges — websites (page opens, console errors, 375 px, links, Lighthouse), lint / build, research sources | 7 | `claude/manager-phase5-fc2a5f` | **PR #87** (merged with master after #80; `check.cmd` green). New: `nova/actions/judges.py`, `nova/actions/sitejudge.py`, `tests/test_judges.py`; research prompt changed - task exam owed | 5097 | **open** |
| Leftovers: cloud re-plan (goal + one-line outcomes only), research send-back once, usage-limit handoff card | 6, 4c-1 / 4c-3 / 4d-1 "not built" + "Not now" | `claude/manager-leftovers-bdf9fd` | **PR #89**, `check.cmd` green; merged with master after #80, #87, #88. Test: `tests/test_manager_leftovers.py` | 5094 | **open** |
| Phase 7: activity log level 1 (off by default; app, window, site, time — no keys, text or screenshots), routines by code, DPAPI form profile | 8 | `claude/manager-phase7-e77e40` | WIP pushed (7b481ad), now **PR #91**. New: `nova/actions/activity.py`, `nova/actions/tools/activity.py`, `nova/core/app/privacy.py`, `static/privacy.js`, tests. Exposes `routines(now=None)` for Phase 8 | 5093 | **PR #91** |
| Phase 8: design (written only) for sentence capture at send time + suggestions from routines, prepare-ahead for level 0 only | 8 | `claude/manager-phase8-3cca34` | **PR #90**; merged with master after #91 (Phase 7), `check.cmd` green. Adapter reads `nova.actions.activity.routines(now=<stamp>)`. New: `nova/actions/suggest.py`, `nova/core/app/suggesting.py`, `nova/platform/busy.py`, `static/suggestions.js`, `tests/test_suggestions.py`. Level-2 capture is design only (7 questions for the user in the plan doc) | 5092 | **PR #90** |

## Merge order (fewest shared files first)

(#78 and #79 merged) → Phase 6 → Phase 5 → 4f → leftovers → Phase 7 → Phase 8 (reads Phase 7's routines) → 4g.
After each merge, the next branch merges `origin/master` and re-runs `check.cmd` before its own merge.
Files most branches touch, so expect conflicts there: `nova/actions/risk.py` (LEVELS), `nova/actions/tools/__init__.py`,
`nova/core/app/background.py`, `static/index.html`, `static/tasks.*`, `nova/core/app/tasks_page.py`,
`docs/plans/nova-manager-plan.md`, `docs/OWNERS.md`. Keep both sides; never drop another branch's tool or level.

## After all are merged

- Run the task exam on the whole stack (`python -m nova.exam --model qwen3-vl:4b-instruct`, on copies of the data —
  the user starts it, it uses the real keyboard and screen).
- Clear this board's rows, the `docs/OWNERS.md` lines of these branches, and the stale "4e-2 in progress" line.
- Not yet allowed: a real paid Claude Code job (the user left that test for later); building Phase 8's text capture.

## Rules every branch must keep (from `AGENTS.md` and the plan)

- `nova/core/app/conversation.py` is at 797 / 800 lines (`test_packages`): don't add there.
- Compiled regexes in `nova/core/app/*.py` belong in `nova/intents.py` or protocol (`test_intents`).
- A new tool goes in `nova/actions/tools/`, the `tools/__init__.py` name table, and `risk.LEVELS` (`test_risk`).
- Win32 code lives in `nova/platform` (`test_platform`).
- Never commit `data/`, keys, or anything from the user's real history; tests use fixtures and fakes.
- Write strings with backslashes with an editor tool, not a bash heredoc. The repo is CRLF with autocrlf.
