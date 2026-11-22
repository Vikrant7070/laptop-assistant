# Who is working on what

One line per area that someone is actively changing. **Sessions update their own line**: add it when you start, and
remove it (or set the owner to "—") when your PR merges. Check `python scripts/who_edits.py <paths>` too: this file says
who *means* to work somewhere, the script says who *has* uncommitted or unmerged changes there.

| Area (paths) | Owner (session, agent or person) | Branch | Since |
|---|---|---|---|
| Manager Phase 4: 4-pre (task card, save + resume, task strip, `/tasks`, question cards) and 4a (ask → brief → go, `nova/actions/manager.py`, `nova/core/app/managing.py`) merged (PRs #50-#55); 4b-4d not started, they wait for the user's go-ahead | Claude session (manager loop) | — | 2026-11-22 |
| Improvement roadmap Phase 1: R10 voice state machine (`nova/core/app/voice_machine.py`, the shadow calls in `voice_io.py`); R14, R15, R11, R13, R12 merged | Claude session (roadmap structure) | `feat/r10-voice-state` | 2026-11-22 |
| Part B leftovers | OpenCode | `refactor/part-b` | 2026-11 |
