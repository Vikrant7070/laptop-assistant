# Who is working on what

One line per area that someone is actively changing. **Sessions update their own line**: add it when you start, and
remove it (or set the owner to "—") when your PR merges. Check `python scripts/who_edits.py <paths>` too: this file says
who *means* to work somewhere, the script says who *has* uncommitted or unmerged changes there.

| Area (paths) | Owner (session, agent or person) | Branch | Since |
|---|---|---|---|
| Manager Phase 4: step 0 merged (PR #24); step 4-pre (task state) added to `docs/plans/nova-manager-plan.md` (doc only); the code and the manager loop (`nova/actions/manager.py`) aren't started, they wait for the user's go-ahead | Claude session (manager plan) | `docs/manager-task-state` | 2026-11-22 |
| Child brain: `childbrain/`, `docs/plans/child-brain-*.md`; the Child brain page (`nova/core/app/childbrain_tab.py`, `static/childbrain.html`) | Claude sessions (child brain WP 1.2, training switch) | `claude/child-brain-wp1.2`, `feat/training-switch` | 2026-11-22 |
| Improvement roadmap Phase 1: `docs/` tidy-up, `nova/actions/skills/` split (`scripts/splits/skills.toml`, `nova/skill_eval.py`, `tests/test_packages.py`), R7 typed settings (`nova/core/app/config.py`) | Claude session (roadmap Phase 1) | `docs/tidy-plans`, `refactor/skills-package`, `feat/r7-typed-settings` | 2026-11-22 |
| Part B leftovers | OpenCode | `refactor/part-b` | 2026-11 |
