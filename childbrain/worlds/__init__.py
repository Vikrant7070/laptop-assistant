"""Worlds: the tiny games, their 'changed' versions, and later the 2D world (WP 0.3, 0.4, 4.1).

All games share one format (see base.py), so one brain can play them all.
"""

from .base import ACTIONS, LAYERS, MAX_SIZE, World, play_once, random_actions
from .maze import Maze
from .snake import Snake

WORLDS = {cls.name: cls for cls in (Snake, Maze)}

__all__ = ["ACTIONS", "LAYERS", "MAX_SIZE", "WORLDS", "World", "Maze", "Snake", "play_once", "random_actions"]
