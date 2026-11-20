"""Maze: find the goal (+1). Walking into a wall just doesn't move you. The reward is rare on purpose.

Mazes come from two fixed banks made once from fixed seeds: a practice bank ("train") and an exam bank
("test"), so exam mazes are never seen during practice. Each bank also holds every rotation and mirror
image of its mazes. Start and goal are picked at random on every reset.

Layers: walls, me, goal.
"""

from functools import lru_cache
import random

import torch

from .base import DOWN, GOAL, LEFT, ME, RIGHT, STAY, UP, WALLS, World

BANK_SEEDS = {"train": 1, "test": 2}
BANK_MAZES = 1024  # per bank, before rotations and mirror images (x8)


def carve(cells: int, rng: random.Random) -> list[list[bool]]:
    """One 'perfect' maze (exactly one path between any two spots), by depth-first carving.
    Returns walls[row][col] for a (2*cells+1)-wide grid."""
    size = 2 * cells + 1
    walls = [[True] * size for _ in range(size)]
    seen = [[False] * cells for _ in range(cells)]
    stack = [(rng.randrange(cells), rng.randrange(cells))]
    seen[stack[0][0]][stack[0][1]] = True
    walls[2 * stack[0][0] + 1][2 * stack[0][1] + 1] = False
    while stack:
        r, c = stack[-1]
        nxt = [(r + dr, c + dc) for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))
               if 0 <= r + dr < cells and 0 <= c + dc < cells and not seen[r + dr][c + dc]]
        if not nxt:
            stack.pop()
            continue
        nr, nc = rng.choice(nxt)
        seen[nr][nc] = True
        walls[2 * nr + 1][2 * nc + 1] = False
        walls[r + nr + 1][c + nc + 1] = False  # the wall between the two cells
        stack.append((nr, nc))
    return walls


def _shapes(m: torch.Tensor) -> list[torch.Tensor]:
    """A maze's 4 rotations and their mirror images."""
    turns = [torch.rot90(m, k, dims=(-2, -1)) for k in range(4)]
    return turns + [t.flip(-1) for t in turns]


def _key(m: torch.Tensor) -> bytes:
    """The same fingerprint for a maze and all its rotations / mirror images."""
    return min(bytes(t.flatten().to(torch.uint8).tolist()) for t in _shapes(m))


@lru_cache(maxsize=8)
def _bank(size: int, split: str) -> tuple[torch.Tensor, frozenset]:
    if size % 2 == 0 or size < 11:
        raise ValueError("maze size must be odd and at least 11")
    # Depth-first carving often makes one long corridor, so small mazes repeat by chance.
    # Skip repeats, and never let a practice maze (in any rotation) into the exam bank.
    banned = _bank(size, "train")[1] if split == "test" else frozenset()
    rng = random.Random(BANK_SEEDS[split] * 1000 + size)
    mazes, keys = [], set()
    for _ in range(BANK_MAZES * 50):
        m = torch.tensor(carve((size - 1) // 2, rng))
        k = _key(m)
        if k in keys or k in banned:
            continue
        mazes.append(m)
        keys.add(k)
        if len(mazes) == BANK_MAZES:
            break
    else:
        raise RuntimeError(f"couldn't find {BANK_MAZES} different {size}x{size} mazes")
    return torch.cat(_shapes(torch.stack(mazes))), frozenset(keys)


def maze_bank(size: int, split: str) -> torch.Tensor:
    """(BANK_MAZES * 8, size, size) bool walls, always the same for a given size and split."""
    return _bank(size, split)[0]


class Maze(World):
    name = "maze"
    actions_used = (UP, DOWN, LEFT, RIGHT, STAY)
    default_size = 11
    sizes = (11,)  # 9x9 and smaller have too few different layouts for two banks of 1,024
    default_max_steps = 100

    def _setup(self):
        n, s, dev = self.n, self.size, self.device
        self.bank = maze_bank(s, self.split).to(dev)
        self.cells = (s - 1) // 2
        self.walls = torch.zeros(n, s, s, dtype=torch.bool, device=dev)
        self.pos = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.goal = torch.zeros(n, 2, dtype=torch.long, device=dev)

    def _reset(self, which):
        pick = torch.randint(0, self.bank.shape[0], (self.n,), generator=self.gen, device=self.device)
        self.walls = torch.where(which[:, None, None], self.bank[pick], self.walls)
        k = self.cells * self.cells
        start = torch.randint(0, k, (self.n,), generator=self.gen, device=self.device)
        goal = (start + 1 + torch.randint(0, k - 1, (self.n,), generator=self.gen, device=self.device)) % k
        to_cell = lambda i: torch.stack([2 * (i // self.cells) + 1, 2 * (i % self.cells) + 1], dim=1)  # noqa: E731
        self.pos = torch.where(which[:, None], to_cell(start), self.pos)
        self.goal = torch.where(which[:, None], to_cell(goal), self.goal)

    def _step(self, actions):
        new = (self.pos + self.moves[actions]).clamp(0, self.size - 1)
        blocked = self.walls[self.rows, new[:, 0], new[:, 1]]
        self.pos = torch.where(blocked[:, None], self.pos, new)
        reached = (self.pos == self.goal).all(dim=1)
        return reached.float(), reached

    def _draw(self, obs):
        s = self.size
        obs[:, WALLS, :s, :s] = self.walls.float()
        self.mark(obs, ME, self.pos)
        self.mark(obs, GOAL, self.goal)
