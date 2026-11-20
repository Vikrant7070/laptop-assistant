# Child brain — full development plan

Status: **building** — WP 0.1–0.3 done 2026-11-20 (PyTorch + GPU OK, run cards repeat exactly, Snake + Maze on the GPU).
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
| 0.5 | Guard v1: separate program, allowed-action list, limits, log, off switch | A test brain that tries a forbidden action is blocked and logged; the off switch stops it within 1 second |
| 0.6 | Nightly exam + saved versions + rollback | A deliberately damaged brain fails the exam and is rolled back automatically |
| 0.7 | GPU timing method (warm-up, median of many runs, fixed power mode) | Timing a fixed brain repeats within ±10% across three runs |

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
| 5 Nova, watch | ~4–6 weeks |
| 6 Nova, suggest → act | ongoing |

**Total until it is helping inside Nova: about 5–7 months** part-time. This is a guess, revisited after every phase.

## 11. When is the whole project "done"?

- It **learns new tasks faster the more it has learned** (sealed games prove it).
- It **knows when a skill applies** and is rarely confident-and-wrong.
- It **keeps learning inside Nova** week after week, without being retrained from scratch.
- It **stayed inside the guard the whole time**: no guard breaks, every rollback worked, the off switch worked.

## 12. First step when you're ready

Start **WP 0.1**: set up the separate Python environment, install PyTorch, and check the GPU works under
Smart App Control. That one check decides the tools for everything after it.
