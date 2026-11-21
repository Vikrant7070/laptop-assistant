# Nova — Part B, plan v3

Supersedes `nova-part-b-plan.md` (v1) and `nova-part-b-plan-v2.md` (v2) in `~/.opencode/plan/`.
Written 2026-11-21 after reviewing OpenCode's `refactor/part-b` branch against current `master`.

---

## 1. Where things stand

### What OpenCode finished (branch `refactor/part-b`, worktree `~/.local/share/opencode/worktree/a51aff/part-b`)

| Phase | State | Commit(s) | Review notes |
|---|---|---|---|
| 0 Safety net | done | `7462df0` `8688833` `2b0165c` `552bd71` | `pyproject.toml`, `requirements-test.txt`, `scripts/check.cmd/.ps1`, `ci.yml`, 49-case golden set, `docs/ARCHITECTURE.md`. Good. CI has **never run** — the workflow is not on GitHub's `master` yet. |
| 1 Protocol | done | `d74aa67` | `nova/protocol.py` + tests. Prompt text still spells out `TASK DONE:` / `[check after step N]` inline in `app.py` (3 places) — fine, those are prompt instructions, not parsers. |
| 5 Model-aware history | done | `ff60cee` | `llm.context_size`, `app.history_limit` (local stays 12, cloud ≤ 60). Clean. |
| 6 Exception logging | done | `35b05a7` | `nova/log.py` (rate-limited), 48 `# intentional:` comments, AST test `test_no_silent_failures.py`. |
| 9 Packaging/docs | **half, uncommitted** | — | `nova/__main__.py`, `__version__`, `tests/test_entrypoint.py`, README/REVIEW/ARCHITECTURE edits sitting in the worktree. Also a drive-by docstring fix in `toolpick.py` (64 → 101 tools) that belongs in its own commit. |
| 2, 3, 4, 7, 8 | not started | — | |

Branch gate at review time: `ruff check .` → clean; pytest → **1005 passed, 1 skipped in 88 s** (includes the uncommitted Phase 9 tests).

### What changed on `master` since the branch was cut (`03d0f4e` → `027af99`, 17 commits)

- Manager phase 2: `nova/actions/judge.py`, `learner.py`, `questions.py`; `app.py` +383 lines, `tools.py` +268.
- Child brain Phase 0 (`childbrain/`), app guard, typing-by-paste, mic level.
- Current sizes: `app.py` 4,620 lines, `tools.py` 3,098 (101 tools), `voice.py` 1,823, `skills.py` 1,212.

### Problems this creates

1. **Merge conflicts.** `master` ⟵ `refactor/part-b` conflicts in `nova/core/app.py` (2 hunks: `check_step` and the
   "not finished yet" nudge — master rewrote both around the Judge, the branch moved them onto `protocol.py`) and
   `tests/joint_report.json`.
2. **Invariants will break on merge.** Master added ~15 new `except Exception` handlers (learner, questions, app, tools)
   without `# intentional:` or `log_exception` → `test_no_silent_failures` fails after merge. Master also added at
   least one new inline `[note from Nova's system]` emitter that should go through `protocol.py`.
3. **Phase 7 is mostly done already, on master.** `judge.py` runs deterministic checks first, a failed check can't be
   overruled by vision, vision looks at most once at the end and is advisory. That is the v1 Phase 7 design.
4. **Parallel writers on the same files.** `feat/manager-phase3` (worktree `.claude/worktrees/manager-phase3`) has
   uncommitted changes in `app.py`, `tools.py`, `toolpick.py`, `coworker.py`, `skills.py` and a new `workers.py`.
   Phases 2 and 3 rewrite exactly those files. `claude/child-brain-wp1.1` only touches `childbrain/` — no clash.
5. **Long-lived branch.** The real cause of 1–2: five phases piled up on one branch while master moved. v1's
   "one phase = one branch" was not followed in practice.
6. **Numbers in v1 are stale.** 64 → 101 tools; 905 → ~1,000+ tests; "no file over ~800 lines" is not reachable for
   `tools.py` by a registry alone — it also has to become a package.

---

## 2. Rules (revised)

1. **Land early, land often.** Each phase merges to `master` via PR within ~2 days of starting. Rebase on `master`
   before starting each phase. No more stacking phases on one branch.
2. **Coordinate writers.** Before a phase touches `app.py` / `tools.py` / `toolpick.py` / `skills.py` /
   `coworker.py`, no other branch may have uncommitted work in those files. Check `git worktree list` +
   `git -C <wt> status` first.
3. Behaviour lock, no drive-bys, re-export shims — unchanged from v1.
4. **New code obeys the new invariants.** After Step A lands, any PR that adds an `except Exception` or a prompt marker
   must use `log_exception` / `protocol.py`. CI enforces it.
5. Tag before each phase: `part-b-<n>-start`, `part-b-<n>-done`.

---

## 3. The plan

### Step A — Finish, sync and ship what exists (S, risk low) — done 2026-11-21, in review

Result: Phase 9 committed (2 commits), master merged (2 `app.py` hunks kept master's Judge logic, markers via
`protocol.py`), 1 silent handler + 3 reset-on-error reads now log, `conftest` redirects `nova.log.FILE`
(it was writing to the real task log). Master added no new tools, routes or skills, so no golden cases were
needed. Gate: 1096 passed, 1 skipped, ruff clean.

1. In the part-b worktree: commit the Phase 9 work as two commits — (a) entry point + docs, (b) the `toolpick`
   docstring fact fix on its own.
