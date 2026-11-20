"""Mixed games: two games combined, so winning needs two old skills at once.

  maze-coins  (practice) Maze + Collect-coins: coins on the way (+0.2 each), the goal still ends it (+1).
  maze-enemy  (SEALED)   Maze + Avoid-the-enemy: reach the goal (+1) while an enemy hunts you (-1, ends it).
                         Always uses the exam maze bank.
"""

import torch

from .base import ENEMIES, ITEMS
from .enemies import move_enemies
from .maze import Maze


class MazeCoins(Maze):
    name = "maze-coins"
    coin_value = 0.2

    def __init__(self, *, coins: int = 4, **kwargs):
        self.k = coins
        super().__init__(**kwargs)

    def _setup(self):
        super()._setup()
        s = self.size
        self.coins = torch.zeros(self.n, s, s, dtype=torch.bool, device=self.device)
        rooms = torch.zeros(s, s, dtype=torch.bool, device=self.device)
        rooms[1::2, 1::2] = True
        self.rooms = rooms

    def _reset(self, which):
        super()._reset(which)
        free = self.rooms.expand(self.n, -1, -1).clone()
        free[self.rows, self.pos[:, 0], self.pos[:, 1]] = False
        free[self.rows, self.goal[:, 0], self.goal[:, 1]] = False
        cells = self.pick_cells(free, self.k)
        coins = torch.zeros_like(self.coins)
        coins[self.rows[:, None].expand(-1, self.k), cells[..., 0], cells[..., 1]] = True
        self.coins = torch.where(which[:, None, None], coins, self.coins)

    def _step(self, actions):
        reward, done = super()._step(actions)
        got = self.coins[self.rows, self.pos[:, 0], self.pos[:, 1]]
        self.coins[self.rows, self.pos[:, 0], self.pos[:, 1]] = False
        return reward + self.coin_value * got.float(), done

    def _draw(self, obs):
        super()._draw(obs)
        s = self.size
        obs[:, ITEMS, :s, :s] = self.coins.float()


class MazeEnemy(Maze):
    name = "maze-enemy"
    sealed = True
    default_max_steps = 120

    def __init__(self, *, chase: float = 0.3, **kwargs):
        self.chase = chase
        super().__init__(**kwargs)

    def _setup(self):
        super()._setup()
        self.enemy = torch.zeros(self.n, 1, 2, dtype=torch.long, device=self.device)

    def _reset(self, which):
        super()._reset(which)
        # The enemy starts in a room that is neither the start nor the goal.
        k = self.cells * self.cells
        idx = torch.randint(0, k, (self.n,), generator=self.gen, device=self.device)
        cell = torch.stack([2 * (idx // self.cells) + 1, 2 * (idx % self.cells) + 1], dim=1)
        taken = self.same_cell(cell, self.pos) | self.same_cell(cell, self.goal)
        for _ in range(4):  # re-roll the few that landed on the start or goal
            idx = torch.where(taken, (idx + 1) % k, idx)
            cell = torch.stack([2 * (idx // self.cells) + 1, 2 * (idx % self.cells) + 1], dim=1)
            taken = self.same_cell(cell, self.pos) | self.same_cell(cell, self.goal)
        self.enemy = torch.where(which[:, None, None], cell[:, None], self.enemy)

    def _step(self, actions):
        reward, reached = super()._step(actions)
        caught = self.same_cell(self.pos, self.enemy).any(1) & ~reached
        self.enemy = move_enemies(self, self.enemy, self.pos, self.walls, self.chase)
        caught |= self.same_cell(self.pos, self.enemy).any(1) & ~reached
        return reward - caught.float(), reached | caught

    def _draw(self, obs):
        super()._draw(obs)
        self.mark(obs, ENEMIES, self.enemy)
