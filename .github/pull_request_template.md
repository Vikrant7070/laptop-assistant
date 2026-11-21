## What changed


## Verification
- [ ] `scripts\check.cmd` (ruff + pytest) green: _N passed_
- [ ] **Task exam** (`python -m nova.exam --model <id> --yes`, started by the user): needed when this PR changes
      **prompts, heuristics (routing, tool picking, done-checks), models or the agent loop**. Paste the before/after
      success and false-"done" rates from `exam/results/`, or say why it isn't needed.
- [ ] Other files checked with `python scripts/who_edits.py <paths>` (no other worktree changing them)

## Not done

