# Who is working on what

One line per area that someone is actively changing. **Sessions update their own line**: add it when you start, and
remove it (or set the owner to "—") when your PR merges. Check `python scripts/who_edits.py <paths>` too: this file says
who *means* to work somewhere, the script says who *has* uncommitted or unmerged changes there.

| Area (paths) | Owner (session, agent or person) | Branch | Since |
|---|---|---|---|
| Manager Phase 4, step 4-pre: (a) the task card (`nova/task_state.py`, PR #50) and (b) save + resume (`nova/core/app/resume.py`, `tasks` table, PR #51), (c) the live strip (`static/taskstrip.js`, PR #52), (d) the `/tasks` page (`nova/core/app/tasks_page.py`, PR #53), (e) the question card (`nova/core/app/asking.py`, PR #54) and 4a (`nova/actions/manager.py`, `nova/core/app/managing.py`); next 4b-4d | Claude session (manager loop) | `feat/manager-task-card`, `feat/manager-task-resume`, `feat/manager-task-strip`, `feat/manager-tasks-page`, `feat/manager-question-card`, `feat/manager-4a` | 2026-11-22 |
| Improvement roadmap Phase 1: R10 voice state machine (`nova/core/app/voice_machine.py`, the shadow calls in `voice_io.py`); R14, R15, R11, R13, R12 merged | Claude session (roadmap structure) | `feat/r10-voice-state` | 2026-11-22 |
| Part B leftovers | OpenCode | `refactor/part-b` | 2026-11 |
