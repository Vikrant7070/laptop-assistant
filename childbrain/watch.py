"""Watch a trained brain play: record one game, move by move, with what goes on inside the brain.

    python -m childbrain watch childbrain/runs/<run folder> [--game snake] [--board exam|new] [--seed 0]
                               [--steps 300] [--out file.json]

The brain is a saved one: a run that kept its weights (results.json "genes" + "weights"), or a guard
version (runs/versions/NNNN/genes.json + weights.json). Weights are JSON numbers, so loading them never runs
code, and nothing here can change them: the brain only plays, it doesn't learn. Practice games only (the
sealed exam games stay sealed). Runs on the CPU with one copy of the game, so it never needs the graphics card.

Every move records:
  board     the picture the brain is given (6 layers, 12x12; only non-zero squares are stored)
  allowed   which buttons make sense now
  chance    how much the brain likes each button (0..1, the allowed ones add up to 1)
  pick      the button it presses: its favourite, the way the exam plays
  guess     its own guess of the score still to come (the "critic"), if it has one
  neurons   its last hidden layer (-1..1), the "thought" the buttons and the guess are read from
  focus     which squares changed its choice most (0..1): how much the chosen button's score moves when
            that square of the picture changes a little (the gradient), added over the six layers
  reward    the score that move earned; done = the game ended (the next move is a new game)

The Child brain tab in Nova plays the file back.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import torch

from .brain.net import PlainBrain, from_json
from .worlds import ACTIONS, LAYERS, MAX_SIZE, PRACTICE, make

FORMAT = 1


def load_brain(folder: Path) -> tuple[dict, dict, dict]:
    """(genes, weights, about) from a run folder or a guard version folder."""
    folder = Path(folder)
    if (folder / "genes.json").exists():
        genes = json.loads((folder / "genes.json").read_text(encoding="utf-8"))
        weights = json.loads((folder / "weights.json").read_text(encoding="utf-8"))
        return genes, weights, {"kind": "version", "id": folder.name, "game": None}
    results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    if "weights" not in results or "genes" not in results:
        raise ValueError(f"{folder.name} didn't keep its weights (keep_weights was off), so it can't be watched")
    card = json.loads((folder / "card.json").read_text(encoding="utf-8"))
    return results["genes"], results["weights"], {
        "kind": "run", "id": folder.name, "name": card.get("name"), "game": results.get("game"),
        "exam": results.get("exam_score_greedy"), "target": results.get("target"), "passed": results.get("passed")}


def _thought(net, obs: torch.Tensor):
    """(button scores, guess or None, last hidden layer or None) in one pass."""
    if isinstance(net, PlainBrain):
        h = net.body(obs)
        return net.buttons(h), net.critic(h).squeeze(-1), h
    if isinstance(net, torch.nn.Sequential) and len(net) == 4:  # mlp: Flatten, Linear, ReLU, Linear
        h = net[:3](obs)
        return net[3](h), None, h
    return net(obs), None, None


def _sparse(obs: torch.Tensor) -> list:
    """[[layer, row, col, value], ...] for the squares that aren't 0 (walls outside the area left out)."""
    return [[int(k), int(r), int(c), round(float(obs[k, r, c]), 3)] for k, r, c in obs.nonzero().tolist()]


def record(folder: Path, game: str | None = None, board: str = "exam", seed: int = 0, steps: int = 300) -> dict:
    genes, weights, about = load_brain(folder)
    game = game or about.get("game") or "snake"
    if game not in PRACTICE:
        raise ValueError(f"{game!r} isn't a practice game; choose from {sorted(PRACTICE)}")
    if board not in ("exam", "new"):
        raise ValueError("board must be 'exam' (the exam layouts) or 'new' (a fresh practice layout)")
    steps = max(1, min(int(steps), 2000))
    torch.manual_seed(seed)
    net = from_json(genes, weights).eval()
    world = make(game, n=1, device="cpu", **({"split": "test"} if board == "exam" else {"seed": seed}))
    top, left, size = world.area
    obs = world.observe()
    moves, score, games = [], 0.0, 1
    start = time.perf_counter()
    for t in range(steps):
        allowed = world.allowed()[0]
        x = obs.clone().requires_grad_(True)
        scores, guess, hidden = _thought(net, x)
        masked = torch.where(allowed, scores[0], -torch.inf)
        chance = torch.softmax(masked, 0)
        pick = int(masked.argmax())
        net.zero_grad()
        scores[0, pick].backward()
        focus = x.grad[0].abs().sum(0)[top:top + size, left:left + size]
        focus = focus / focus.max() if float(focus.max()) > 0 else focus
        res = world.step(torch.tensor([pick]))
        reward, done = float(res.reward[0]), bool(res.done[0])
        score += reward
        moves.append({
            "board": _sparse(obs[0].detach()),
            "allowed": [bool(a) for a in allowed.tolist()],
            "chance": [round(float(c), 4) for c in chance.tolist()],
            "pick": pick,
            "guess": None if guess is None else round(float(guess[0].detach()), 4),
            "neurons": None if hidden is None else [round(float(v), 3) for v in hidden[0].detach().tolist()],
            "focus": [[round(float(v), 3) for v in row] for row in focus.tolist()],
            "reward": round(reward, 4),
            "done": done,
            "game_score": round(float(res.info["episode_return"][0]), 4) if done else None,
        })
        if done and t < steps - 1:
            games += 1
        obs = res.obs
    return {
        "format": FORMAT, "brain": about, "genes": genes, "game": game, "board": board, "seed": seed,
        "area": [top, left, size], "grid": MAX_SIZE, "layers": list(LAYERS), "buttons": list(ACTIONS),
        "moves": moves, "total_reward": round(score, 4), "games": games,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "clock_seconds": round(time.perf_counter() - start, 2),
    }


def main(folder: str, game: str | None, board: str, seed: int, steps: int, out: str | None) -> Path:
    data = record(Path(folder), game, board, seed, steps)
    path = Path(out) if out else Path(folder) / "watch" / f"{data['game']}_{board}_{seed}_{steps}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return path
