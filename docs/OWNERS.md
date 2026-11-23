# Who is working on what

One line per area that someone is actively changing. **Sessions update their own line**: add it when you start, and
remove it (or set the owner to "—") when your PR merges. Check `python scripts/who_edits.py <paths>` too: this file says
who *means* to work somewhere, the script says who *has* uncommitted or unmerged changes there.

| Area (paths) | Owner (session, agent or person) | Branch | Since |
|---|---|---|---|
| Manager Phase 4: 4-pre, 4a and 4b merged (PRs #50-#55, #57). Plan rewritten 2026-11-23 for every laptop task: 4c read-back checks, 4d stuck/loop watch, 4e risk levels, 4f remember + app notes, 4g scheduled jobs (`docs/plans/nova-manager-plan.md`); building waits for the user's go-ahead | Claude session (manager loop) | `docs/manager-all-tasks` | 2026-11-23 |
| Improvement roadmap Phase 1: R10 voice state machine (`nova/core/app/voice_machine.py`, the shadow calls in `voice_io.py`); R14, R15, R11, R13, R12 merged | Claude session (roadmap structure) | `feat/r10-voice-state` | 2026-11-22 |
| Planner model: `qwen3.5:4b` as `deep_model`, planner token budget + own sampling (`nova/brain/router.py`, `nova/brain/llm.py` `chat`, `config/model_routing.json`) | Claude session (model choice) | `feat/qwen35-planner` | 2026-11-23 |
| Part B leftovers | OpenCode | `refactor/part-b` | 2026-11 |
