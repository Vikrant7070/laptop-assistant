# Child brain — Phase 0 write-up: ground and guard

Date: 2026-11-20. Branch `claude/child-brain-phase0-411200`. Details per package are in
[docs/child-brain-dev-plan.md](../../docs/child-brain-dev-plan.md) ("WP 0.x result" sections).

## What exists now

| Piece | Where | In one line |
|---|---|---|
| Own Python setup | `.venv-childbrain`, `requirements-childbrain.txt` | PyTorch 2.11 (CUDA 12.8) on the RTX 4050; Nova untouched |
| Run cards | `childbrain/runcard.py` | Every run saves settings + seed + code fingerprint; re-runs give the same numbers bit for bit, CPU and GPU |
| 12 games | `childbrain/worlds/` | One shared format; 1–3 M steps/s on the GPU; 9 practice games, 3 sealed exam games |
| Changed versions | `worlds/changed.py`, `worlds/mixed.py` | Look-changed, rules-changed and mixed versions of any game |
| Guard v1 | `childbrain/guard/` | Brain in its own process in a Windows job; asks for everything; allowed list, limits, chained log, off switch in 13–49 ms |
| Versions + nightly exam | `guard/versions.py`, `guard/exam.py`, `guard/nightly.py` | Sandboxed, exactly repeatable exam; keeps only no-worse versions; rolls back damage automatically |
| GPU timing | `childbrain/measure/timing.py` | Warm-up, CUDA events, median; refuses to time while the GPU is busy |

Tests: `.venv-childbrain\Scripts\python -m pytest childbrain` → about 380, all passing (~3 min). Nova's own test
run skips them.

## What worked

- **Exactness.** Runs, exams and games repeat exactly. That's what makes "the score changed, so something was
  damaged" a reliable rule in the nightly check.
- **Writing a second, independent check for every game rule found real bugs:**
  - exam mazes leaking into the practice set;
  - Pong games that were lost before the player could move;
  - mazes too small to give enough different layouts.
- **The guard's tests behave like an attacker:** a sneaky brain, a spammer, an endless loop, code that reaches
  outside, a version damaged in place with faked fingerprints. All were caught.

## What didn't, or isn't finished

- **The tripwire is not a wall.** The code check stops obvious escapes, but a brain process could still reach
  files or the network if its code found a way round. Real isolation (a restricted Windows account / AppContainer,
  no network) must come before Phase 5.
- **Power mode.** In "balanced" mode the GPU clock sometimes drops mid-timing (1 of 6 checks). The timing tool
  flags it, but the plan's "fixed power mode" needs you to pick "Best performance" while timing.
- **Sharing the GPU with Nova.** With Nova's models loaded, only ~0.5–1.5 GB of the 6 GB is free, and
  timings are worthless while Nova works. Experiments must run with Nova closed, or overnight.
- **Exam scores aren't scaled per game yet.** Catch counts ±10, most games ±1. Fair scaling against random /
  best-known baselines comes with the first learning brains.
- **The night doesn't start by itself yet.** Running when the laptop is idle and charging is left for when
  there's something to learn overnight.
- **Plan count.** The plan said 7 practice + 3 sealed games; there are 9 + 3, because the mixed games were extra.

## Random-player floors (what every brain must beat)

Snake −0.88 · Maze 0.16 · Catch −7.5 · Push-the-box 0.08 · Avoid −0.92 · Coins 2.6 · Colour-match 0.03 ·
Connect-4 0.28 · Maze-coins 0.28 · (sealed) Key-and-door 0.06 · Pong-lite −0.51 · Maze-enemy −0.34

## Next decision

Phase 1, "show the problem": a plain brain that learns each game alone (WP 1.1), then the same brain learning
the 7+ practice games one after another, to chart how much it forgets (WP 1.2). That chart is the baseline every
later phase must beat.
