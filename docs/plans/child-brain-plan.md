Status: active — 2026-11-21, the research idea; the build follows child-brain-dev-plan.md.

# Child brain — research plan

Status: **plan only, no code yet** (2026-11-20). A separate research track from Nova's manager plan
([nova-manager-plan.md](nova-manager-plan.md), section 9b).
The step-by-step build plan is in [child-brain-dev-plan.md](child-brain-dev-plan.md).

## 1. The idea, in four steps

1. **Learn how to learn.** The child brain starts almost blank (no human data) and first gets good at *learning
   itself*, so each new thing is quicker to pick up than the last.
2. **Know when and where to use what it learned.** Learning means nothing if you don't know when it applies.
   The brain must recognise "this situation needs that skill", even when the situation looks different, and also
   notice "this one looks familiar but needs something else".
3. **Learn by doing real things inside Nova.** Then it moves into Nova, does real laptop work, and keeps learning
   from what happens. It doesn't freeze after training like today's models do.
4. **A guard around it, so it can't go rogue.** It learns freely inside a fence. It can never change the fence,
   and anything that matters needs Nova's checks or your Allow.

It's built for chips, not copied from the human brain: the search for its shape counts GPU speed too.

## 2. Decisions (from our Q&A)

| Question | Choice |
|---|---|
| Main goal | Learns how to learn, knows when to apply it, keeps learning by doing |
| Human data | **None** for the core brain. It learns from rules, results and curiosity |
| Hardware | **Laptop only**: 6 GB GPU and CPU, no cloud spending |
| Training ground | Small games first (Snake, Connect-4, mazes…), then a small 2D world, then Nova |
| Final home | **Nova**: real tasks, inside a guard |

## 3. The honest part

- **"Keep learning" is unsolved.** When a normal network learns something new, it overwrites the old
  ("catastrophic forgetting"). That's why big labs freeze their models.
- **"Knowing when to apply" is also hard.** Today's models often use the wrong skill because the situation *looks*
  similar. Teaching this on purpose is the most interesting part of your idea.
- **A brain that changes itself can drift** into bad habits. That's why the guard (step 4) is part of the design
  from day one, not added at the end.
- **Scale:** on a laptop, a realistic result is a small learner, roughly a small animal mind in a simple world, and
  later a set of self-improving helpers inside Nova. It won't be human-level, and nothing here would be conscious.

## 4. Step 1 — learn how to learn

| Idea | What it is, simply |
|---|---|
| **Evolve a learner, not a player** | Evolution picks brains that learn *new, unseen* games fastest, not brains that are good at one game |
| **Practice old things (replay)** | While learning something new, it sometimes replays old experiences so it doesn't forget |
| **Sleep** | By day it learns fast into a short-term "notebook". Overnight, while the laptop is idle, it sorts that into long-term skills |
| **Grow new parts** | A really new kind of task gets a small new expert (mixture-of-experts), so old skills stay safe |
| **Curiosity** | A reward for finding things it can't predict yet, so it explores on its own |
| **Chip-friendly shapes** | Ternary / 1-bit weights (BitNet style), memory cells (Mamba style), cellular automata. Kept if they're faster on the GPU |

**Sign it's working:** game 10 is learned faster than game 1, and game 1 is still remembered.

## 5. Step 2 — know when and where to apply it

Every skill the brain owns comes with a **"when to use me" detector**. That's a small part that looks at the
situation and says "I fit here, I'm 80% sure".

- **Same skill, different look.** Tests where the game *looks* new (other colours, other size, walls moved) but the
  old skill still works. The brain should reuse it, not start from zero.
- **Same look, different need.** Tests where the game looks familiar but the rules quietly changed. The brain
  should notice its skill stopped working, lower its confidence, and learn something new.
- **Mixing skills.** Tests that need two old skills together (e.g. "find the exit" + "avoid the enemy").
- **"I don't know" is allowed.** When no skill fits well, it says so and explores carefully (or, inside Nova,
  asks the slow path).
- **Check after use.** After using a skill, it checks the result and updates the detector: "that worked here" or
  "not here".

**Sign it's working:** on secret test games, it picks the right old skill most of the time, notices quickly when
a skill doesn't fit, and is rarely confident-and-wrong.

## 6. Step 3 — learn by doing, inside Nova