2. `git merge master` into `refactor/part-b` (merge, not rebase — the branch has 7 commits and a clean history is not
   worth rewriting them).
   - `app.py` hunks: keep master's Judge logic verbatim; re-express only its marker strings through `protocol.py`
     helpers. Emitted text must stay byte-identical to master's.
   - `joint_report.json`: regenerate with the joint check, don't hand-merge.
3. Sweep master's new code for the Phase 1/6 invariants: every new `except Exception` gets `log_exception(...)` or
   `# intentional: <why>`; route new note emitters through `protocol.note(...)`.
4. Add 3–5 golden cases for master's new routing (worker/manager requests, learner skill offers).
5. Gate green (`scripts\check.cmd`), push, open PR → first real CI run → merge to `master`. Tag `part-b-a`.

**Accept:** CI green on GitHub; `master` contains Phases 0, 1, 5, 6, 9a; `refactor/part-b` deleted.

### Step B — Store (Phase 4, M, risk medium) — done 2026-11-21, in review

Result: `nova/store.py`; 20 modules ask `store.current()` for paths (16 frozen path globals removed, plus
`settings.json` reads in sfx/avatar/body and the voice scripts that ignored `NOVA_DATA`). `conftest` installs one
`Store` per test; tests use `store.current().<name>` instead of module globals. `tests/test_store.py` runs a fresh
interpreter with `NOVA_DATA` and fails if any data path ignores it or any module freezes one at import.
Not done on purpose: the modules' own load/save functions were already atomic (except `forms`/`body`) and were
left as they are; `Store.read_json`/`write_json` exist for new code.

No longer waits for the `app.py` split — it only touches path globals and `conftest`, and is the safest structural
change. Same steps as v1 Phase 4 (`nova/store.py`, one redirect point, fresh-interpreter `NOVA_DATA` test).
`nova/log.py`'s `FILE` and master's new learner/episode files are included.

**Accept:** `NOVA_DATA` alone redirects every file; `conftest` installs one `Store`; no test patches a path global.

### Step C — UI split (Phase 8, M, risk low)

Independent of everything else; can run in parallel with B in its own session (touches only `static/`).
`index.html` is 1,048 lines with 28 `innerHTML` sites. Same steps as v1 Phase 8; also split `knowledge.html`'s inline
script if it has one.

### Gate — manager phase 3 lands first

Do not start D or E until `feat/manager-phase3` is committed and merged (or explicitly parked by the user). Otherwise
one of the two pieces of work gets rewritten by hand.

### Step D — Tool registry + `tools.py` package (Phase 2, L, risk medium)

Same as v1 Phase 2, plus:
- Target is `nova/actions/tools/` as a package: `registry.py`, and one module per batch (`system.py`, `files.py`,
  `windows.py`, `apps.py`, `notes.py`, `coworker.py`, `body.py`, plus whatever manager phase 3 adds, e.g. workers).
  `nova/actions/tools/__init__.py` re-exports `TOOLS`, `FUNCS`, `run_tool`, `RISKY`… so callers don't change.
- 101 tools → 7–8 batches, one PR per 2–3 batches, each merged to `master` (rule 1).
- Canary test from v1 stays.

**Accept:** adding a tool = one decorated function; no module in `actions/tools/` over ~800 lines; suite green.

### Step E — Split `app.py` (Phase 3, L, risk high)

v1's target layout stands, with two changes:
- Add **`nova/core/judge_loop.py`** (or fold into `conversation.py`): `check_step`, `final_judge`, `look_check`,
  `propose_checks`, `settle` — master's Judge glue is now a coherent ~300-line unit.
- **Each extraction is its own PR merged to master** (prompts → session → api → integrations → voice_io →
  judge_loop → conversation → background → drop shim). Not one branch.
- Recount `conftest` resets before starting; master has added some.

**Accept:** as v1 (no module > ~800 lines, no import cycles, suite green, manual smoke of chat + voice + tray + phone).

### Step F — Vision: close-out only (was Phase 7, S, risk low)

The Judge already makes vision advisory. What remains:
1. `NOVA_VISION_CHECK=0|1` escape hatch around the one `look_check` in `final_judge`.
2. Audit that `judge.goal_checks` covers the v1 list (`open_app`, `open_website`, fullscreen, dark mode); add tests for
   any gap.
3. Decision-matrix unit test for `final_judge` if `test_judge.py` doesn't already have one.

### Step G — Finish Phase 9 (S)

After E: rewrite `docs/ARCHITECTURE.md` module map for the new layout, "where to change what" table in README,
Part B section in `docs/REVIEW.md` final version.

---

## 4. Order and effort

| Step | Was | Effort | Risk | Depends on | Can run in parallel with |
|---|---|---|---|---|---|
| A Finish & sync | 9a + merge | S | low | — | — |
| B Store | 4 | M | medium | A | C |
| C UI split | 8 | M | low | A | B, D, E |
| *gate* | — | — | — | manager phase 3 merged | — |
| D Registry | 2 | L | medium | A, gate | C |
| E Split app.py | 3 | L | high | D | C |
| F Vision close-out | 7 | S | low | A | B, C |
| G Docs final | 9b | S | low | E | — |

**Low-risk stopping point:** after A + B + C + F the codebase has CI, the protocol, logging, model-aware history, one
data store and a split UI — without touching `tools.py`/`app.py` structure.

**Still out of scope:** FastAPI/async, a database, a frontend framework, autonomy model or default model changes.

---

## 5. Definition of done

Unchanged from v1, with "no file over ~800 lines" applying to `nova/actions/tools/*` as well, and CI running on
GitHub (not only locally).
