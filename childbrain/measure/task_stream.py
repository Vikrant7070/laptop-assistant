"""WP 1.2: the task stream. ONE plain brain learns the games one after another; we measure what it forgets.

    python -m childbrain run task-stream --device cuda --seed 42

This is the baseline every later phase must beat. It is deliberately plain: one set of settings for every game
(gamma 0.9, no early stop, a fixed move budget per game), nothing done to protect old skills.

After each game the brain sits the EXAM worlds (split="test", greedy, as in learn_alone) of every game in the
stream, so we get a games x checkpoints table. Games not learned yet are scored too (that shows any head start
from earlier games), but forgetting only counts games already learned.

  just_after  exam score right after the game was learned
  at_end      exam score after the whole stream
  forgetting  (just_after - at_end) / (target - random floor): the share of the "learned it" gap that was lost.
              0 = kept it all, 1 = fell back as far as the random player's distance to the target.

The two mazes are refused: they never reached their targets alone (WP 1.1), so forgetting on them can't be measured.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..brain.net import build
from ..learn.ppo import Learner, Settings
from ..runcard import experiment
from ..worlds import PRACTICE, make
from .learn_alone import PASSED_WITH, TARGETS, _seed, score

# Order fixed before the run (2026-11-21): the order WP 1.1 listed the passing games in.
ORDER = ["catch", "snake", "collect-coins", "colour-match", "push-box", "connect-4", "avoid-enemy"]
NOT_IN_STREAM = {"maze", "maze-coins"}  # never passed alone (WP 1.1)

# Moves per game: what each game needed alone in WP 1.1 (PASSED_WITH runs), rounded up with some margin,
# since here every game uses gamma 0.9 and has no early stop.
BUDGETS = {
    "catch": 2_000_000,          # passed at 1.3M
    "snake": 4_000_000,          # 2.6M
    "collect-coins": 4_000_000,  # 2.6M
    "colour-match": 8_000_000,   # 6.6M
    "push-box": 6_000_000,       # 4.6M
    "connect-4": 3_000_000,      # 2.0M
    "avoid-enemy": PASSED_WITH["avoid-enemy"]["max_steps"],  # 20M (needed practice past the target)
}
EXAM_COPIES = 1024
CHART_FILE = "forgetting.png"
TABLE_FILE = "table.md"


def check_games(games: list[str]) -> None:
    if not games:
        raise ValueError("the stream needs at least one game")
    if len(set(games)) != len(games):
        raise ValueError(f"each game once only: {games}")
    for g in games:
        if g in NOT_IN_STREAM:
            raise ValueError(f"{g!r} never reached its target alone (WP 1.1), so forgetting on it can't be "
                             f"measured; leave it out of the stream")
        if g not in PRACTICE:
            raise ValueError(f"{g!r} isn't a practice game; choose from {sorted(set(PRACTICE) - NOT_IN_STREAM)}")


def exam_all(brain, games: list[str], device: str, copies: int = EXAM_COPIES) -> dict:
    return {g: score(brain, g, device, copies, "test", "greedy") for g in games}


def _share(game: str, value: float) -> float:
    """How far from the random player to the target: 0 = random, 1 = at target."""
    target, floor, _ = TARGETS[game]
    return (value - floor) / (target - floor)


@experiment(defaults={
    "games": ORDER, "budgets": BUDGETS, "copies": 1024, "hidden": 256, "layers": 1, "eyes": "grid", "channels": 32,
    "horizon": 64, "lr": 3e-4, "entropy": 0.01, "epochs": 4, "minibatches": 8, "gamma": 0.9, "anneal": True,
    "optimizer": "fresh", "curve_every": 1_000_000, "exam_copies": EXAM_COPIES,
}, after_run=lambda folder, results: save_outputs(folder, results))
def task_stream(settings: dict, device: str) -> dict:
    games = list(settings["games"])
    check_games(games)
    unknown = set(settings["budgets"]) - (set(PRACTICE) - NOT_IN_STREAM)
    if unknown:
        raise ValueError(f"budgets for games that can't be in the stream: {sorted(unknown)}")
    budgets = {g: int(settings["budgets"].get(g, BUDGETS.get(g, 5_000_000))) for g in games}
    if settings["optimizer"] not in ("fresh", "keep"):
        raise ValueError("optimizer must be 'fresh' (new Adam per game) or 'keep' (carry Adam's memory over)")

    genes = {"kind": "plain", "hidden": settings["hidden"], "layers": settings["layers"],
             "eyes": settings["eyes"], "channels": settings["channels"]}
    brain = build(genes).to(device)  # the ONE brain, kept across every game
    s = Settings(horizon=settings["horizon"], lr=settings["lr"], entropy=settings["entropy"], epochs=settings["epochs"],
                 minibatches=settings["minibatches"], gamma=settings["gamma"], anneal=settings["anneal"])
    per_round = settings["horizon"] * settings["copies"]

    checkpoints = ["start"]
    table = {g: [v] for g, v in exam_all(brain, games, device, settings["exam_copies"]).items()}  # game -> exam score per checkpoint
    curve = {"steps": [0], **{g: [table[g][0]] for g in games}}           # finer: exam scores during the stream
    moves, total, clock, last_opt = {}, 0, {}, None
    for game in games:
        world = make(game, n=settings["copies"], device=device, seed=_seed())
        rounds = max(1, budgets[game] // per_round)
        learner = Learner(brain, world, s, total_rounds=rounds)
        if settings["optimizer"] == "keep" and last_opt is not None:
            learner.opt.load_state_dict(last_opt)  # lr is set per round by the anneal, so this only keeps Adam's memory
        every = max(1, settings["curve_every"] // per_round) if settings["curve_every"] else 0
        start = time.perf_counter()
        for r in range(rounds):
            learner.round()
            if every and (r + 1) % every == 0 and r != rounds - 1:
                curve["steps"].append(total + learner.steps)
                for g, v in exam_all(brain, games, device, settings["exam_copies"]).items():
                    curve[g].append(v)
        clock[game] = round(time.perf_counter() - start, 1)
        total += learner.steps
        moves[game] = learner.steps
        last_opt = learner.opt.state_dict()
        now = exam_all(brain, games, device, settings["exam_copies"])
        checkpoints.append(f"after {game}")
        curve["steps"].append(total)
        for g in games:
            table[g].append(now[g])
            curve[g].append(now[g])

    per_game = {}
    for i, g in enumerate(games):
        target, floor, _ = TARGETS[g]
        just_after, at_end = table[g][i + 1], table[g][-1]
        per_game[g] = {
            "target": target, "random_floor": floor,
            "before": table[g][0], "just_after": just_after, "at_end": at_end,
            "reached_target_just_after": just_after >= target, "still_at_target_at_end": at_end >= target,
            "forgetting": (just_after - at_end) / (target - floor),
            "share_just_after": _share(g, just_after), "share_at_end": _share(g, at_end),
        }
    earlier = games[:-1]  # the last game can't have been forgotten yet
    return {
        "games": games,
        "checkpoints": checkpoints,
        "exam_table": table,
        "per_game": per_game,
        "mean_forgetting": sum(per_game[g]["forgetting"] for g in earlier) / len(earlier) if earlier else 0.0,
        "games_at_target_just_after": sum(p["reached_target_just_after"] for p in per_game.values()),
        "games_at_target_at_end": sum(p["still_at_target_at_end"] for p in per_game.values()),
        "optimizer": settings["optimizer"],
        "budgets": budgets,
        "moves_practised": moves,
        "total_moves": total,
        "curve": curve,
        "clock_seconds_per_game": clock,
        "genes": genes,
    }


# ---------- outputs saved next to the run card ----------

def table_markdown(results: dict) -> str:
    games, cps = results["games"], results["checkpoints"]
    short = ["start"] + [c.removeprefix("after ") for c in cps[1:]]
    lines = ["Exam score after each game (rows: game scored; columns: brain after learning that game; "
             "**bold** = the game just learned)", "",
             "| Game | " + " | ".join(short) + " |", "|---|" + "---|" * len(short)]
    for i, g in enumerate(games):
        cells = []
        for j, v in enumerate(results["exam_table"][g]):
            cell = f"{v:.2f}"
            cells.append(f"**{cell}**" if j == i + 1 else cell)
        lines.append(f"| {g} | " + " | ".join(cells) + " |")
    lines += ["", "| Game | Target | Random | Just after | At end | Forgetting |", "|---|---|---|---|---|---|"]
    for g in games:
        p = results["per_game"][g]
        tick = lambda ok: " ✅" if ok else " ❌"  # noqa: E731
        lines.append(f"| {g} | {p['target']} | {p['random_floor']} | {p['just_after']:.2f}"
                     f"{tick(p['reached_target_just_after'])} | {p['at_end']:.2f}{tick(p['still_at_target_at_end'])}"
                     f" | {p['forgetting']:.0%} |")
    lines.append(f"\nMean forgetting over the earlier games: {results['mean_forgetting']:.0%}")
    return "\n".join(lines) + "\n"


def chart(results: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    games, curve = results["games"], results["curve"]
    xs = [s / 1e6 for s in curve["steps"]]
    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=120)
    colours = plt.get_cmap("tab10")
    bounds, total = [], 0
    for g in games:
        total += results["moves_practised"][g]
        bounds.append(total / 1e6)
    left = 0.0
    for i, (g, right) in enumerate(zip(games, bounds)):
        ax.axvspan(left, right, color=colours(i), alpha=0.07, lw=0)
        ax.text((left + right) / 2, 1.33, g, ha="center", va="top", fontsize=8, color=colours(i), rotation=0)
        left = right
    for i, g in enumerate(games):
        ys = [_share(g, v) for v in curve[g]]
        ax.plot(xs, ys, color=colours(i), lw=1.8, label=g)
    ax.axhline(1.0, color="0.3", lw=1, ls="--")
    ax.axhline(0.0, color="0.6", lw=1, ls=":")
    ax.text(xs[-1], 1.0, " target", va="center", fontsize=8, color="0.3")
    ax.text(xs[-1], 0.0, " random", va="center", fontsize=8, color="0.5")
    ax.set_xlim(0, xs[-1])
    ax.set_ylim(min(-0.5, min(_share(g, v) for g in games for v in curve[g]) - 0.05), 1.38)
    ax.set_xlabel("moves practised in the stream (millions)")
    ax.set_ylabel("exam score, as share of random → target")
    ax.set_title(f"WP 1.2 task stream: one plain brain, {len(games)} games in a row "
                 f"(mean forgetting {results['mean_forgetting']:.0%})")
    ax.legend(loc="lower left", fontsize=8, ncol=2, frameon=False)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def save_outputs(folder: Path, results: dict) -> None:
    (Path(folder) / TABLE_FILE).write_text(table_markdown(results), encoding="utf-8")
    chart(results, Path(folder) / CHART_FILE)
