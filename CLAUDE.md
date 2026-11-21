# Claude Code in this repository

Read and follow **`AGENTS.md`**: it holds the rules for every agent working on Nova (worktrees only, never the main
folder, `python scripts/who_edits.py <paths>` before editing, the `scripts\check.cmd` gate, patching home modules,
`@tool`, and what never gets committed).

Quick reminders:
- The main folder runs the live Nova. Work in `.claude/worktrees/<name>` only.
- `python scripts/who_edits.py <paths>` exits 1 if another worktree is changing those paths: stop and ask.
- Update your line in `docs/OWNERS.md` when you start on an area.
