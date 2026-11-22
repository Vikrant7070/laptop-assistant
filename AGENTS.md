# Working on Nova: rules for every agent (Claude, OpenCode, anyone)

Nova is a Windows desktop voice assistant (Python 3.12, Flask, pywebview, Ollama + optional cloud brains). Several
sessions often work on it at once, and **the live Nova runs from the main folder**. These rules keep them out of each
other's way. Background: `docs/ARCHITECTURE.md`, `docs/improvement-roadmap.md`.

## 1. Where to work
- Work only in a git worktree under `.claude/worktrees/<name>`, on your own branch, started from fresh `origin/master`.
- **Never edit the main folder** (`C:\Users\vikra\OneDrive\Desktop\Laptop assistant`). Nova runs from it; a half-made
  edit there breaks her for the user.
- Never touch another session's worktree or branch. In particular OpenCode's `refactor/part-b` worktree is its own.

## 2. Before you edit a file
```
python scripts/who_edits.py <files or folders>
```
It lists the other worktrees that are changing those paths (uncommitted, or committed but not yet on master), and
exits 1 if there are any. **If it lists one, stop and ask** the user (or the session that owns it) before editing.
`docs/OWNERS.md` says who is working on which area; add or update your own line when you start, and clear it when your
PR merges.

## 3. Branches and PRs
- Short branches: merge within about two days. Merge `origin/master` in before you start and again before you push.
- One piece of work = one branch = one PR against `master`.
- **The gate:** `scripts\check.cmd` (ruff + pytest) green before every push. CI runs the same and must be green before
  merge. Auto-merge is off; the user says when to merge.
- Commit messages: one imperative line saying what changed in plain words ("Add the task exam runner"), then a body
  if needed. PR bodies: *What changed*, *Verification* (exact commands and results), *Not done* (and why).
- Changes that alter prompts, heuristics, models or the agent loop need a task exam run (`python -m nova.exam`, the
  user starts it) before and after; say so in the PR.

## 4. Code conventions
- `nova/actions/tools/` and `nova/core/app/` are **packages that answer `tools.<name>` / `app.<name>` from the name's
  home module**. Patch and assign names **in their home module** (`tools.home_of(name)`, `app.home_of(name)`), never on
  the package: a patch on the package changes a copy the real code never reads. The guard tests enforce this.
- Add a tool with the `@tool` decorator (`nova/actions/registry.py`) in the module for its area. No hand-written tables.
- Data paths come from `nova/store.py` (`store.current().settings`, `.file(name)`), never a hard-coded `data/` path.
  Tests get a temp data folder from `conftest.py`.
- Keep modules under ~800 lines. Errors are logged (`log_exception`), never silently swallowed.
- Every behaviour change comes with a test. The suite is hermetic (no real keyboard, voice, browser or PowerShell).

## 5. Never commit
- `data/` (personal profile, chats, settings, encrypted keys), API keys, tokens, passwords, `.env` files.
- Large binaries outside Git LFS: `*.vrm` and `*.wav` go through LFS (`.gitattributes`, roadmap R3).
- Anything under `.venv*`.

## 6. Safety rules that are product decisions (don't change them without the user)
- A PowerShell/terminal command that changes anything **always asks**, at every autonomy level, with no setting to turn
  that off. Read-only commands may run freely.
- The task exam runs only when the user starts it (`--yes`), never on a schedule and never by an agent on its own.
- The child brain is its own project (`C:\Users\vikra\OneDrive\Desktop\child-brain`, since 2026-11-22), not part of Nova.
  Nova only writes the graphics-card signal it reads (`nova/brain/gpu_signal.py`), so Nova goes first on the card.
