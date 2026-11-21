"""WP 1.1: one plain brain practises ONE game alone, until it reaches the game's target score.

    python -m childbrain run learn-alone --device cuda --set game=snake

Targets were fixed before any training (2026-11-20), between the random player's floor and a hand-written
expert, so "reached the target" means "clearly learned the game", not "perfect":

Progress checks use fresh PRACTICE worlds (split="train"), so stopping early never peeks at the exam.
The final score is on the EXAM worlds (split="test", fixed seed; for Maze that means mazes it never saw),
played the way the guard's exam plays: the brain's own best button every step ("greedy").
"""

from __future__ import annotations

import hashlib
import json
import time

import torch

from ..brain.net import build, weights_to_json
from ..learn.ppo import Learner, Settings, by_chance, greedy
from ..runcard import experiment, paused_seconds, report_progress, stop_requested
from ..worlds import PRACTICE, make, play_once, random_actions

# game -> (target, random floor from WP 0.4, best possible or expert)
TARGETS = {
    "snake": (5.0, -0.88, "greedy player +17"),
    "maze": (0.70, 0.16, "1.0 (always reach the goal)"),
    "catch": (8.0, -7.5, "+10 (catch all 10)"),
    "push-box": (0.70, 0.08, "1.0"),
    "avoid-enemy": (0.70, -0.92, "+1.0 (never caught)"),
    "collect-coins": (5.5, 2.6, "6"),
    "colour-match": (2.5, 0.03, "3"),
    "connect-4": (0.70, 0.28, "+1.0 (always beat the random player)"),
    "maze-coins": (1.2, 0.28, "1.8 (4 coins + goal)"),
}


# Settings that reached each target alone (WP 1.1, 2026-11-21; see docs/child-brain-dev-plan.md "WP 1.1 result").
# Pass them with --set. The two mazes never reached their targets; their best tries are listed instead.
PASSED_WITH = {
    "catch": {"gamma": 0.9},
    "snake": {},
    "collect-coins": {},
    "colour-match": {},
    "push-box": {},
    "connect-4": {},
    "avoid-enemy": {"gamma": 0.9, "max_steps": 20_000_000, "stop_at_target": False},
}
BEST_TRY = {
    "maze": {"layers": 2, "channels": 64, "max_steps": 60_000_000},        # 64.5% of 70%
    "maze-coins": {"layers": 2, "channels": 64, "max_steps": 30_000_000},  # 0.85 of 1.2
}


def _seed() -> int:
    return int(torch.randint(0, 2**62, (1,)).item())  # from the run card's seed


def score(brain, game: str, device: str, copies: int, split: str, how: str = "greedy") -> float:
    kw = {"split": split} if split == "test" else {"split": split, "seed": _seed()}
    world = make(game, n=copies, device=device, **kw)
    if how == "greedy":
        policy = greedy(brain)
    elif how == "chance":
        gen = torch.Generator(device=world.device)
        gen.manual_seed(_seed())
        policy = by_chance(brain, gen)
    else:
        gen = torch.Generator(device=world.device)
        gen.manual_seed(_seed())
        policy = lambda obs, allowed: random_actions(allowed, gen)  # noqa: E731
    brain.eval()
    out = play_once(world, policy).mean().item()
    brain.train()
    return out


@experiment(defaults={
    "game": "snake", "copies": 1024, "max_steps": 30_000_000, "hidden": 256, "layers": 1,
    "eyes": "grid", "channels": 32, "anneal": True, "horizon": 64, "lr": 3e-4, "entropy": 0.01, "epochs": 4, "minibatches": 8, "gamma": 0.99,
    "check_every": 10, "check_copies": 1024, "stop_at_target": True, "keep_weights": True,
})
def learn_alone(settings: dict, device: str) -> dict:
    game = settings["game"]
    if game not in PRACTICE:
        raise ValueError(f"{game!r} isn't a practice game; choose from {sorted(PRACTICE)}")
    target = TARGETS[game][0]
    genes = {"kind": "plain", "hidden": settings["hidden"], "layers": settings["layers"],
             "eyes": settings["eyes"], "channels": settings["channels"]}
    brain = build(genes).to(device)
    world = make(game, n=settings["copies"], device=device, seed=_seed())
    s = Settings(horizon=settings["horizon"], lr=settings["lr"], entropy=settings["entropy"],
                 epochs=settings["epochs"], minibatches=settings["minibatches"], gamma=settings["gamma"], anneal=settings["anneal"])
    rounds = max(1, settings["max_steps"] // (settings["horizon"] * settings["copies"]))
    learner = Learner(brain, world, s, total_rounds=rounds)

    checks = {"steps": [], "score": []}
    reached_at = None
    stopped = None
    start = time.perf_counter()

    def live(phase: str) -> None:
        p = learner.progress
        report_progress(phase=phase, game=game, target=target, random_floor=TARGETS[game][1],
                        steps=learner.steps, max_steps=rounds * settings["horizon"] * settings["copies"],
                        round=len(p.steps), rounds=rounds, seconds=round(time.perf_counter() - start - paused_seconds(), 1),
                        checks_steps=checks["steps"], checks_score=checks["score"], reached_at=reached_at,
                        practice_steps=p.steps[-400:], practice_score=p.score[-400:])

    live("practising")
    for r in range(rounds):
        learner.round()
        if (r + 1) % settings["check_every"] == 0 or r == rounds - 1:
            now = score(brain, game, device, settings["check_copies"], "train")
            checks["steps"].append(learner.steps)
            checks["score"].append(now)
            if now >= target and reached_at is None:
                reached_at = learner.steps
                if settings["stop_at_target"]:
                    break
        if r % 5 == 4:
            live("practising")
        stopped = stop_requested()
        if stopped:
            break
    seconds = time.perf_counter() - start - paused_seconds()   # time spent waiting for Nova isn't practice
    live("final exam")

    exam = score(brain, game, device, 1024, "test", "greedy")
    exam_chance = score(brain, game, device, 1024, "test", "chance")
    weights = weights_to_json(brain)
    fingerprint = hashlib.sha256(json.dumps(weights, sort_keys=True).encode()).hexdigest()
    out = {
        "game": game,
        "target": target,
        "random_floor": TARGETS[game][1],
        "reached_target_in_practice_at_step": reached_at,
        "steps_practised": learner.steps,
        "clock_seconds": round(seconds, 1),  # wall-clock: not compared on rerun
        "exam_score_greedy": exam,
        "exam_score_by_chance": exam_chance,
        "passed": exam >= target,
        "checks_steps": checks["steps"],
        "checks_score": checks["score"],
        "practice_steps": learner.progress.steps,
        "practice_score": learner.progress.score,
        "practice_sureness": learner.progress.sureness,
        "genes": genes,
        "weights_sha256": fingerprint,
    }
    if stopped:
        out["stopped_early"] = stopped  # only when it happened, so older runs still rerun to the same numbers
    if settings["keep_weights"]:
        out["weights"] = weights
    return out
