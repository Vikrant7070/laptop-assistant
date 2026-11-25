Status: active — 2026-11-25. Handoff board for the manager plan. The parallel build of 2026-11-24 is merged; the
rows under "Open" are what is left. Any Claude session (or person) may pick up an open row. Update this file in the
same PR as your work.

# Manager build — handoff board

The plan is `docs/plans/nova-manager-plan.md`. On 2026-11-24 one *managing* session ran nine worker sessions in
parallel, each in its own worktree; their PRs were merged one at a time on 2026-11-24/25 (master 09e3166).

## How to pick up a task

1. Pick an **open** row below and write your name / session in its "Taken by" cell (in your PR).
2. Work in a worktree (`.claude/worktrees/<name>`), never in the main folder: it runs the live Nova (`AGENTS.md`).
   Base your branch on `origin/master`.
3. Run `python scripts/who_edits.py <paths>` before editing; add your line to `docs/OWNERS.md`.
4. Finish: `scripts\check.cmd` green, an "as built" note in the plan doc, push, open a PR to master, set the row to
   **PR #n**. Don't merge: the user (or a managing session, with the user's OK) merges.
5. Live checks only on a separate copy of Nova (throwaway `NOVA_DATA` / `NOVA_SKILLS`, its own port, `autoPort`
   false). Never quit or restart the real Nova unless the user asks.

## Open

| Task | Why / where | Who starts it | Taken by |
|---|---|---|---|
| Task exam on the whole stack (`python -m nova.exam --model qwen3-vl:4b-instruct`, on copies of the data), compared with the last run (60 % success / 28 % false done / 4 % honest, median 7.6 s) | Phases 4c-4 to 8 are merged without an exam; Phase 5 changed `nova/prompts/research.md` | **The user** (it drives the real keyboard and screen; refuses while Notepad is open) | — |
| Update the live Nova to master and restart it | The main folder still runs an older master; nothing built on 2026-11-24/25 is live yet | **The user's OK** (memory note "Restarting live Nova": `/api/quit`, then `Start Nova.bat` by full path) | — |
| Phase 8 level-2 recording: answer the 7 design questions | "Phase 8 design - level 2 recording" in the plan doc. **Nothing of it is built** and nothing may be until the user answers | **The user** answers; then a session builds | — |
| Tune barge-in `ECHO_MIN` (0.65) from live numbers | `nova/audio/voice/barge_in.py`; the task log has "barge-in … echo 0.xx" lines once the live Nova runs the new code | open | — |
| A real Claude Code job | Never run yet; paid. The user left it for later (2026-11-21) | **The user's OK** | — |
| "Not now" list in plan section 6: a second worker reviewing the first one's work without the conversation | After the exam shows the judges work | open, after the user's go | — |

Not part of this board: the local-first v2 work (`docs/plans/local-first-v2.md`, its own sessions and `docs/OWNERS.md`
lines), R10 voice state machine, and OpenCode's `refactor/part-b` (never touch it).

## Merged on 2026-11-24/25

| Task | PR |
|---|---|
| Barge-in: her own echo no longer counts as the user | #78 |
| 4c-4 read-back gaps: downloads, sent Gmail, form submits, wallpaper; Wi-Fi + wallpaper undo | #79 |
| Phase 6: Chrome / PowerShell / Claude Code history into unconfirmed facts (opt-in per source) | #80 |
| Handoff board | #86 |
| Phase 5: judges for worker results (websites, lint / build, research sources) | #87 |
| 4f: one record per task, app notes, app-path skills, lessons from corrections | #88 |
| Leftovers: cloud re-plan (goal + outcomes only), research send-back, usage-limit handoff card | #89 |
| Phase 8: routine suggestions + level-2 recording design (design only) | #90 |
| Phase 7: activity log level 1 (off by default), routines, DPAPI form profile | #91 |
| 4g: scheduled and repeating jobs | #92 |
| Fix a date-dependent research test | #93 |

What was learned running nine sessions at once:
- Every branch edits `docs/OWNERS.md` and the plan doc, so each merge made the rest conflict. Merge one at a time:
  the PR's own session merges `origin/master` in, re-runs `check.cmd`, pushes, then it is merged when CI is green.
- Nine sessions hit the 5-hour usage limit together. Save unfinished work as a pushed `WIP:` commit so another
  session can carry on.
- A worker session must not merge PRs itself (one did, and the rest had to catch up).

## Rules every branch must keep (from `AGENTS.md` and the plan)

- `nova/core/app/conversation.py` is at its 800-line limit (`test_packages`): don't add there.
- Compiled regexes in `nova/core/app/*.py` belong in `nova/intents.py` or protocol (`test_intents`).
- A new tool goes in `nova/actions/tools/`, the `tools/__init__.py` name table, and `risk.LEVELS` (`test_risk`).
- Win32 code lives in `nova/platform` (`test_platform`).
- Never commit `data/`, keys, or anything from the user's real history; tests use fixtures and fakes.
- Write strings with backslashes with an editor tool, not a bash heredoc. The repo is CRLF with autocrlf.
