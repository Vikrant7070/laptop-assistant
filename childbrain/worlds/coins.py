"""Collect-coins: walk over coins to collect them (+1 each). The episode ends when all are collected.

Layers: me, items = coins.
"""

import torch

from .base import ITEMS, ME, World


class Coins(World):
    name = "collect-coins"
    default_size = 8
    default_max_steps = 100

    def __init__(self, *, coins: int = 6, **kwargs):
        self.k = coins
        super().__init__(**kwargs)

    def _setup(self):
        n, s, dev = self.n, self.size, self.device
        self.me = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.coins = torch.zeros(n, s, s, dtype=torch.bool, device=dev)

    def _reset(self, which):
        s, n = self.size, self.n
        cells = self.pick_cells(torch.ones(n, s, s, dtype=torch.bool, device=self.device), self.k + 1)
        coins = torch.zeros(n, s, s, dtype=torch.bool, device=self.device)
        rows = self.rows[:, None].expand(-1, self.k)
        coins[rows, cells[:, 1:, 0], cells[:, 1:, 1]] = True
        self.me = torch.where(which[:, None], cells[:, 0], self.me)
        self.coins = torch.where(which[:, None, None], coins, self.coins)

    def _step(self, actions):
        self.me = (self.me + self.moves[actions]).clamp(0, self.size - 1)
        got = self.coins[self.rows, self.me[:, 0], self.me[:, 1]]
        self.coins[self.rows, self.me[:, 0], self.me[:, 1]] = False
        done = ~self.coins.flatten(1).any(1)
        return got.float(), done

    def _draw(self, obs):
        s = self.size
        self.mark(obs, ME, self.me)
        obs[:, ITEMS, :s, :s] = self.coins.float()
