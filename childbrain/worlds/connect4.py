"""Connect-4: get four of your discs in a row (across, down or diagonal).

To fit the shared buttons, you move a cursor along the top row (left / right) and "use" drops a disc in
that column. Dropping into a full column isn't allowed.

  opponent="random"  you play first or second (coin flip) against a random player:
                     +1 when you win, -1 when it wins, 0 for a draw.
  opponent="self"    self-play: one brain plays both sides. The picture is always drawn from the side of
                     whoever moves next, and +1 goes to the move that wins. info["player"] says who moved.

Layers: me = my discs, enemies = the other side's discs, extra = the cursor (top row of the picture).
The board is 6 rows x 7 columns, drawn under the cursor row.
"""

import torch

from .base import ENEMIES, EXTRA, LEFT, ME, RIGHT, STAY, USE, StepResult, World

ROWS, COLS = 6, 7


def four_in_a_row(mine: torch.Tensor) -> torch.Tensor:
    """mine (n, 6, 7) bool -> (n,) has four in a row."""
    m = mine
    across = m[:, :, :-3] & m[:, :, 1:-2] & m[:, :, 2:-1] & m[:, :, 3:]
    down = m[:, :-3] & m[:, 1:-2] & m[:, 2:-1] & m[:, 3:]
    diag = m[:, :-3, :-3] & m[:, 1:-2, 1:-2] & m[:, 2:-1, 2:-1] & m[:, 3:, 3:]
    anti = m[:, :-3, 3:] & m[:, 1:-2, 2:-1] & m[:, 2:-1, 1:-2] & m[:, 3:, :-3]
    return torch.stack([x.flatten(1).any(1) for x in (across, down, diag, anti)]).any(0)


class Connect4(World):
    name = "connect-4"
    actions_used = (LEFT, RIGHT, STAY, USE)
    default_size = 7
    sizes = (7,)
    one_me = False
    default_max_steps = 300

    def __init__(self, *, opponent: str = "random", **kwargs):
        if opponent not in ("random", "self"):
            raise ValueError("opponent must be 'random' or 'self'")
        self.opponent = opponent
        super().__init__(**kwargs)

    def _setup(self):
        n, dev = self.n, self.device
        self.board = torch.zeros(n, ROWS, COLS, dtype=torch.long, device=dev)  # 0 empty, 1 / 2 = players
        self.turn = torch.ones(n, dtype=torch.long, device=dev)                 # who moves next
        self.me_player = torch.ones(n, dtype=torch.long, device=dev)            # vs random: which side I am
        self.cursor = torch.zeros(n, dtype=torch.long, device=dev)
        self.last_player = torch.zeros(n, dtype=torch.long, device=dev)

    def _reset(self, which):
        n = self.n
        self.board = torch.where(which[:, None, None], 0, self.board)
        self.turn = torch.where(which, 1, self.turn)
        cursor = torch.randint(0, COLS, (n,), generator=self.gen, device=self.device)
        self.cursor = torch.where(which, cursor, self.cursor)
        if self.opponent == "random":
            second = torch.rand(n, generator=self.gen, device=self.device) < 0.5
            self.me_player = torch.where(which, torch.where(second, 2, 1), self.me_player)
            self._random_move(which & second)  # the random player opens when I'm second

    def _drop(self, col, player, do):
        filled = (self.board[self.rows, :, col] != 0).sum(1)
        land = (ROWS - 1 - filled).clamp_min(0)
        cell = self.board[self.rows, land, col]
        self.board[self.rows, land, col] = torch.where(do, player, cell)

    def _random_move(self, do):
        legal = self.board[:, 0, :] == 0
        noise = torch.rand(legal.shape, generator=self.gen, device=self.device)
        col = torch.where(legal, noise, -1.0).argmax(1)
        self._drop(col, self.turn, do)
        self.turn = torch.where(do, 3 - self.turn, self.turn)

    def _allowed(self, allowed):
        allowed[:, USE] = self.board[self.rows, 0, self.cursor] == 0
        return allowed

    def _step(self, actions):
        self.cursor = (self.cursor + self.moves[actions, 1]).clamp(0, COLS - 1)
        drop = actions == USE
        mover = self.turn.clone()
        self._drop(self.cursor, mover, drop)
        won = drop & four_in_a_row(self.board == mover[:, None, None])
        full = (self.board[:, 0, :] != 0).all(1)
        over = won | (drop & full)
        self.turn = torch.where(drop, 3 - self.turn, self.turn)
        self.last_player = mover
        reward = won.float()
        if self.opponent == "random":
            reply = drop & ~over
            self._random_move(reply)
            lost = reply & four_in_a_row(self.board == (3 - self.me_player)[:, None, None])
            full = (self.board[:, 0, :] != 0).all(1)
            reward = reward - lost.float()
            over = over | lost | (reply & full)
        return reward, over

    def step(self, actions) -> StepResult:
        res = super().step(actions)
        res.info["player"] = self.last_player
        return res

    def _draw(self, obs):
        side = self.me_player if self.opponent == "random" else self.turn
        b = self.board
        obs[:, ME, 1:ROWS + 1, :COLS] = (b == side[:, None, None]).float()
        obs[:, ENEMIES, 1:ROWS + 1, :COLS] = ((b != 0) & (b != side[:, None, None])).float()
        self.mark(obs, EXTRA, torch.stack([torch.zeros_like(self.cursor), self.cursor], dim=1))
