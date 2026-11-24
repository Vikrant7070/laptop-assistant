# Who is working on what

One line per area that someone is actively changing. **Sessions update their own line**: add it when you start, and
remove it (or set the owner to "—") when your PR merges. Check `python scripts/who_edits.py <paths>` too: this file says
who *means* to work somewhere, the script says who *has* uncommitted or unmerged changes there.

| Area (paths) | Owner (session, agent or person) | Branch | Since |
|---|---|---|---|
| Manager Phase 4: 4-pre to 4e-1 merged (PRs #50-#55, #57, #59, #61, #67-#74). 4e-2 undo + settings by level in progress (`nova/actions/undo.py`, `judging.execute`, ⚙ Access); then 4f, 4g, each after the user's go | Claude session (manager loop) | `claude/manager-4e-2` | 2026-11-24 |
| Improvement roadmap Phase 1: R10 voice state machine (`nova/core/app/voice_machine.py`, the shadow calls in `voice_io.py`); R14, R15, R11, R13, R12 merged | Claude session (roadmap structure) | `feat/r10-voice-state` | 2026-11-22 |
| Free ladder (strongest free Gemini first, next one when its limit is used up): `nova/brain/usage.py`, `nova/brain/cloud.py`, `config/cloud_free.json`, Brain tab switch. Local first (Part B) merged PR #68 | Claude session (free AI / cloud meter) | `feat/free-model-ladder` | 2026-11-24 |
| Local first v2 L6 "see whether it gets better": `nova/brainstats.py`, `/api/brain/stats` (`api.py`), `static/brainstats.js`, the Brain tab's stats hook in `static/index.html`, `card["climbed"]` in `resume.save` | Claude session (lf2-l6) | `claude/lf2-l6-brain-stats-e68899` | 2026-11-24 |
| Local first v2 L5 part 1: light/hard jobs on the free ladder (`usage.route(..., job=)`, `light_from` in `config/cloud_free.json`). Part 2 (wiring call sites) after L1+L4 merges | Claude session (lf2-l5-light-route) | `claude/lf2-l5-light-route-949be4` | 2026-11-24 |
| Manager 4c-4: read-back gaps (downloads, sent mail/messages, form submits, wallpaper; Wi-Fi + wallpaper undo) — `nova/actions/readback.py`, `nova/actions/undo.py`, `wallpaper()` in `nova/platform` | Claude session (manager read-back gaps) | `claude/manager-readback-gaps-6fe565` | 2026-11-24 |
| Local first v2 L3: check before "done" (type_text + file-not-found readers in `nova/actions/readback.py`, failed-again climb hook in `judging.readback_verdict` / `brains.py`). Stacked on PR #79 | Claude session (lf2-l3-readback) | `claude/lf2-l3-readback-014f96` | 2026-11-24 |
| Manager Phase 7: activity log level 1 + form profile (`nova/actions/activity.py`, `nova/actions/forms.py`, `nova/core/app/privacy.py`, `static/privacy.js`) | Claude session (manager phase 7) | `claude/manager-phase7-e77e40` | 2026-11-24 |
| Part B leftovers | OpenCode | `refactor/part-b` | 2026-11 |
| Barge-in echo fix (`nova/audio/voice/barge_in.py`, `tests/test_barge_in.py`) | Claude session (barge-in echo) | `claude/bargein-echo-4ec2d0` | 2026-11-24 |
