# Docs

## Reference
- [ARCHITECTURE.md](ARCHITECTURE.md): module map, the request lifecycle, conventions.
- [REVIEW.md](REVIEW.md): the code review and the Part B summary.
- [adr/](adr/): short records of decisions already made.
  - [0001](adr/0001-deterministic-first-judge.md) deterministic checks decide "done"
  - [0002](adr/0002-tool-registry.md) tools are declared with `@tool`
  - [0003](adr/0003-packages-answer-from-home-module.md) packages answer from the home module
  - [0004](adr/0004-data-paths-via-store.md) data paths via `store`
- [OWNERS.md](OWNERS.md): who is working on which area (once roadmap R1 is merged).
- [iphone-app.md](iphone-app.md), [vroid-nova-guide.md](vroid-nova-guide.md): how-tos.

## Plans (`plans/`)
Every plan starts with `Status: active | done | dropped — <date>, <one line>`. Update that line when the status
changes.

| Plan | Status |
|---|---|
| [child-brain-plan.md](plans/child-brain-plan.md) | active: the research idea |
| [child-brain-dev-plan.md](plans/child-brain-dev-plan.md) | active: Phase 0 and WP 1.1 done |
| [device-network-plan.md](plans/device-network-plan.md) | active: phases 1–3 built |
| [nova-upgrade-plan.md](plans/nova-upgrade-plan.md) | active: B1 done, Part A not started |
| [heard-bubble-latency-plan.md](plans/heard-bubble-latency-plan.md) | done |
| [mic-sensitivity-plan.md](plans/mic-sensitivity-plan.md) | done |
| [part-b-plan-v3.md](plans/part-b-plan-v3.md) | done (PRs #8–#18) |
| [skills-v2-plan.md](plans/skills-v2-plan.md) | done (phase 10 dropped) |
| [nova-manager-plan.md](nova-manager-plan.md) | active: Phase 4 under way. **Still in `docs/`**: the Phase 4 branch is editing it. It moves to `plans/` (with its status line) when Phase 4 merges. |

The improvement roadmap (`improvement-roadmap.md`), the project review and the file-split plan stay in `docs/` while
the roadmap is being worked through.

**Older plans outside the repo:** OpenCode's `nova-part-b-plan.md` (v1) and `nova-part-b-plan-v2.md` (v2) in
`~/.opencode/plan/` are superseded by [part-b-plan-v3.md](plans/part-b-plan-v3.md). They aren't copied here.
