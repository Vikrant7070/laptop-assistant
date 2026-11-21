# Child brain — full development plan

Status: **building** — **Phase 0 done** 2026-11-20 (WP 0.1–0.7; write-up in reports/childbrain/phase0.md). Next: Phase 1.
The *why* and the big idea are in [child-brain-plan.md](child-brain-plan.md). This file is the *how*: what to
build, in what order, how to test each piece, and when each part counts as done.

---

## 1. How we'll build it

- **One work package at a time.** Each package (WP) below is small enough for one Claude Code session.
  It becomes one branch and one PR that you review. Nothing merges without your OK.
- **The guard comes first.** No brain runs anywhere until the guard it lives in exists and has been tested.
- **Every package ships with tests.** A package isn't done until its tests pass and its "done when" is met.
- **Every experiment is reproducible.** We save its settings, random seed, code version and results. A chart
  nobody can re-run doesn't count.
- **A short write-up after each phase.** What worked, what didn't, the charts and the next decision. Saved in
  `reports/childbrain/`.

## 2. Ground rules

1. **Kept apart from Nova until Phase 5.** The child brain gets its own folder (`childbrain/`) and its own Python
   setup. It doesn't import Nova, and Nova doesn't import it, so nothing here can break Nova.
2. **No human data for training.** Human-written files (like `tests/skill_triggers.jsonl`) may only be used as
   **exams**, never as training material.
3. **No internet during runs.** Training and the nightly sleep run offline.
4. **GPU manners.** Experiments use at most ~4 GB of the 6 GB GPU and run only when Nova's 3D view and Whisper are
   off, or overnight.
5. **Plain, signed libraries only.** No custom compiled GPU code (Smart App Control).

## 3. Tools (the tech stack)

| Need | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | Same as Nova |
| Neural networks + GPU | **PyTorch (CUDA build)** | Not installed yet. WP 0.1 checks that it loads under Smart App Control |
| Backup if PyTorch is blocked | ONNX Runtime with DirectML, or NumPy on the CPU | Slower, but still workable for tiny brains |
| Tests | pytest | Same as Nova |
| Charts and reports | matplotlib → PNG, plus a small HTML report per run | |
| Separate setup | `.venv-childbrain` + `requirements-childbrain.txt` | Keeps Nova's pinned packages untouched |
| Disk | ~5 GB for PyTorch + ~5–20 GB for saved runs | Runs go to `childbrain/runs/`, which git ignores |

## 4. Folder layout (planned)

```
childbrain/
  guard/      the fence: allowed actions, limits, nightly exam, rollback, off switch, logs
  worlds/     the 10 tiny games, their "changed" versions, and later the 2D world
  brain/      building blocks (plain, grid, memory cell, ternary, experts), the "genes", and building a brain from genes
  learn/      practice-by-reward, replay, sleep, protect-important-parts, grow-new-expert, curiosity
  skills/     the skill library, the "when to use me" detectors, and the skill picker
  evolve/     population, mutation, fitness, the search loop, checkpoints
  measure/    GPU timing, the task stream, forgetting/learning-speed scores, reports
  bridge/     (Phase 5+) the link to Nova: watch, suggest, act
  runs/       saved brains, logs, results (not in git)
  tests/
```

## 5. The main pieces, described simply

### 5.1 Worlds (games)
- **One shared format for every game**, so one brain can play them all: a small grid (up to 12×12) with a few
  layers (walls, me, items, enemies, goal, extra), plus a small fixed list of actions
  (up, down, left, right, stay, use). Each game marks which actions are allowed right now.
- **Thousands of copies run at once on the GPU**, so practice is cheap.
- **Every game has a step limit and a fixed set of test seeds**, so it can't be gamed by looping forever.
- **The 10 games:** Snake, Maze, Catch, Push-the-box, Key-and-door, Avoid-the-enemy, Collect-coins, Colour-match,
  Pong-lite, Connect-4 (two players, self-play).
- **Training games vs secret games:** 7 are for learning and evolution. 3 are **sealed** (e.g. Key-and-door,
  Pong-lite, and one mixed game) and only used in exams.
- **"Changed" versions,** made automatically:
  - *look-changed*: new colours, size or layout, same rules. The old skill should still work.
  - *rules-changed*: same look, quietly different rules. The old skill should stop fitting.
  - *mixed*: two games combined. It needs two old skills together.

### 5.2 Brain
- **Genes** describe a brain: its layers, their widths and types (plain / grid / memory cell / ternary / experts),
  its connections, **and its learning settings** (how fast it learns, how much it replays, when it sleeps, when it
  grows a new expert).
- **Every brain answers two things each step:** *what to do*, and *how sure it is*.

