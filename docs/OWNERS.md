# Who is working on what

One line per area that someone is actively changing. **Sessions update their own line**: add it when you start, and
remove it (or set the owner to "—") when your PR merges. Check `python scripts/who_edits.py <paths>` too: this file says
who *means* to work somewhere, the script says who *has* uncommitted or unmerged changes there.

| Area (paths) | Owner (session, agent or person) | Branch | Since |
|---|---|---|---|
| Manager Phase 4, step 4-pre (a): the task card (`nova/task_state.py`, hooks in `conversation.py` / `judging.py` / `skillrun.py`); next 4-pre (b) save + resume, then the strip, `/tasks`, the question card, then 4a-4d (`nova/actions/manager.py`) | Claude session (manager loop) | `feat/manager-task-card` | 2026-11-22 |
| Improvement roadmap Phase 1: R10 voice state machine (`nova/core/app/voice_machine.py`, the shadow calls in `voice_io.py`); R14, R15, R11, R13, R12 merged | Claude session (roadmap structure) | `feat/r10-voice-state` | 2026-11-22 |
| Part B leftovers | OpenCode | `refactor/part-b` | 2026-11 |
