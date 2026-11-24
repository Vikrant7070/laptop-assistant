# Who is working on what

One line per area that someone is actively changing. **Sessions update their own line**: add it when you start, and
remove it (or set the owner to "—") when your PR merges. Check `python scripts/who_edits.py <paths>` too: this file says
who *means* to work somewhere, the script says who *has* uncommitted or unmerged changes there.

| Area (paths) | Owner (session, agent or person) | Branch | Since |
|---|---|---|---|
| Manager Phase 4: 4-pre, 4a, 4b, 4c-1 merged (PRs #50-#55, #57, #61). 4c-2 (PR #67), 4c-3 (PR #69); 4d-1 (PR #70); 4d-2 (PR #73); 4e-1 risk levels in progress (`nova/actions/risk.py`, `session.is_risky`, `tools.run_tool`, `watching.outside_order`); then 4e-2, 4f, 4g, each after the user's go | Claude session (manager loop) | `claude/manager-4c-2`, `-4c-3`, `-4d`, `-4d-2`, `-4e` | 2026-11-24 |
| Improvement roadmap Phase 1: R10 voice state machine (`nova/core/app/voice_machine.py`, the shadow calls in `voice_io.py`); R14, R15, R11, R13, R12 merged | Claude session (roadmap structure) | `feat/r10-voice-state` | 2026-11-22 |
| Part B leftovers | OpenCode | `refactor/part-b` | 2026-11 |