Nova's big brains stay as they are (frozen, smart, slow). The child brain becomes a set of **small skills that keep
learning from your laptop's real day**:

- **Teacher = what actually happened.** Did the task succeed? Did Nova's deterministic check pass? Did you accept
  or undo it? That's the feedback, so it needs no human training data.
- **Example skills:** "is the user at the desk", "what kind of window is this", "is this an error box", "which way
  of doing this task usually works on *this* laptop", "is this sentence a command or chat".
- **Knowing where it applies:** each skill's detector learns which apps, windows and moments it's good in, and
  stays quiet elsewhere.
- **Sleep at night:** the day's experience is sorted into long-term skills while the laptop is idle.

## 7. Step 4 — the guard (so it can't go rogue)

The guard is **outside** the child brain, written by us, and the brain **cannot read or change it**.

| Guard | What it does |
|---|---|
| **Fixed action list** | It can only use actions we allow. Anything else doesn't exist for it |
| **Trust levels, earned slowly** | Watch only → suggest → act on safe, undoable things → wider. It moves up a level only after passing tests, and you approve each step up |
| **Nova's "Never" list stays** | No passwords, OTPs, money or IDs. No pushing, publishing, deleting, paying or installing without your Allow. Its learning can't unlock any of this |
| **Slow path decides what matters** | When it's unsure, or the action is important, Nova's normal checks or you decide, not the child brain |
| **Can't change its own rewards** | It can't edit its scoring, its tests, its guard or its own logs |
| **Daily exam** | Every night, after sleep, it takes fixed tests. If it got worse or broke a rule, the new version is thrown away |
| **Rollback** | Every night's version is saved, so you can go back to any day |
| **Limits** | Caps on actions per minute, GPU time, disk space. No internet of its own |
| **Off switch** | One command (or a voice phrase) stops it, and Nova carries on without it |
| **Everything logged** | What it did, why (which skill, how sure), and the result. You can read it any time |

## 8. Phases

| Phase | What | Done when |
|---|---|---|
| **0. Ground** | About 10 tiny games that run fast on the GPU, the test stream, the timing method, and **a first version of the guard** (action list, logs, exam, rollback) | All games run; a plain network can learn each alone; the guard blocks a test "bad action" |
| **1. Show the problem** | A plain network learns the games one after another | We have a chart of how badly it forgets. That's the baseline |
| **2. Learn how to learn** | Replay, sleep, grow-new-experts, then evolution of the learner | After 10 games in a row: ≥ 90% kept on old games, later games learned faster, an evolved learner beats the hand-made one on secret games |
| **3. Know when to apply** | "When to use me" detectors, with the look-changed / rules-changed / mixed tests from section 5 | On secret tests it picks the right skill most of the time and is rarely confident-and-wrong |
| **4. A small world, lifelong** | A simple 2D survival world with curiosity. One brain runs for days without reset, sleeping each night, inside the guard | Over a week it keeps unlocking new things, doesn't lose old ones, and passes every nightly exam |
| **5. Nova: watch only** | It watches Nova's real day and predicts what would work, but does nothing | Its predictions get better week after week |
| **6. Nova: suggest, then act** | Suggests first, then acts on safe, undoable things, moving up only with your OK | It saves time on real tasks, you rarely undo it, and it never breaks a guard rule |

Each phase gets a short write-up (what worked, what didn't, charts) before the next one starts.

## 9. Out of reach on this laptop

- A human-level or conscious mind. Nobody knows how to build one yet.
- A language model that keeps learning. Language needs far more data and compute than a laptop has.
  (Nova keeps using the big brains for language.)
- Big-lab-sized searches (thousands of GPU-days). Ours stay tiny on purpose.
- Precise energy measurements. We score speed instead.
- Custom low-level GPU code. Smart App Control may block unsigned binaries, so we stick to well-known, signed libraries.

## 10. Gotchas to remember

- Evolution and learning both **cheat when they can** (looping snakes, gaming the score). That's one more reason
  the scoring and tests live behind the guard.
- Speed readings move with power mode and heat. Fix the power mode and warm up before timing.
- The 6 GB GPU is shared with Nova. Run evolution only while Nova's 3D view and Whisper are off. Nightly "sleep" runs
  when the laptop is idle.
- Keep every run reproducible: save the seed, genes, scores and speed of every brain tried.
