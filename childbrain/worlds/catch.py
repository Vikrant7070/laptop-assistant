"""Catch: things fall from the top; move along the bottom row to catch them.

+1 for a catch, -1 for a miss. The episode ends after `drops` things have fallen.
Layers: me = the catcher, items = the falling thing.
"""

import torch

from .base import ITEMS, LEFT, ME, RIGHT, STAY, World


class Catch(World):
    name = "catch"
    actions_used = (LEFT, RIGHT, STAY)
    default_size = 8
    default_max_steps = 200

    def __init__(self, *, drops: int = 10, **kwargs):
        self.drops = drops
        super().__init__(**kwargs)

    def _setup(self):
        n, dev = self.n, self.device
        self.me = torch.zeros(n, dtype=torch.long, device=dev)
        self.thing = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.fallen = torch.zeros(n, dtype=torch.long, device=dev)

    def _new_thing(self, which):
        col = torch.randint(0, self.size, (self.n,), generator=self.gen, device=self.device)
        fresh = torch.stack([torch.zeros_like(col), col], dim=1)
        self.thing = torch.where(which[:, None], fresh, self.thing)

    def _reset(self, which):
        col = torch.randint(0, self.size, (self.n,), generator=self.gen, device=self.device)
        self.me = torch.where(which, col, self.me)
        self.fallen = torch.where(which, 0, self.fallen)
        self._new_thing(which)

    def _step(self, actions):
        self.me = (self.me + self.moves[actions, 1]).clamp(0, self.size - 1)
        self.thing = self.thing + torch.tensor([1, 0], device=self.device)
        landed = self.thing[:, 0] == self.size - 1
        caught = landed & (self.thing[:, 1] == self.me)
        reward = caught.float() - (landed & ~caught).float()
        self.fallen = self.fallen + landed.long()
        self._new_thing(landed)
        return reward, self.fallen >= self.drops

    def _draw(self, obs):
        bottom = torch.full_like(self.me, self.size - 1)
        self.mark(obs, ME, torch.stack([bottom, self.me], dim=1))
        self.mark(obs, ITEMS, self.thing)
