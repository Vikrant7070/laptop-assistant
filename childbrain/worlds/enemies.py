"""Enemies that chase: shared by Avoid-the-enemy and the Maze-with-enemy mixed game."""

import torch


def move_enemies(world, enemies: torch.Tensor, target: torch.Tensor, blocked: torch.Tensor | None,
                 chase: float) -> torch.Tensor:
    """Move every enemy one step (or not at all).

    enemies (n, k, 2), target (n, 2), blocked (n, s, s) walls or None.
    With chance `chase` an enemy steps toward the target; otherwise it moves at random.
    It never walks through walls or off the board."""
    n, k, _ = enemies.shape
    s = world.size
    cand = enemies[:, :, None, :] + world.moves[:5][None, None]          # (n, k, 5, 2): 4 moves + stay
    ok = ((cand >= 0) & (cand < s)).all(-1)
    cand = cand.clamp(0, s - 1)
    if blocked is not None:
        rows = world.rows[:, None, None].expand(-1, k, 5)
        ok &= ~blocked[rows, cand[..., 0], cand[..., 1]]
    dist = (cand - target[:, None, None, :]).abs().sum(-1).float()
    noise = torch.rand((n, k, 5), generator=world.gen, device=world.device)
    hunt = torch.rand((n, k, 1), generator=world.gen, device=world.device) < chase
    score = torch.where(hunt, -dist + 0.1 * noise, noise)
    pick = torch.where(ok, score, -1e9).argmax(-1)                       # stay is always ok
    return cand.gather(2, pick[..., None, None].expand(-1, -1, 1, 2)).squeeze(2)
