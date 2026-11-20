"""Key-and-door (SEALED exam game): pick up the key ("use" while standing on it), open the door, reach the goal.

Only reaching the goal scores (+1), so the reward is rare and needs two steps of planning.
Layers: walls, me, items = the key (drawn on me once carried), goal, extra = the door.
"""

import torch

from .base import EXTRA, GOAL, ITEMS, ME, USE, WALLS, World
from .base import DOWN, LEFT, RIGHT, STAY, UP


class KeyDoor(World):
    name = "key-door"
    sealed = True
    actions_used = (UP, DOWN, LEFT, RIGHT, STAY, USE)
    default_size = 9
    sizes = tuple(range(7, 13))
    default_max_steps = 150

    def _setup(self):
        n, s, dev = self.n, self.size, self.device
        self.mid = s // 2
        walls = torch.zeros(s, s, dtype=torch.bool, device=dev)
        walls[0], walls[-1], walls[:, 0], walls[:, -1] = True, True, True, True
        walls[:, self.mid] = True
        self.base_walls = walls
        self.door = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.me = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.key = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.goal = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.has_key = torch.zeros(n, dtype=torch.bool, device=dev)

    def _reset(self, which):
        s, n = self.size, self.n
        row = torch.randint(1, s - 1, (n,), generator=self.gen, device=self.device)
        door = torch.stack([row, torch.full_like(row, self.mid)], dim=1)
        left = torch.zeros(n, s, s, dtype=torch.bool, device=self.device)
        left[:, 1:s - 1, 1:self.mid] = True
        right = torch.zeros_like(left)
        right[:, 1:s - 1, self.mid + 1:s - 1] = True
        two = self.pick_cells(left, 2)
        goal = self.pick_cells(right, 1)[:, 0]
        w = which[:, None]
        self.door = torch.where(w, door, self.door)
        self.me = torch.where(w, two[:, 0], self.me)
        self.key = torch.where(w, two[:, 1], self.key)
        self.goal = torch.where(w, goal, self.goal)
        self.has_key = torch.where(which, False, self.has_key)

    def _on_key(self):
        return self.same_cell(self.me, self.key) & ~self.has_key

    def _allowed(self, allowed):
        allowed[:, USE] = self._on_key()
        return allowed

    def _step(self, actions):
        self.has_key = self.has_key | ((actions == USE) & self._on_key())
        new = self.me + self.moves[actions]
        wall = self.base_walls[new[:, 0], new[:, 1]]
        at_door = self.same_cell(new, self.door)
        blocked = wall & ~(at_door & self.has_key)
        self.me = torch.where(blocked[:, None], self.me, new)
        done = self.same_cell(self.me, self.goal)
        return done.float(), done

    def _draw(self, obs):
        s = self.size
        walls = self.base_walls.expand(self.n, -1, -1).clone()
        walls[self.rows, self.door[:, 0], self.door[:, 1]] = False
        obs[:, WALLS, :s, :s] = walls.float()
        self.mark(obs, ME, self.me)
        self.mark(obs, ITEMS, torch.where(self.has_key[:, None], self.me, self.key))
        self.mark(obs, GOAL, self.goal)
        self.mark(obs, EXTRA, self.door)
