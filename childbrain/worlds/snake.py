"""Snake: eat the food (+1), don't hit the edge or yourself (-1).

The snake keeps moving; "stay" means "keep going the same way". Turning straight back into your own
body isn't allowed. A snake that goes too long without eating ends its episode (no looping forever).

Layers: me = head, items = food, extra = body (1.0 at the head, fading toward the tail).
"""

import torch

from .base import DOWN, EXTRA, ITEMS, LEFT, ME, RIGHT, STAY, UP, World

REVERSE = torch.tensor([DOWN, UP, RIGHT, LEFT])


class Snake(World):
    name = "snake"
    actions_used = (UP, DOWN, LEFT, RIGHT, STAY)
    default_size = 8
    default_max_steps = 200

    def __init__(self, *, hunger_limit: int | None = None, **kwargs):
        self._hunger_limit = hunger_limit
        super().__init__(**kwargs)

    def _setup(self):
        n, s, dev = self.n, self.size, self.device
        self.hunger_limit = self._hunger_limit or 2 * s * s
        # body[i, r, c] = how many more steps that cell stays body (0 = empty). The head has the highest value.
        self.body = torch.zeros(n, s, s, dtype=torch.long, device=dev)
        self.head = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.food = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.length = torch.ones(n, dtype=torch.long, device=dev)
        self.direction = torch.zeros(n, dtype=torch.long, device=dev)
        self.hunger = torch.zeros(n, dtype=torch.long, device=dev)
        self.reverse = REVERSE.to(dev)

    def _reset(self, which):
        s, w = self.size, which
        start = self.rand_cells(s)
        self.head = torch.where(w[:, None], start, self.head)
        self.body = torch.where(w[:, None, None], 0, self.body)
        h = self.head
        self.body[self.rows, h[:, 0], h[:, 1]] = torch.where(w, 1, self.body[self.rows, h[:, 0], h[:, 1]])
        self.length = torch.where(w, 1, self.length)
        new_dir = torch.randint(0, 4, (self.n,), generator=self.gen, device=self.device)
        self.direction = torch.where(w, new_dir, self.direction)
        self.hunger = torch.where(w, 0, self.hunger)
        self._place_food(w)

    def _place_food(self, which):
        s = self.size
        score = torch.rand(self.n, s, s, generator=self.gen, device=self.device)
        score = torch.where(self.body > 0, -1.0, score)
        idx = score.view(self.n, -1).argmax(dim=1)
        cell = torch.stack([idx // s, idx % s], dim=1)
        self.food = torch.where(which[:, None], cell, self.food)

    def _allowed(self, allowed):
        blocked = self.reverse[self.direction]
        allowed[self.rows, blocked] = allowed[self.rows, blocked] & (self.length == 1)
        return allowed

    def _step(self, actions):
        s = self.size
        turn = actions < 4
        direction = torch.where(turn, actions, self.direction)
        new = self.head + self.moves[direction]
        off = ((new < 0) | (new >= s)).any(dim=1)
        new = new.clamp(0, s - 1)
        eat = (new == self.food).all(dim=1) & ~off
        # The tail moves on unless the snake just ate.
        self.body = torch.where(eat[:, None, None], self.body, (self.body - 1).clamp_min(0))
        hit = self.body[self.rows, new[:, 0], new[:, 1]] > 0
        dead = off | hit
        self.length = self.length + eat.long()
        cell = self.body[self.rows, new[:, 0], new[:, 1]]
        self.body[self.rows, new[:, 0], new[:, 1]] = torch.where(dead, cell, self.length)
        self.head = torch.where(dead[:, None], self.head, new)
        self.direction = direction
        self.hunger = torch.where(eat, 0, self.hunger + 1)
        full = self.length >= s * s
        self._place_food(eat & ~full)
        starved = self.hunger >= self.hunger_limit
        reward = eat.float() - dead.float()
        return reward, dead | full | starved

    def _draw(self, obs):
        s = self.size
        self.mark(obs, ME, self.head)
        self.mark(obs, ITEMS, self.food)
        obs[:, EXTRA, :s, :s] = self.body.float() / self.length[:, None, None].float()
