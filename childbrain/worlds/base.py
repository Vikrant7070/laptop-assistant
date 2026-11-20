"""The shared game format: one brain can play every game.

Every game is a batch of `n` copies that run together on one device (CPU or GPU).

  observe()  -> float tensor (n, 6, 12, 12), values 0..1. The six layers are LAYERS.
                The playing area sits in the top-left corner; everything outside it is marked as wall.
  allowed()  -> bool tensor (n, 6): which ACTIONS make sense right now. A move that isn't allowed is
                treated as "stay".
  step(a)    -> StepResult(obs, reward, done, info). `a` is a long tensor (n,) of action numbers.
                A copy whose episode ended is reset at once, so `obs` is already its next start.
                info["episode_return"] is the finished episode's total score where done is True.

Every episode has a step limit, so no game can be played (or gamed) forever. A world built with the
same seed always plays out the same way; `split="test"` worlds are for exams, never for practice.
"""

from __future__ import annotations

from typing import NamedTuple

import torch

LAYERS = ("walls", "me", "items", "enemies", "goal", "extra")
ACTIONS = ("up", "down", "left", "right", "stay", "use")
N_LAYERS, N_ACTIONS = len(LAYERS), len(ACTIONS)
WALLS, ME, ITEMS, ENEMIES, GOAL, EXTRA = range(N_LAYERS)
UP, DOWN, LEFT, RIGHT, STAY, USE = range(N_ACTIONS)
MAX_SIZE = 12

# How each action moves (row, column).
MOVES = torch.tensor([[-1, 0], [1, 0], [0, -1], [0, 1], [0, 0], [0, 0]])

TEST_SEED = 20261120  # fixed seed for exam worlds: same layouts every time


class StepResult(NamedTuple):
    obs: torch.Tensor
    reward: torch.Tensor
    done: torch.Tensor
    info: dict


