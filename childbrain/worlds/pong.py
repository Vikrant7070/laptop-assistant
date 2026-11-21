"""Pong-lite (SEALED exam game): keep the bouncing ball in play with a 3-wide paddle on the bottom row.

+1 each time the paddle sends the ball back up, -1 for a miss (which ends the episode).
The ball moves one step diagonally each turn and bounces off the side and top edges.
Layers: me = paddle, items = ball, extra = where the ball was a step ago (shows its direction).
"""

import torch

from .base import EXTRA, ITEMS, LEFT, ME, RIGHT, STAY, World


class Pong(World):
    name = "pong-lite"
    sealed = True
    one_me = False
    actions_used = (LEFT, RIGHT, STAY)
    default_size = 10
    sizes = tuple(range(6, 13))
    default_max_steps = 200

    def _setup(self):
        n, dev = self.n, self.device
        self.paddle = torch.zeros(n, dtype=torch.long, device=dev)  # centre column
        self.ball = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.prev = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.vel = torch.zeros(n, 2, dtype=torch.long, device=dev)

    def _reset(self, which):
        s, n = self.size, self.n
        paddle = torch.randint(1, s - 1, (n,), generator=self.gen, device=self.device)
        # The ball starts in the top row, so the paddle always has time to get there (no lost-at-start games).
        col = torch.randint(0, s, (n,), generator=self.gen, device=self.device)
        ball = torch.stack([torch.zeros_like(col), col], dim=1)
        side = torch.randint(0, 2, (n,), generator=self.gen, device=self.device) * 2 - 1
        vel = torch.stack([torch.ones_like(side), side], dim=1)  # always starts going down
        w = which[:, None]
        self.paddle = torch.where(which, paddle, self.paddle)
        self.ball = torch.where(w, ball, self.ball)
        self.prev = torch.where(w, ball, self.prev)
        self.vel = torch.where(w, vel, self.vel)

    def _step(self, actions):
        s = self.size
        self.paddle = (self.paddle + self.moves[actions, 1]).clamp(1, s - 2)
        vr, vc = self.vel[:, 0], self.vel[:, 1]
        r, c = self.ball[:, 0], self.ball[:, 1]
        vc = torch.where((c + vc < 0) | (c + vc >= s), -vc, vc)
        vr = torch.where(r + vr < 0, 1, vr)
        at_paddle_row = (r + vr == s - 1) & (vr == 1)
        hit = at_paddle_row & ((c + vc - self.paddle).abs() <= 1)
        miss = at_paddle_row & ~hit
        vr = torch.where(hit, -1, vr)
        self.prev = self.ball
        self.ball = torch.stack([r + vr, c + vc], dim=1).clamp(0, s - 1)
        self.vel = torch.stack([vr, vc], dim=1)
        return hit.float() - miss.float(), miss

    def _draw(self, obs):
        s = self.size
        bottom = torch.full_like(self.paddle, s - 1)
        paddle = torch.stack([torch.stack([bottom, self.paddle + d], dim=1) for d in (-1, 0, 1)], dim=1)
        self.mark(obs, ME, paddle)
        self.mark(obs, EXTRA, self.prev)
        self.mark(obs, ITEMS, self.ball)
