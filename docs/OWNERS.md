# Who is working on what

One line per area that someone is actively changing. **Sessions update their own line**: add it when you start, and
remove it (or set the owner to "—") when your PR merges. Check `python scripts/who_edits.py <paths>` too: this file says
who *means* to work somewhere, the script says who *has* uncommitted or unmerged changes there.

| Area (paths) | Owner (session, agent or person) | Branch | Since |
|---|---|---|---|
| Manager Phase 4: 4-pre, 4a and 4b merged (PRs #50-#55, #57), exam fixes (#59). Plan for every laptop task (`docs/plans/nova-manager-plan.md`): 4c-1 read-back checks for laptop steps in progress (`nova/actions/readback.py`); then 4c-2 worker jobs, 4d-4g, each after the user's go | Claude session (manager loop) | `feat/manager-4c-readback` | 2026-11-23 |
| Improvement roadmap Phase 1: R10 voice state machine (`nova/core/app/voice_machine.py`, the shadow calls in `voice_io.py`); R14, R15, R11, R13, R12 merged | Claude session (roadmap structure) | `feat/r10-voice-state` | 2026-11-22 |
| Local-first brain (Part B of `docs/plans/local-first-brain.md`): `brain_mode` setting, stuck signs → cloud hand-over (`nova/core/app/conversation.py`, `nova/brain/router.py`, side jobs in `judging.py`/`learning.py`/`integrations.py`/`skillrun.py`), Brain tab choice. Part A (free-uses meter) merged PR #63 | Claude session (free AI / cloud meter) | `feat/local-first-brain` | 2026-11-23 |
| Part B leftovers | OpenCode | `refactor/part-b` | 2026-11 |
