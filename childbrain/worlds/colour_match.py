"""Colour-match: there are tiles of two colours; collect only the wanted colour.

+1 for a wanted tile, -1 for a wrong one (the tile is used up either way). The episode ends when no
wanted tiles are left. Which colour is wanted changes every episode and is shown by the goal layer:
goal layer lit = colour A (items layer) is wanted, goal layer dark = colour B (extra layer) is wanted.
"""

import torch

from .base import EXTRA, GOAL, ITEMS, ME, World


class ColourMatch(World):
    name = "colour-match"
    default_size = 8
    default_max_steps = 100

    def __init__(self, *, tiles: int = 3, **kwargs):
        self.k = tiles
        super().__init__(**kwargs)

    def _setup(self):
        n, s, dev = self.n, self.size, self.device
        self.me = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.a = torch.zeros(n, s, s, dtype=torch.bool, device=dev)
        self.b = torch.zeros(n, s, s, dtype=torch.bool, device=dev)
        self.want_a = torch.zeros(n, dtype=torch.bool, device=dev)

    def _reset(self, which):
        s, n, k = self.size, self.n, self.k
        cells = self.pick_cells(torch.ones(n, s, s, dtype=torch.bool, device=self.device), 2 * k + 1)
        rows = self.rows[:, None].expand(-1, k)
        a = torch.zeros(n, s, s, dtype=torch.bool, device=self.device)
        b = torch.zeros_like(a)
        a[rows, cells[:, 1:k + 1, 0], cells[:, 1:k + 1, 1]] = True
        b[rows, cells[:, k + 1:, 0], cells[:, k + 1:, 1]] = True
        want = torch.rand(n, generator=self.gen, device=self.device) < 0.5
        w = which[:, None, None]
        self.me = torch.where(which[:, None], cells[:, 0], self.me)
        self.a, self.b = torch.where(w, a, self.a), torch.where(w, b, self.b)
        self.want_a = torch.where(which, want, self.want_a)

    def _step(self, actions):
        self.me = (self.me + self.moves[actions]).clamp(0, self.size - 1)
        r, c = self.me[:, 0], self.me[:, 1]
        on_a, on_b = self.a[self.rows, r, c], self.b[self.rows, r, c]
        self.a[self.rows, r, c] = False
        self.b[self.rows, r, c] = False
        good = torch.where(self.want_a, on_a, on_b)
        bad = torch.where(self.want_a, on_b, on_a)
        left = torch.where(self.want_a, self.a.flatten(1).any(1), self.b.flatten(1).any(1))
        return good.float() - bad.float(), ~left

    def _draw(self, obs):
        s = self.size
        self.mark(obs, ME, self.me)
        obs[:, ITEMS, :s, :s] = self.a.float()
        obs[:, EXTRA, :s, :s] = self.b.float()
        obs[:, GOAL, :s, :s] = self.want_a.float()[:, None, None]
