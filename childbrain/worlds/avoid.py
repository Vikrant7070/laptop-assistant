"""Avoid-the-enemy: stay away from the enemies for as long as you can.

Each step alive earns 1/max_steps (so lasting the whole episode = +1); being caught = -1 and ends it.
Enemies step toward you half the time and wander otherwise.
Layers: me, enemies.
"""

import torch

from .base import ENEMIES, ME, World
from .enemies import move_enemies


class Avoid(World):
    name = "avoid-enemy"
    default_size = 8
    default_max_steps = 100

    def __init__(self, *, enemies: int = 2, chase: float = 0.5, **kwargs):
        self.k, self.chase = enemies, chase
        super().__init__(**kwargs)

    def _setup(self):
        n, dev = self.n, self.device
        self.me = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.enemies = torch.zeros(n, self.k, 2, dtype=torch.long, device=dev)
        r = torch.arange(self.size, device=dev)
        self.grid = torch.stack(torch.meshgrid(r, r, indexing="ij"), dim=-1)  # (s, s, 2)

    def _reset(self, which):
        me = self.rand_cells(self.size)
        far = (self.grid[None] - me[:, None, None]).abs().sum(-1) >= 3     # enemies start 3+ steps away
        enemies = self.pick_cells(far, self.k)
        self.me = torch.where(which[:, None], me, self.me)
        self.enemies = torch.where(which[:, None, None], enemies, self.enemies)

    def _step(self, actions):
        self.me = (self.me + self.moves[actions]).clamp(0, self.size - 1)
        caught = self.same_cell(self.me, self.enemies).any(1)
        self.enemies = move_enemies(self, self.enemies, self.me, None, self.chase)
        caught |= self.same_cell(self.me, self.enemies).any(1)
        reward = torch.where(caught, -1.0, 1.0 / self.max_steps)
        return reward, caught

    def _draw(self, obs):
        self.mark(obs, ME, self.me)
        self.mark(obs, ENEMIES, self.enemies)
