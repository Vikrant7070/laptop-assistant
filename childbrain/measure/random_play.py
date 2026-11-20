"""How well does a random player do? The 'floor' score every brain must beat."""

import torch

from ..runcard import experiment
from ..worlds import WORLDS, play_once, random_actions


@experiment(defaults={"game": "snake", "copies": 4096, "size": 0, "max_steps": 0, "split": "train"})
def random_play(settings: dict, device: str) -> dict:
    cls = WORLDS[settings["game"]]
    world = cls(n=settings["copies"], size=settings["size"] or None, device=device,
                max_steps=settings["max_steps"] or None, split=settings["split"],
                seed=int(torch.randint(0, 2**62, (1,)).item()))  # from the run card's seed
    gen = torch.Generator(device=world.device)
    gen.manual_seed(int(torch.randint(0, 2**62, (1,)).item()))
    scores = play_once(world, lambda obs, allowed: random_actions(allowed, gen))
    return {
        "game": world.name,
        "size": world.size,
        "max_steps": world.max_steps,
        "mean_score": scores.mean().item(),
        "share_scored": (scores > 0).float().mean().item(),
        "best_score": scores.max().item(),
    }
