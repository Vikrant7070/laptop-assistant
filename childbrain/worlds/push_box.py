"""Push-the-box: push the box onto the target (+1). You can push, never pull, so a box in a corner is stuck.

Layers: walls (the room's edge), me, items = box, goal = target.
"""

import torch

from .base import GOAL, ITEMS, ME, WALLS, World


class PushBox(World):
    name = "push-box"
    default_size = 7
    sizes = tuple(range(6, 13))
    default_max_steps = 100

    def _setup(self):
        n, s, dev = self.n, self.size, self.device
        edge = torch.zeros(s, s, dtype=torch.bool, device=dev)
        edge[0], edge[-1], edge[:, 0], edge[:, -1] = True, True, True, True
        self.edge = edge
        self.frame[WALLS, :s, :s] = edge.float()
        self.me = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.box = torch.zeros(n, 2, dtype=torch.long, device=dev)
        self.target = torch.zeros(n, 2, dtype=torch.long, device=dev)

    def _reset(self, which):
        s = self.size
        # The box starts away from the walls, so it can always be pushed somewhere.
        box_ok = torch.zeros(self.n, s, s, dtype=torch.bool, device=self.device)
        box_ok[:, 2:s - 2, 2:s - 2] = True
        box = self.pick_cells(box_ok, 1)[:, 0]
        free = ~self.edge.expand(self.n, -1, -1).clone()
        free[self.rows, box[:, 0], box[:, 1]] = False
        two = self.pick_cells(free, 2)
        w = which[:, None]
        self.box = torch.where(w, box, self.box)
        self.me = torch.where(w, two[:, 0], self.me)
        self.target = torch.where(w, two[:, 1], self.target)

    def _step(self, actions):
        d = self.moves[actions]
        new = self.me + d
        into_box = self.same_cell(new, self.box)
        box_new = self.box + d
        box_stuck = self.edge[box_new[:, 0].clamp(0, self.size - 1), box_new[:, 1].clamp(0, self.size - 1)]
        blocked = self.edge[new[:, 0], new[:, 1]] | (into_box & box_stuck)
        push = into_box & ~blocked
        self.me = torch.where(blocked[:, None], self.me, new)
        self.box = torch.where(push[:, None], box_new, self.box)
        done = self.same_cell(self.box, self.target)
        return done.float(), done

    def _draw(self, obs):
        self.mark(obs, ME, self.me)
        self.mark(obs, ITEMS, self.box)
        self.mark(obs, GOAL, self.target)