class World:
    """Base class. A game fills in _reset, _step, _draw and (if needed) _allowed."""

    name = "world"
    actions_used = (UP, DOWN, LEFT, RIGHT, STAY)  # the game's actions; others are never allowed
    default_size = 8
    sizes: tuple = tuple(range(5, MAX_SIZE + 1))  # the board sizes this game can be played at
    default_max_steps = 200
    sealed = False  # sealed games are exam-only: they can't be built for practice
    one_me = True  # exactly one "me" cell (False for a paddle or a board of discs)

    def __init__(self, *, n: int = 1024, size: int | None = None, device: str = "cpu",
                 max_steps: int | None = None, seed: int | None = None, split: str = "train"):
        self.size = size or self.default_size
        if self.size not in self.sizes:
            raise ValueError(f"{self.name} can be size {self.sizes}, not {self.size}")
        if split not in ("train", "test"):
            raise ValueError("split must be 'train' or 'test'")
        if self.sealed and split != "test":
            raise PermissionError(f"{self.name} is a sealed exam game: build it with split='test' only")
        self.n, self.split = n, split
        self.device = torch.device(device)
        self.max_steps = max_steps or self.default_max_steps
        if seed is None:
            seed = TEST_SEED if split == "test" else int(torch.randint(0, 2**62, (1,)).item())
        self.seed = seed
        self.gen = torch.Generator(device=self.device)
        self.gen.manual_seed(seed)

        self.rows = torch.arange(n, device=self.device)
        self.moves = MOVES.to(self.device)
        self.t = torch.zeros(n, dtype=torch.long, device=self.device)
        self.ep_return = torch.zeros(n, device=self.device)
        base = torch.zeros(N_ACTIONS, dtype=torch.bool)
        base[list(self.actions_used)] = True
        self.base_allowed = base.to(self.device)
        frame = torch.zeros(N_LAYERS, MAX_SIZE, MAX_SIZE, device=self.device)
        frame[WALLS] = 1.0
        frame[WALLS, : self.size, : self.size] = 0.0  # inside the playing area: no wall unless the game draws one
        self.frame = frame
        self._setup()
        self.reset()

    # ----- the public format -----

    @property
    def area(self) -> tuple[int, int, int]:
        """Where the playing area sits in the 12x12 picture: (top row, left column, size)."""
        return 0, 0, self.size

    def reset(self, which: torch.Tensor | None = None) -> torch.Tensor:
        if which is None:
            which = torch.ones(self.n, dtype=torch.bool, device=self.device)
        self._reset(which)
        self.t = torch.where(which, 0, self.t)
        self.ep_return = torch.where(which, 0.0, self.ep_return)
        return self.observe()

    def observe(self) -> torch.Tensor:
        obs = self.frame.expand(self.n, -1, -1, -1).clone()
        self._draw(obs)
        return obs

    def allowed(self) -> torch.Tensor:
        return self._allowed(self.base_allowed.expand(self.n, -1).clone())

    def step(self, actions: torch.Tensor) -> StepResult:
        actions = actions.to(self.device, torch.long)
        ok = self.allowed().gather(1, actions[:, None]).squeeze(1)
        actions = torch.where(ok, actions, STAY)
        reward, ended = self._step(actions)
        self.t += 1
        self.ep_return += reward
        timeout = (self.t >= self.max_steps) & ~ended
        done = ended | timeout
        info = {"episode_return": self.ep_return.clone(), "timeout": timeout, "length": self.t.clone()}
        self.reset(done)  # no `if done.any()`: that would make the CPU wait for the GPU every step
        return StepResult(self.observe(), reward, done, info)

    # ----- filled in by each game -----

    def _setup(self) -> None:
        """Make the game's state tensors (called once, before the first reset)."""

    def _reset(self, which: torch.Tensor) -> None:
        raise NotImplementedError

    def _step(self, actions: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Apply the actions. Return (reward, ended) — ended = the game itself finished (not the step limit)."""
        raise NotImplementedError

    def _draw(self, obs: torch.Tensor) -> None:
        raise NotImplementedError

    def _allowed(self, allowed: torch.Tensor) -> torch.Tensor:
        return allowed

    # ----- helpers for games -----

    def rand_cells(self, k: int) -> torch.Tensor:
        """(n, 2) random cells inside a k x k area."""
        return torch.randint(0, k, (self.n, 2), generator=self.gen, device=self.device)

    def mark(self, obs: torch.Tensor, layer: int, cells: torch.Tensor, show: torch.Tensor | None = None) -> None:
        """Set cells on a layer. `cells` is (n, 2) or (n, k, 2); with `show`, only where it's True.
        Cells that aren't shown keep what was there (so things can't hide other things)."""
        if cells.dim() == 2:
            cells = cells[:, None]
            show = None if show is None else show[:, None]
        rows = self.rows[:, None].expand(-1, cells.shape[1])
        old = obs[rows, layer, cells[..., 0], cells[..., 1]]
        new = torch.ones_like(old) if show is None else torch.where(show, 1.0, old)
        obs[rows, layer, cells[..., 0], cells[..., 1]] = new

    def pick_cells(self, free: torch.Tensor, k: int) -> torch.Tensor:
        """(n, k, 2) different random cells per copy, chosen among `free` (n, s, s) cells."""
        s = free.shape[-1]
        score = torch.rand(free.shape, generator=self.gen, device=self.device)
        score = torch.where(free, score, -1.0)
        idx = score.flatten(1).topk(k, dim=1).indices
        return torch.stack([idx // s, idx % s], dim=2)

    def same_cell(self, a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
        """Is cell a == cell b? Works for (n, 2) against (n, k, 2) too."""
        if a.dim() < b.dim():
            a = a[:, None]
        return (a == b).all(dim=-1)


def random_actions(allowed: torch.Tensor, gen: torch.Generator) -> torch.Tensor:
    """A random player: picks uniformly among the allowed actions."""
    noise = torch.rand(allowed.shape, generator=gen, device=allowed.device)
    return torch.where(allowed, noise, -1.0).argmax(dim=1)


def play_once(world: World, policy, reset: bool = True) -> torch.Tensor:
    """Play until every copy has finished one episode, and return each copy's score.

    `policy(obs, allowed) -> actions`. Never runs longer than the step limit."""
    obs = world.reset() if reset else world.observe()
    scores = torch.zeros(world.n, device=world.device)
    finished = torch.zeros(world.n, dtype=torch.bool, device=world.device)
    for _ in range(world.max_steps):
        res = world.step(policy(obs, world.allowed()))
        first = res.done & ~finished
        scores = torch.where(first, res.info["episode_return"], scores)
        finished |= res.done
        obs = res.obs
    return scores
