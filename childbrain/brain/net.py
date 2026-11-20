"""The simplest brains, and how a brain is stored.

A brain = genes (a small JSON description of its shape) + weights (its numbers).
Weights are stored as plain JSON numbers, not as a pickle, so loading a brain can never run code.
Every brain answers the shared game format: it looks at the picture and picks an allowed button.
Bigger building blocks (grid, memory cell, ternary, experts) come in Phase 1-2.

  {"kind": "linear"}                 one layer: picture -> button scores
  {"kind": "mlp", "hidden": 64}      picture -> hidden layer -> button scores
"""

from __future__ import annotations

import torch
from torch import nn

from ..worlds.base import MAX_SIZE, N_ACTIONS, N_LAYERS

INPUTS = N_LAYERS * MAX_SIZE * MAX_SIZE
KINDS = ("linear", "mlp")


def build(genes: dict) -> nn.Module:
    kind = genes.get("kind")
    if kind == "linear":
        return nn.Sequential(nn.Flatten(), nn.Linear(INPUTS, N_ACTIONS))
    if kind == "mlp":
        hidden = int(genes.get("hidden", 64))
        if not 1 <= hidden <= 4096:
            raise ValueError("hidden must be 1..4096")
        return nn.Sequential(nn.Flatten(), nn.Linear(INPUTS, hidden), nn.ReLU(), nn.Linear(hidden, N_ACTIONS))
    raise ValueError(f"unknown brain kind {kind!r}; known: {KINDS}")


def weights_to_json(net: nn.Module) -> dict:
    """{name: [shape, flat list of numbers]}. float32 -> Python float is exact, so nothing is lost."""
    return {k: [list(v.shape), v.detach().cpu().double().flatten().tolist()] for k, v in net.state_dict().items()}


def load_weights(net: nn.Module, data: dict) -> nn.Module:
    want = net.state_dict()
    if set(data) != set(want):
        raise ValueError(f"weights don't fit these genes: expected {sorted(want)}, got {sorted(data)}")
    state = {}
    for k, (shape, flat) in data.items():
        if list(shape) != list(want[k].shape) or len(flat) != want[k].numel():
            raise ValueError(f"weights for {k} have the wrong shape")
        state[k] = torch.tensor(flat, dtype=want[k].dtype).reshape(shape)
    net.load_state_dict(state)
    return net


def from_json(genes: dict, weights: dict) -> nn.Module:
    return load_weights(build(genes), weights)


class BadBrain(Exception):
    """The brain gave scores that aren't numbers (NaN / infinity)."""


def act(net: nn.Module, obs: torch.Tensor, allowed: torch.Tensor) -> torch.Tensor:
    """Pick the best-scoring allowed button for every copy."""
    with torch.no_grad():
        scores = net(obs)
    if not torch.isfinite(scores[allowed]).all():
        raise BadBrain("the brain's button scores aren't all numbers")
    return torch.where(allowed, scores, -torch.inf).argmax(1)
