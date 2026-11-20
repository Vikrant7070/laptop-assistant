"""Worlds: the tiny games, their 'changed' versions, and later the 2D world (WP 0.3, 0.4, 4.1).

All games share one format (see base.py), so one brain can play them all.

  PRACTICE  games for learning and evolution
  SEALED    exam-only games: they refuse to be built for practice, and their code and exam content are
            fingerprinted by the guard (childbrain/guard/seals.json)

make(name, change=...) builds any game, optionally look-changed or rules-changed.
"""

import random

from .avoid import Avoid
from .base import ACTIONS, LAYERS, MAX_SIZE, World, play_once, random_actions
from .catch import Catch
from .changed import LookChanged, RulesChanged
from .coins import Coins
from .colour_match import ColourMatch
from .connect4 import Connect4
from .key_door import KeyDoor
from .maze import Maze
from .mixed import MazeCoins, MazeEnemy
from .pong import Pong
from .push_box import PushBox
from .snake import Snake

PRACTICE = {c.name: c for c in (Snake, Maze, Catch, PushBox, Avoid, Coins, ColourMatch, Connect4, MazeCoins)}
SEALED = {c.name: c for c in (KeyDoor, Pong, MazeEnemy)}
WORLDS = {**PRACTICE, **SEALED}
CHANGES = (None, "look", "controls", "reward")


def make(name: str, *, change: str | None = None, change_seed: int = 0, **kwargs):
    """Build a game by name. Sealed games are always built as exams (split='test')."""
    cls = WORLDS[name]
    if cls.sealed:
        if kwargs.get("split", "test") != "test":
            raise PermissionError(f"{name} is a sealed exam game: it can't be used for practice")
        kwargs["split"] = "test"
    if change == "look" and not kwargs.get("size") and len(cls.sizes) > 1:
        # A new look includes a new board size, where the game allows one.
        other = [s for s in cls.sizes if s != cls.default_size]
        kwargs["size"] = random.Random(change_seed).choice(other)
    world = cls(**kwargs)
    if change is None:
        return world
    if change == "look":
        return LookChanged(world, seed=change_seed)
    if change in ("controls", "reward"):
        return RulesChanged(world, kind=change, seed=change_seed)
    raise ValueError(f"change must be one of {CHANGES}")


__all__ = ["ACTIONS", "CHANGES", "LAYERS", "MAX_SIZE", "PRACTICE", "SEALED", "WORLDS", "World",
           "LookChanged", "RulesChanged", "make", "play_once", "random_actions"]
