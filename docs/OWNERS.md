# Who is working on what

One line per area that someone is actively changing. **Sessions update their own line**: add it when you start, and
remove it (or set the owner to "—") when your PR merges. Check `python scripts/who_edits.py <paths>` too: this file says
who *means* to work somewhere, the script says who *has* uncommitted or unmerged changes there.

| Area (paths) | Owner (session, agent or person) | Branch | Since |
|---|---|---|---|
| Manager 4f remember and learn: one record per task (episodes on the task row, `nova/db.py`), app notes (`nova/actions/appnotes.py`), lessons from corrections (`nova/actions/lessons.py`), `learner.py`, `learning.py`. 4-pre to 4e-2 merged (PRs #50-#75) | Claude session (manager 4f) | `claude/manager-4f-c45edf` | 2026-11-24 |
| Improvement roadmap Phase 1: R10 voice state machine (`nova/core/app/voice_machine.py`, the shadow calls in `voice_io.py`); R14, R15, R11, R13, R12 merged | Claude session (roadmap structure) | `feat/r10-voice-state` | 2026-11-22 |
| Free ladder (strongest free Gemini first, next one when its limit is used up): `nova/brain/usage.py`, `nova/brain/cloud.py`, `config/cloud_free.json`, Brain tab switch. Local first (Part B) merged PR #68 | Claude session (free AI / cloud meter) | `feat/free-model-ladder` | 2026-11-24 |
| Part B leftovers | OpenCode | `refactor/part-b` | 2026-11 |
