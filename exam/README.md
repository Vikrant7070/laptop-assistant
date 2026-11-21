# Task exam

How often does Nova really do what she's asked? 25 real tasks (`tasks.toml`), each checked by code on the real
desktop: an app really open, a file really written, dark mode really on. The runner is `nova/exam.py`.

```
python -m nova.exam --list                                   # the tasks; nothing runs
python -m nova.exam --model qwen3-vl:4b-instruct --yes       # all of them, on this laptop
python -m nova.exam --model cloud:google:gemini-2.5-flash --group files --yes
```

- **It acts on your laptop**: it opens and closes Notepad, Calculator, a YouTube tab and Settings, types, switches dark
  mode (restored at the end) and writes files in its own temporary sandbox folder. Close your work and quit Nova
  first (it refuses to run while Nova is running).
- It runs only when you start it with `--yes`: never on a schedule, never in CI (decided 2026-11-21).
- Results go to `results/<date>-<model>.json` and `.md`: the **success rate**, the **false "done" rate** (she said
  done, a check failed), the **honest failure rate** (not done, and she said so), and median seconds per task.
  Commit them, so later changes can be compared against them.
- PRs that change prompts, heuristics, models or the agent loop include a before/after run (PR template).
