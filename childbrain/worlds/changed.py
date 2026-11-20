"""'Changed' versions of any game, made automatically (the third kind, mixed games, is in mixed.py).

  look-changed   LookChanged: same rules, new look. The board is turned and/or mirrored, moved to another
                 spot in the 12x12 picture, and its layers swap "colours" (e.g. coins drawn where goals
                 usually are). The buttons turn with the board, so "up" still moves up on screen.
                 An old skill should still work after spotting what changed.
  rules-changed  RulesChanged: same look, quietly different rules.
                   "controls": the move buttons are shuffled (up might now go left).
                   "reward":   every score is flipped (what used to be good is now bad).
                 An old skill should stop fitting.

Both wrap a world and keep the shared format, so they pass the same format tests as every game.
"""

from __future__ import annotations

import random

import torch

from .base import MAX_SIZE, MOVES, N_ACTIONS, N_LAYERS, WALLS, StepResult


class Changed:
    """Forwards everything to the wrapped world."""

    kind = "changed"

    def __init__(self, world):
        self.world = world
        self.n, self.device, self.max_steps = world.n, world.device, world.max_steps
        self.size, self.split, self.seed, self.sealed = world.size, world.split, world.seed, world.sealed
        self.actions_used = world.actions_used
        self.name = f"{world.name}~{self.kind}"
        self.to_inner = torch.arange(N_ACTIONS, device=self.device)  # my button -> the game's button

    @property
    def area(self):
        return self.world.area

    @property
    def t(self):
        return self.world.t

    def observe(self):
        return self.world.observe()

    def allowed(self):
        return self.world.allowed()[:, self.to_inner]

    def reset(self, which=None):
        self.world.reset(which)
        return self.observe()

    def step(self, actions):
        res = self.world.step(self.to_inner[actions.to(self.device, torch.long)])
        return StepResult(self.observe(), res.reward, res.done, res.info)


def _turn(area: torch.Tensor, k: int, mirror: bool) -> torch.Tensor:
    area = torch.rot90(area, k, dims=(-2, -1))
    return area.flip(-1) if mirror else area


def _button_map(k: int, mirror: bool) -> list[int]:
    """For each on-screen move, the game's move that looks like it after turning/mirroring the board."""
    out = list(range(N_ACTIONS))
    for inner in range(4):
        grid = torch.zeros(3, 3)
        grid[1 + MOVES[inner, 0], 1 + MOVES[inner, 1]] = 1
        r, c = divmod(int(_turn(grid, k, mirror).argmax()), 3)
        seen = [i for i in range(4) if MOVES[i, 0] == r - 1 and MOVES[i, 1] == c - 1][0]
        out[seen] = inner
    return out


class LookChanged(Changed):
    kind = "look"

    def __init__(self, world, seed: int = 0):
        super().__init__(world)
        rng = random.Random(seed)
        r0, c0, s = world.area
        self.k, self.mirror = rng.randrange(4), rng.random() < 0.5
        self.offset = (rng.randrange(MAX_SIZE - s + 1), rng.randrange(MAX_SIZE - s + 1))
        perm = list(range(N_LAYERS))
        while perm == list(range(N_LAYERS)):
            rng.shuffle(perm)
        self.layers = torch.tensor(perm, device=self.device)  # new layer i shows old layer perm[i]
        self.to_inner = torch.tensor(_button_map(self.k, self.mirror), device=self.device)
        self.name = f"{world.name}~look{seed}"

    @property
    def area(self):
        return (*self.offset, self.size)

    def observe(self):
        s = self.size
        r, c = self.offset
        inner = self.world.observe()[:, :, :s, :s]
        out = torch.zeros(self.n, N_LAYERS, MAX_SIZE, MAX_SIZE, device=self.device)
        out[:, WALLS] = 1.0
        out[:, :, r:r + s, c:c + s] = _turn(inner, self.k, self.mirror)
        return out[:, self.layers]


class RulesChanged(Changed):
    def __init__(self, world, kind: str = "controls", seed: int = 0):
        if kind not in ("controls", "reward"):
            raise ValueError("kind must be 'controls' or 'reward'")
        self.kind = f"rules-{kind}"
        super().__init__(world)
        self.flip = kind == "reward"
        if kind == "controls":
            rng = random.Random(seed)
            moves = [a for a in range(4) if a in world.actions_used]
            if len(moves) < 2:
                raise ValueError(f"{world.name} has too few move buttons to shuffle")
            shuffled = list(moves)
            while shuffled == moves:
                rng.shuffle(shuffled)
            to_inner = list(range(N_ACTIONS))
            for a, b in zip(moves, shuffled):
                to_inner[a] = b
            self.to_inner = torch.tensor(to_inner, device=self.device)

    def step(self, actions):
        res = super().step(actions)
        if not self.flip:
            return res
        info = dict(res.info, episode_return=-res.info["episode_return"])
        return StepResult(res.obs, -res.reward, res.done, info)
