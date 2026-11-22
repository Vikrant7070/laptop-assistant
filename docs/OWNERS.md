# Who is working on what

One line per area that someone is actively changing. **Sessions update their own line**: add it when you start, and
remove it (or set the owner to "—") when your PR merges. Check `python scripts/who_edits.py <paths>` too: this file says
who *means* to work somewhere, the script says who *has* uncommitted or unmerged changes there.

| Area (paths) | Owner (session, agent or person) | Branch | Since |
|---|---|---|---|
| Manager Phase 4: step 0 merged (PR #24); step 4-pre (task state) added to `docs/plans/nova-manager-plan.md` (doc only); the code and the manager loop (`nova/actions/manager.py`) aren't started, they wait for the user's go-ahead | Claude session (manager plan) | `docs/manager-task-state` | 2026-11-22 |
| Child brain: `childbrain/`, `docs/plans/child-brain-*.md`; the Child brain page (`nova/core/app/childbrain_tab.py`, `static/childbrain.html`) | Claude sessions (child brain WP 1.2, training switch) | `claude/child-brain-wp1.2`, `feat/training-switch` | 2026-11-22 |
| Improvement roadmap Phase 1 (structure items R14, R15, R11, R13, R12, R10): `nova/intents.py`, then prompts, threads, storage, platform, voice state | Claude session (roadmap structure) | `refactor/r14-intents`, `refactor/r15-prompt-files`, `refactor/r11-runtime-threads` | 2026-11-22 |
| Part B leftovers | OpenCode | `refactor/part-b` | 2026-11 |
