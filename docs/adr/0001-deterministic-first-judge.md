# 0001 Deterministic checks decide "done"; the screen look only advises

Status: accepted (manager plan phase 1, 2026-11)

## Context
The small local model often says "done" when nothing happened, and a vision model looking at a screenshot is slow
and sometimes wrong in both directions.

## Decision
`nova/actions/judge.py` trusts, in order: deterministic checks (`nova/actions/checks.py`: `window_open`,
`page_has`, `dark_mode`, ...) made for the goal by code or proposed by the brain and validated; then, only when no
check covers the goal, one advisory screen look at the end. A failed check means not done, and the vision model
can't overrule it. A vision "no" gets one nudge, then a question to the user.

## Consequences
- "Done" is only as good as the checks: new task shapes need new checks.
- The task exam (roadmap R5) uses the same checks to score success.
