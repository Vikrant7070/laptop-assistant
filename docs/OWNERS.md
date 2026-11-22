# Who is working on what

One line per area that someone is actively changing. **Sessions update their own line**: add it when you start, and
remove it (or set the owner to "—") when your PR merges. Check `python scripts/who_edits.py <paths>` too: this file says
who *means* to work somewhere, the script says who *has* uncommitted or unmerged changes there.

| Area (paths) | Owner (session, agent or person) | Branch | Since |
|---|---|---|---|
| Manager Phase 4: step 0 merged (PR #24); step 4-pre (task state) added to `docs/plans/nova-manager-plan.md` (doc only); the code and the manager loop (`nova/actions/manager.py`) aren't started, they wait for the user's go-ahead | Claude session (manager plan) | `docs/manager-task-state` | 2026-11-22 |
| Child brain: `childbrain/`, `docs/plans/child-brain-*.md`; the Child brain page (`nova/core/app/childbrain_tab.py`, `static/childbrain.html`) | Claude sessions (child brain WP 1.2, training switch) | `claude/child-brain-wp1.2`, `feat/training-switch` | 2026-11-22 |
| Improvement roadmap Phase 1: R9 event stream (`nova/core/app/eventbus.py`, `background.py`, `voice_io.py`, `session.py`, `static/nova.js`, `static/ear.html`); R7, the skills split and the docs tidy-up are merged | Claude session (roadmap Phase 1) | `feat/r9-event-stream` | 2026-11-22 |
| Part B leftovers | OpenCode | `refactor/part-b` | 2026-11 |