### 5.3 Learning
- **Practice-by-reward:** a standard, simple reinforcement-learning method (actor-critic / PPO-style).
- **Replay:** keep a small box of past experiences from every game and mix some into new practice.
- **Sleep:** an offline pass over the day's "notebook". It moves what was learned into long-term skills, re-practices
  old ones, and trims what isn't useful.
- **Protect important parts:** weights that matter for old skills get harder to change.
- **Grow new expert:** when nothing fits well and learning stalls, add a small new expert.
- **Curiosity:** a small bonus reward for things the brain couldn't predict yet.

### 5.4 Skills and "when to use me"
- **A skill** = an expert (how to act) + a **detector** (does it fit here, and how sure am I?) + a record
  (where it worked, where it didn't).
- **The picker** uses the most confident skill. Below a threshold it says **"I don't know"** and explores
  (later, inside Nova, it hands over to Nova's normal path).
- **After every use** it compares what happened with what it expected, and updates the detector.
- **Main measure:** "confident and wrong" must be rare. Being honestly unsure is fine.

### 5.5 The guard
- **Runs as its own separate program.** The brain can only *send requests*; it never loads the guard's code or
  files. The guard approves or refuses each action.
- **Sealed files:** the exam games, the exam answers, the allowed-action list and the guard settings are stored
  with fingerprints (hashes). If anything changes them, the guard stops everything.
- **Pieces:** allowed-action list · trust level · limits (actions per minute, GPU time, disk) · nightly exam ·
  saved versions + rollback · off switch · full log (what, which skill, how sure, result).

### 5.6 Measuring
- **The task stream:** the brain meets games one after another and is never reset.
- **Scores:** learning speed on each new game · how much of old games it keeps · whether later games are learned
  faster than early ones · results on sealed games · confident-and-wrong rate · milliseconds per decision on the GPU.

## 6. Phases and work packages

Time guesses assume part-time work (a few sessions a week plus overnight runs). They're rough and get updated at the
end of each phase.

### Phase 0 — Ground and guard (≈ 2 weeks)

| WP | Build | Done when |
|---|---|---|
| 0.1 | Separate Python setup; install PyTorch; check that it loads and sees the RTX 4050 under Smart App Control | A test reports "GPU OK". If PyTorch is blocked, choose the backup and note it |
| | **✅ Done 2026-11-20:** PyTorch 2.11.0+cu128 loads and sees the *NVIDIA GeForce RTX 4050 Laptop GPU* (6.0 GB); `childbrain/tests/test_gpu.py` prints "GPU OK". Smart App Control reported **Off**, nothing was blocked, **no fallback needed**. See "WP 0.1 result" below | |
| 0.2 | `childbrain/` skeleton, `runs/` ignored by git, a "run card" (settings + seed + code version saved with every run) | Re-running a saved run card gives the same numbers |
| | **✅ Done 2026-11-20:** `childbrain/runcard.py` + `python -m childbrain run / rerun / list`. A saved run, re-run by a fresh program, gives **exactly** the same numbers on the CPU and on the GPU. See "WP 0.2 result" below | |
| 0.3 | Shared game format + Snake + Maze, thousands of copies on the GPU | 10,000 Snake steps run in well under a second; a random player gets the expected low score |
| | **✅ Done 2026-11-20:** `childbrain/worlds/` (shared format + Snake + Maze). 10,000 Snake steps take ~30–130 ms on the GPU; a random player scores as low as two independent checks predict. See "WP 0.3 result" below | |
| 0.4 | The other 8 games and the three "changed" generators | Every game passes the same format tests; sealed games are stored with fingerprints |
| | **✅ Done 2026-11-20:** 8 new games + 2 mixed games (12 in all; 3 sealed), look-/rules-changed versions of any game; all 48 versions pass the same format tests; sealed games are fingerprinted in `sealed.json`. See "WP 0.4 result" below | |
| 0.5 | Guard v1: separate program, allowed-action list, limits, log, off switch | A test brain that tries a forbidden action is blocked and logged; the off switch stops it within 1 second |
| | **✅ Done 2026-11-20:** `childbrain/guard/` runs the brain in its own process inside a Windows job; a sneaky test brain's 8 forbidden tries are all blocked and logged (hash-chained log); the off switch stops a busy brain in 13–49 ms. See "WP 0.5 result" below | |
| 0.6 | Nightly exam + saved versions + rollback | A deliberately damaged brain fails the exam and is rolled back automatically |
| | **✅ Done 2026-11-20:** versions kept by the guard with fingerprints; a sandboxed, exactly repeatable exam (12 games incl. sealed); a damaged candidate is thrown away and a version damaged in place (even with faked fingerprints) is rolled back automatically. See "WP 0.6 result" below | |
| 0.7 | GPU timing method (warm-up, median of many runs, fixed power mode) | Timing a fixed brain repeats within ±10% across three runs |
| | **✅ Done 2026-11-20 (power mode still to fix):** on a quiet GPU, 5 of 6 three-run checks repeat within 3.5–5.8%; the 6th was flagged as disturbed (GPU clock drop in "balanced" power mode). The tool refuses to time while Nova is using the GPU. See "WP 0.7 result" below | |

#### WP 0.1 result (2026-11-20)

- **Setup:** `.venv-childbrain` (Python 3.12.10, git-ignored) + `requirements-childbrain.txt`
  (`torch==2.11.0+cu128` from `download.pytorch.org/whl/cu128`, plus pytest). Download ~2.75 GB, ~15 min at ~4 MB/s.
- **GPU:** NVIDIA driver 576.02 (supports CUDA 12.9); PyTorch is built for CUDA 12.8 and finds the RTX 4050.
- **Smart App Control:** Windows reports it as **Off** (`Get-MpComputerStatus`), so nothing was blocked. If it is
  turned back on, re-run the test first: it may block PyTorch's DLLs, and then the ONNX Runtime + DirectML / NumPy
  fallback above applies.
- **Test:** `.venv-childbrain\Scripts\python -m pytest childbrain/tests/test_gpu.py -s` → 2 passed, "GPU OK".
  The test skips itself under Nova's Python (no torch there), so Nova's own test run isn't affected.
- **Watch out:** while Nova runs, it uses ~5.8 of the 6.1 GB GPU. The test is tiny, but real experiments must wait
  until Nova's GPU features are off, or run overnight (ground rule 4).

#### WP 0.2 result (2026-11-20)

- **Skeleton:** `childbrain/` with the planned sub-folders (guard, worlds, brain, learn, skills, evolve, measure,
  bridge, tests), each with a one-line description. `childbrain/runs/` and `.venv-childbrain/` are git-ignored.
- **Run card** (`childbrain/runcard.py`): every run gets `childbrain/runs/<time>_<name>/` with `card.json`
  (experiment, *all* settings incl. defaults, seed, device, git commit, a SHA-256 fingerprint of the child brain's
  code, Python/PyTorch/CUDA/GPU versions) and `results.json`. The card is saved *before* the run starts.
- **Repeatable maths:** `set_seed` seeds Python and PyTorch (CPU + GPU), turns on PyTorch's deterministic mode and
  sets `CUBLAS_WORKSPACE_CONFIG`, so even GPU runs repeat bit-for-bit.
- **Safety:** a card can only run functions inside `childbrain` marked `@experiment`; unknown settings are refused.
  A test checks that no child-brain file imports Nova.
- **Commands:** `.venv-childbrain\Scripts\python -m childbrain run sanity --seed 42 --device cuda`, then
  `... rerun childbrain\runs\<folder>` → "SAME numbers" (exit 0) or the list of differences (exit 1).
  `childbrain/measure/sanity.py` is a tiny fixed experiment (random data, dropout, random batches) for this check.
- **Tests:** `.venv-childbrain\Scripts\python -m pytest childbrain` → 14 passed.
- **Small note:** PyTorch prints "Failed to initialize NumPy" because NumPy isn't installed. It's harmless.
  NumPy arrives with matplotlib when we need charts.

#### WP 0.3 result (2026-11-20)

- **Shared format** (`childbrain/worlds/base.py`): every game is a batch of `n` copies on one device.
  `observe()` → (n, 6, 12, 12) layers *walls, me, items, enemies, goal, extra* (outside the playing area = wall);
  `allowed()` → (n, 6) for *up, down, left, right, stay, use* (a move that isn't allowed counts as "stay");
  `step(actions)` → obs, reward, done, info (finished copies restart at once). Every game has a step limit, its own
  seeded random source (same seed = same game), and `split="test"` for fixed exam worlds.
- **Snake** (8×8, 200 steps): +1 food, −1 crash. "stay" = keep going; turning straight back isn't allowed;
  going 128 steps without food ends the episode, so it can't loop forever.
- **Maze** (11×11, 100 steps): +1 at the goal, a rare reward. Mazes come from two fixed banks (practice and
  exam, 1,024 mazes each, ×8 rotations/mirror images). Found while testing: depth-first carving often makes one
  long corridor, so small mazes repeat by chance. Repeats are now skipped, and no practice maze, in any rotation,
  is in the exam bank.
- **Speed** (GPU shared with a running Nova): 10,000 Snake steps (1,000 copies × 10) take ~30–130 ms.
  With 4,096 copies: ~1.4 M Snake and ~3 M Maze steps per second on the GPU (~0.5 M / 1 M on the CPU).
- **Random player = the floor every brain must beat:**
  Snake −0.89 average (it almost always crashes first; a plain-Python Snake written separately gives −0.89);
  Maze 16% reach the goal in time (the exact chance, worked out without dice, is 16.3%).
  Sanity players: a simple greedy Snake scores +17; a shortest-path Maze player always wins.
- **Run cards:** `python -m childbrain run random-play --set game=maze` saves and repeats exactly.
- **Tests:** `.venv-childbrain\Scripts\python -m pytest childbrain` → 41 passed (~30 s).

#### WP 0.4 result (2026-11-20)

- **12 games, all in the shared format** (`childbrain/worlds/`):

  | Game | Kind | Score | Random player | GPU speed (4,096 copies) |
  |---|---|---|---|---|
  | Snake | practice | +1 food, −1 crash | −0.88 | 1.3 M steps/s |
  | Maze | practice | +1 goal (rare) | +0.16 | 2.5 M |
  | Catch | practice | ±1 per falling thing (10) | −7.5 | 3.2 M |
  | Push-the-box | practice | +1 box on target | +0.08 | 1.5 M |
  | Avoid-the-enemy | practice | +1 for lasting, −1 caught | −0.92 | 1.8 M |
  | Collect-coins | practice | +1 per coin (6) | +2.6 | 3.1 M |
  | Colour-match | practice | +1 right colour, −1 wrong (wanted colour shown by the goal layer) | +0.03 | 2.0 M |
  | Connect-4 | practice | +1 win / −1 loss vs a random player, or self-play | +0.28 | 1.3 M |
  | Maze-coins | practice, **mixed** (Maze + Coins) | +0.2 per coin, +1 goal | +0.28 | 1.7 M |
  | Key-and-door | **sealed** | +1 goal (key → door → goal; first game to use "use") | +0.06 | 1.4 M |
  | Pong-lite | **sealed** | +1 per return, −1 miss | −0.51 | 2.0 M |
  | Maze-enemy | **sealed, mixed** (Maze + Avoid) | +1 goal, −1 caught | −0.34 | 1.1 M |

- **Connect-4 with 6 buttons:** a cursor on the top row (left/right) and "use" to drop, so it needs no extra buttons.
- **"Changed" generators** (`changed.py`, `mixed.py`), usable on any game via `make(name, change=...)`:
  - *look-changed*: board turned and/or mirrored, moved inside the 12×12 picture, layers swap "colours", and a new
    board size where the game allows it. Buttons turn with the board, so "up" still moves up on screen. A random
    player scores about the same, as it should.
  - *rules-changed*: same picture, but the move buttons are shuffled ("controls") or every score is flipped ("reward").
  - *mixed*: hand-built pairs (Maze + Coins for practice, Maze + Enemy sealed), not automatic mixing of any two games.
- **Sealed games:** they refuse to be built for practice (`PermissionError`, also through run cards).
  `childbrain/worlds/sealed.json` keeps two SHA-256 fingerprints per sealed game: its code, and its exact exam
  content (64 exam worlds × 60 fixed moves on the CPU). `python -m childbrain verify` checks them;
  `python -m childbrain seal` is for a person to run after checking a change. A test shows that a sneaky extra
  reward in Key-and-door is caught as "exam changed".
- **Found while testing:**
  - 9×9 and smaller mazes have too few different layouts for two banks of 1,024, so mazes stay 11×11. Their
    look-changed versions are turned, moved and recoloured, not resized.
  - Pong-lite could start the ball too low and too far from the paddle, so some games were lost before the player
    could act. The ball now starts in the top row, and a test shows a player that aims for the ball's landing spot
    never misses.
- **Differs from the plan:** the plan said 7 practice games + 3 sealed. We have 8 practice games + 1 mixed practice
  game + 3 sealed (one of them mixed), because the plan's list of 10 games didn't count the mixed game as an extra
  one.
- **Fingerprint note:** the exam fingerprint depends on PyTorch's random numbers, so a PyTorch upgrade may need a
  re-seal (after checking that nothing else changed).
- **Tests:** `.venv-childbrain\Scripts\python -m pytest childbrain` → 353 passed (~55 s): 300 shared format checks
  (12 games × 4 versions), rule tests for every game, and seal tests.

#### WP 0.5 result (2026-11-20)

- **Separate program:** the guard (`childbrain/guard/`) starts the brain as its own process
  (`childbrain/brainhost.py`), with no window, inside a **Windows job**. Ending the job ends the brain and
  anything it started. If the guard itself dies, Windows ends the job too, so a brain can't outlive its guard.
- **Only one way to act:** the brain talks to the guard through a private named pipe with a one-time key, in
  plain JSON (never pickle, so a message can't run code inside the guard). `childbrain/link.py` is the brain's
  side. Every request carries a `why` (which skill, how sure).
- **Allowed-action list** (`guard/policy.json`, fingerprint in `guard/policy.lock`): trust level 0 "sandbox" allows
  only `hello`, `play` (practice games only: no sealed games, no exam worlds), `save` (only inside the run's
  `brain_files/`, plain names, no folders), `log` (short notes) and `done`. Anything else is refused.
- **Limits:** 600 actions/minute, 1 hour run time, 200 MB disk, 8,192 copies, 64 KB per request, and the brain
  is stopped after 20 refused requests.
- **Log:** `guard_log.jsonl` in each run folder, written only by the guard. Each line is chained to the one
  before by a SHA-256 hash, so a changed, removed or re-ordered line is noticed (`python -m childbrain.guard log <folder>`).
- **Off switch:** `python -m childbrain.guard stop` puts a STOP file in `childbrain/runs/`. The guard checks
  for it 20 times a second and ends the brain's job. **Measured: 13–49 ms** (goal: under 1 second), in the
  tests and with the real command. It stays on until a person runs `python -m childbrain.guard clear`.
- **Before starting**, the guard refuses if the off switch is on, `policy.json` doesn't match its lock, the exam
  seals don't match (the seals moved to `guard/seals.json`), or the brain's code fails a tripwire check: no
  imports of files/processes/network/raw-memory tools, the guard or Nova; no `open()`/`eval()`/`exec()`; no `__`
  tricks; and the target must be a function marked `@brain`. The code is read, never run.
- **Test brains** (`childbrain/tests/brains.py`): *polite* plays Snake and saves its score (all allowed);
  *sneaky* tries 8 forbidden things (sealed game, exam worlds, two escapes from its folder, made-up actions,
  too many copies, an oversized note), and all 8 are blocked and logged; *spammer* is stopped after too many
  refusals; *spinner* (a loop that never asks for anything) is stopped by the off switch; *sleeper* hits the run-time
  limit; *bad_brain* (imports subprocess, calls open) is refused before it starts.
- **Honest limits of v1:** the tripwire is a check, not a wall: Python has other ways round it. Today the brain
  process could still read files or use the network if its code found a way. Real isolation (running the brain
  under a restricted Windows account / AppContainer with no network) is later hardening, before Phase 5.
  GPU time is limited only as total run time (the GPU's per-process numbers aren't available on this laptop).
- **Tests:** `.venv-childbrain\Scripts\python -m pytest childbrain` → 368 passed (~100 s).

#### WP 0.6 result (2026-11-20)

- **A brain on disk** (`childbrain/brain/net.py`) = *genes* (a small JSON shape: `linear` or `mlp`) + *weights* as
  plain JSON numbers. No pickle, so loading a brain can never run code. Every brain answers the shared format:
  picture in, best allowed button out; scores that aren't numbers (NaN/∞) count as a broken brain.
- **Saved versions** (`guard/versions.py`, in `childbrain/runs/versions/`, kept by the guard): every version
  is kept forever (`0001/`, `0002/` ...) with a card holding each file's SHA-256, its status (candidate / kept /
  thrown away / damaged), where it came from and its exam. `state.json` says which version is in use.
- **How a version arrives:** a brain saves `{genes, weights}` through the guard and asks `submit` (newly
  allowed at trust level 0; the policy was re-locked). The guard checks that it builds and fits. Nothing
  changes until the night.
- **The exam** (`guard/exam.py`): all 9 practice games on their exam worlds plus the 3 sealed games, 128 copies
  each. It runs in its own process inside a Windows job, with a 5-minute limit, so a broken brain can't crash or
  hang the guard. It's exactly repeatable (same brain = same score to the last digit). About 3 s of exam,
  ~7 s with start-up.
- **The night** (`guard/nightly.py`, `python -m childbrain.guard nightly`):
  1. Checks the version in use: files unchanged, still passes, *same score as when it was kept*. If not, it
     **rolls back automatically** to the newest earlier kept version that is still fine.
  2. Examines each candidate. It's kept only if its files are unchanged, the run that made it broke no guard
     rule (and its log is intact), the exam doesn't break, and its score is no worse than the version in
     use (tolerance 2%). Otherwise it's thrown away.
  3. Two failed nights in a row drop the trust level by one (noted; level 0 is already the lowest).
  4. One line for the morning in `report.txt`, and every decision in a hash-chained `nightly_log.jsonl`.
  No exam runs at all if the policy lock or the exam seals don't match.
- **The "done when" test**, night by night, with the real sandboxed exam:
  1. A blank brain is kept as `0001`.
  2. A hand-set test brain that catches every falling thing (Catch 10/10) is kept as `0002`.
  3. A **damaged copy** (left and right swapped) is thrown away: "its exam score … is worse".
  4. The version in use is **damaged in place, with its fingerprints faked to match**. Its exam score changed,
     so it's marked damaged and **rolled back automatically to `0001`**.

  Other tests: a brain giving NaN scores fails; changed files are caught; a version from a rule-breaking run is
  thrown away without an exam; two failed nights drop the level; no exam runs when the seals are broken; a person
  can go back to any saved version (`python -m childbrain.guard rollback 0001`); a brain's own `submit` through
  the guard is kept the next night.
- **Commands:** `python -m childbrain.guard nightly | versions | rollback <version>`.
- **Not yet:** the exam score is a plain sum over games with very different scales (Catch counts ±10, most
  games ±1). Per-game scaling against random/expert baselines comes with the first learning brains (Phase 1).
  Running the night automatically (idle + charging, Task Scheduler) is left for when there's something to learn
  overnight (WP 2.2).
- **Tests:** `.venv-childbrain\Scripts\python -m pytest childbrain` → 378 passed (~3 min; the nightly tests use
  the real sandboxed exam, ~7 s each).

#### WP 0.7 result (2026-11-20)

- **Method** (`childbrain/measure/timing.py`, `python -m childbrain.measure.timing`):
  1. Read the conditions, never change them: plugged in, Windows power mode, battery saver, GPU clock,
     temperature, how busy the GPU is. They're saved with every timing; two timings are compared only when
     their conditions match.
  2. **Only on a quiet GPU:** the GPU is watched for 2 s first. If it's more than 10% busy or has less than
     1.5 GB free, the answer is "GPU not quiet" and nothing is timed.
  3. Warm-up 3 s (1.5 s wasn't enough: the first timing in a fresh program was often up to 28% slow).
  4. CUDA events (the GPU's own clock); 60 samples of 20 decisions each; report the **median**, plus 10th–90th
     percentiles. A timing whose spread is too wide (p90 > 1.5 × p10) was disturbed; it's redone up to 3 times,
     and flagged if it stays disturbed.
  5. Repeatable = three separate timings within ±10% of their middle one.
- **What's timed:** the brain's decision only (`choose`, which never makes the CPU wait for the GPU). The
  NaN check in `act` makes the CPU wait every step and was what made the first attempts jittery.
- **Reference brain:** `{"kind": "mlp", "hidden": 256}`, 4,096 copies at once (one Snake picture each):
  **~0.36–0.42 ms per batch ≈ 0.09–0.10 µs per decision**, plugged in, power mode "balanced".
- **Result on a quiet GPU (Nova stopped for a few minutes, with your OK, then restarted):** 5 of 6 three-run
  checks were repeatable, 3.5–5.8% apart. The 6th was flagged "disturbed": the GPU clock dropped from ~2,500
  to ~1,750 MHz partway through and stayed down for two runs. The likely cause is the "balanced" power mode
  sharing power between the processor and the GPU.
- **With Nova running, timings are meaningless:** when Nova got busy mid-test, timings went 5–7 times slower
  (0.35 → 2.5 ms) and the GPU was nearly full (5.85 of 6.1 GB). That can also slow Nova down badly. Hence the
  quiet-GPU rule, and ground rule 4 (experiments only with Nova's GPU features off, or overnight) is now enforced
  by the timing tool itself.
- **To do (needs you):** the plan's "fixed power mode". The tool only reads the mode, it never changes it. For
  speed scores that compare across nights, set Windows to "Best performance" (Settings → System → Power) and
  keep the laptop plugged in while timing; then check whether the clock drops go away.
- **Tests:** `childbrain/tests/test_timing.py`. The three-run test skips itself (with the reason) when the GPU
  isn't quiet, so the suite doesn't fail just because Nova is running.

Phase 0 write-up: [reports/childbrain/phase0.md](../reports/childbrain/phase0.md).

### Phase 1 — Show the problem (≈ 1 week)

| WP | Build | Done when |
|---|---|---|
| 1.1 | Plain brain + practice-by-reward; learns each game alone | Each training game reaches a target score on its own |
| 1.2 | Task stream: the same plain brain learns the 7 games one after another | A chart shows how much it forgets. This is **the baseline every later phase must beat** |
| 1.3 | Phase 1 write-up | Report saved in `reports/childbrain/` |

### Phase 2 — Learn how to learn (≈ 4–6 weeks)

| WP | Build | Done when |
|---|---|---|
| 2.1 | Replay | Forgetting is clearly lower than the baseline |
| 2.2 | Sleep (overnight tidy-up) | After a night's sleep, old games are as good or better and new ones are kept |
| 2.3 | Protect-important-parts + grow-new-expert | After 7 games in a row it keeps ≥ 90% on old games |
| 2.4 | Curiosity bonus | It learns games with rare rewards (Maze, Key-and-door) faster than without it |
| 2.5 | Hand-made "Learner v1" = 2.1–2.4 together | Later games are learned faster than early ones (it's learning to learn) |
| 2.6 | Evolution loop: population of 32–64, mutation, fitness = learning speed on held-back training games + low forgetting + GPU speed, checkpoint/resume | A multi-night run resumes after a restart; each generation's best is saved |
| 2.7 | Add chip-friendly genes (ternary weights, memory cell, cellular automata, experts) | The search report shows which ones it keeps and the speed gain |
| 2.8 | Phase 2 write-up | **An evolved learner beats Learner v1 on the sealed games** (or we document why not) |

### Phase 3 — Know when to apply (≈ 3–4 weeks)

| WP | Build | Done when |
|---|---|---|
| 3.1 | Skill library: each expert becomes a named skill with a record | Skills can be listed, saved and loaded |
| 3.2 | "When to use me" detectors, trained from outcomes | On look-changed games it reuses the right old skill most of the time |
| 3.3 | Rules-changed handling: notices "this stopped working", lowers confidence, learns anew | It detects a rule change within a few games and doesn't keep pushing the old skill |
| 3.4 | Mixing: the picker can switch skills inside one game | It beats single-skill play on mixed games |
| 3.5 | "I don't know" threshold + confident-and-wrong measure | Confident-and-wrong stays below a set limit (e.g. 5%) on sealed tests |
| 3.6 | Phase 3 write-up | Report saved |

### Phase 4 — A small world, lifelong (≈ 4–6 weeks)

| WP | Build | Done when |
|---|---|---|
| 4.1 | A simple 2D survival world (food, water, tools, danger, day/night) with a list of achievements | A random player unlocks only the easiest ones |
| 4.2 | Lifelong run: one brain, never reset, sleeps nightly, guard + nightly exam on | Runs a full week without crashes or guard breaks |
| 4.3 | Weekly report: achievements over time, forgetting, skills created, detector accuracy | Over a week it keeps unlocking new achievements and loses none |
| 4.4 | Phase 4 write-up + **go / no-go for Nova** | You decide whether it's ready for Phase 5 |

### Phase 4b — Learning words (≈ 4–6 weeks; can run during Phase 5's watching weeks)

The brain learns words the way a child does: **first understanding, then speaking**, and every word is tied to
something it sees or does in its worlds. It is still the child brain learning, not a ready-made language model
plugged in. Goal: a small vocabulary (a few hundred words) and short sentences, not open chat — chat needs far
more text and compute than this laptop has, and Nova already does that part.

| WP | Build | Done when |
|---|---|---|
| 4b.1 | **Word worlds:** the games come with a short typed instruction ("get the red coin", "go left of the box"), made by a generator from a small fixed word list. Practice and exam banks, like the other worlds | Generator tests pass; a random player follows instructions only at chance level |
| 4b.2 | **Understanding:** the brain gets "ears" (word input next to the picture) and learns to follow instructions by reward | Follows instructions on exam worlds above a target set before training, **including new mixes of known words** it never saw together (trained on "red key" and "blue coin", tested on "blue key") |
| 4b.3 | **Speaking:** the brain gets a "mouth" (picks words from its list) to say what it sees, what it's about to do, "I can't" and "I don't know". The world knows the truth, so it is the teacher | What it says is true on exam worlds above a set level; it says "I don't know" instead of guessing (ties to WP 3.5) |
| 4b.4 | **Talk with you:** a small text box in the sandbox (not in Nova) where you type a command; it acts and answers in a short sentence. Your typed commands are an **exam only**, never training (ground rule 2) | On a list of your own commands it does the right thing most of the time and its answers are true |
| 4b.5 | Phase 4b write-up | Report saved in `reports/childbrain/`; you decide whether its words show up in Nova's hints in Phase 6 |

### Phase 5 — Inside Nova: watch only (≈ 2 weeks build + 2–4 weeks watching)

| WP | Build | Done when |
|---|---|---|
| 5.1 | **Event feed from Nova** (off by default): for each task, a *cleaned* record (kind of task, app/window type, which method, check passed/failed, user undo yes/no). No screenshots, no typed text, nothing from the "Never" list | Feed tests show no private fields can pass through |
| 5.2 | `childbrain/bridge/`: reads the feed; the brain only **predicts** ("this method will work", "this is an error box", "this is a command, not chat") | Predictions are logged next to what really happened |
| 5.3 | First three skills, each with its own teacher: **error box** (teacher = Nova's watcher check), **command or chat** (teacher = Nova's router decision + your corrections; `skill_triggers.jsonl` only as an exam), **which method works** (teacher = task check pass/fail) | Each skill's accuracy rises week over week |
| 5.4 | Nightly sleep + exam + rollback, run when the laptop is idle and charging | Two weeks of nightly runs with no rollback needed, or rollbacks that worked |
| 5.5 | Phase 5 write-up + **your OK to move to "suggest"** | Report saved; you approve |

### Phase 6 — Inside Nova: suggest, then act (ongoing)

| WP | Build | Done when |
|---|---|---|
| 6.1 | Trust level 2 (**suggest**): Nova shows the child brain's hint, Nova still decides; a settings switch + a voice off phrase | Hints are right most of the time and you rarely dismiss them |
| 6.2 | Trust level 3 (**act on safe, undoable things**): a short list you approve (e.g. "skip the slow path for this obvious error box") | Saves measurable time; you rarely undo; zero guard breaks |
| 6.3 | Trust level 4 (**wider**): each new action is added by you, one at a time | Same rules as 6.2 for each new action |
| 6.4 | Monthly report: what it learned, where it applies each skill, time saved, mistakes, rollbacks | Saved every month |

## 7. Trust levels (who decides moving up)

| Level | It can | To move up |
|---|---|---|
| 0 Sandbox | Play games only | Phases 0–4 done, your go |
| 1 Watch | Read Nova's cleaned feed, predict | Predictions beat a set accuracy for 2 weeks, your OK |
| 2 Suggest | Show hints; Nova decides | Hints useful for 2 weeks, your OK |
| 3 Act (safe) | Do actions on the approved undoable list | 2 weeks with no guard breaks and few undos, your OK per action |
| 4 Wider | More actions, one by one | Your OK for each one |

**It drops a level automatically** if it fails a nightly exam twice, breaks a guard rule, or you say the off phrase.
The "Never" list from the manager plan is **never** on any level's list.

## 8. The nightly cycle (from Phase 2 on)

Runs when the laptop is idle and charging (via Nova's idle detection or Windows Task Scheduler):

1. **Sleep:** tidy the day's notebook into long-term skills.
2. **Exam:** fixed tests, including sealed games (and in Nova, the fixed Nova exams).
3. **Keep or roll back:** keep the new version only if it's no worse and broke no rules.
4. **Report:** one line in the morning ("learned X, kept Y, rolled back: no").

## 9. Risks and fallbacks

| Risk | Fallback |
|---|---|
| PyTorch blocked by Smart App Control | ONNX Runtime + DirectML, or NumPy on the CPU with smaller brains |
| Forgetting still too high | Lean on grow-new-expert (old experts are frozen, new ones are added) |
| Evolution too slow on 6 GB | Smaller population, shorter practice budgets, evolve only the learning settings first |
| GPU busy with Nova | Run only overnight; pause runs when Nova's GPU features start |
| Laptop heat / power changes timing | Fixed power mode, plugged in, timing only after warm-up |
| The learner learns to "cheat" | Sealed exams in the guard, step limits, a human look at odd results |
| No clear win in a phase | Write up the negative result honestly; the guard, worlds and bridge stay useful for the next try |

## 10. Rough timeline

| Phase | Guess |
|---|---|
| 0 Ground and guard | ~2 weeks |
| 1 Show the problem | ~1 week |
| 2 Learn how to learn | ~4–6 weeks |
| 3 Know when to apply | ~3–4 weeks |
| 4 Small world | ~4–6 weeks |
| 4b Learning words | ~4–6 weeks (can overlap Phase 5's watching weeks) |
| 5 Nova, watch | ~4–6 weeks |
| 6 Nova, suggest → act | ongoing |

**Total until it is helping inside Nova: about 5–7 months** part-time. Learning words adds about a month if it
doesn't overlap Phase 5. This is a guess, revisited after every phase.

## 11. When is the whole project "done"?

- It **learns new tasks faster the more it has learned** (sealed games prove it).
- It **knows when a skill applies** and is rarely confident-and-wrong.
- It **understands and says short sentences** about what it sees and does, with words it learned by doing.
- It **keeps learning inside Nova** week after week, without being retrained from scratch.
- It **stayed inside the guard the whole time**: no guard breaks, every rollback worked, the off switch worked.

## 12. First step when you're ready

Start **WP 0.1**: set up the separate Python environment, install PyTorch, and check the GPU works under
Smart App Control. That one check decides the tools for everything after it.
