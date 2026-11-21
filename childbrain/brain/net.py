"""The simplest brains, and how a brain is stored.

A brain = genes (a small JSON description of its shape) + weights (its numbers).
Weights are stored as plain JSON numbers, not as a pickle, so loading a brain can never run code.
Every brain answers the shared game format: it looks at the picture and picks an allowed button.
Bigger building blocks (grid, memory cell, ternary, experts) come in Phase 1-2.

  {"kind": "linear"}                 one layer: picture -> button scores
  {"kind": "mlp", "hidden": 64}      picture -> hidden layer -> button scores
  {"kind": "plain", "hidden": 256, "layers": 2, "eyes": "flat" | "grid", "channels": 32}
                                     the plain learning brain (WP 1.1): picture -> hidden layers -> button
                                     scores AND a guess of how much score is still to come (the "critic").
                                     How sure it is = how much of its choice goes to one button.
                                     eyes="grid" first looks at the picture with two small 3x3 filters layers
                                     (the same filter everywhere), so "food just left of me" is learned once,
                                     not once per square.
"""

from __future__ import annotations

import torch
from torch import nn

from ..worlds.base import MAX_SIZE, N_ACTIONS, N_LAYERS

INPUTS = N_LAYERS * MAX_SIZE * MAX_SIZE
KINDS = ("linear", "mlp", "plain")


class PlainBrain(nn.Module):
    """Hidden layers shared by two heads: button scores (forward) and a score guess (value)."""

    def __init__(self, hidden: int, layers: int, eyes: str = "flat", channels: int = 32):
        super().__init__()
        if eyes == "flat":
            parts, width = [nn.Flatten()], INPUTS
        elif eyes == "grid":
            parts = [nn.Conv2d(N_LAYERS, channels, 3, padding=1), nn.ReLU(),
                     nn.Conv2d(channels, channels, 3, padding=1), nn.ReLU(), nn.Flatten()]
            width = channels * MAX_SIZE * MAX_SIZE
        else:
            raise ValueError("eyes must be 'flat' or 'grid'")
        for _ in range(layers):
            parts += [nn.Linear(width, hidden), nn.Tanh()]
            width = hidden
        self.body = nn.Sequential(*parts)
        self.buttons = nn.Linear(hidden, N_ACTIONS)
        self.critic = nn.Linear(hidden, 1)
        with torch.no_grad():  # start nearly unsure: every button about equally likely
            self.buttons.weight.mul_(0.01)
            self.buttons.bias.zero_()

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        return self.buttons(self.body(obs))

    def both(self, obs: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """(button scores, score guess) from one pass through the body."""
        h = self.body(obs)
        return self.buttons(h), self.critic(h).squeeze(-1)


def build(genes: dict) -> nn.Module:
    kind = genes.get("kind")
    if kind == "linear":
        return nn.Sequential(nn.Flatten(), nn.Linear(INPUTS, N_ACTIONS))
    if kind == "mlp":
        hidden = int(genes.get("hidden", 64))
        if not 1 <= hidden <= 4096:
            raise ValueError("hidden must be 1..4096")
        return nn.Sequential(nn.Flatten(), nn.Linear(INPUTS, hidden), nn.ReLU(), nn.Linear(hidden, N_ACTIONS))
    if kind == "plain":
        hidden, layers = int(genes.get("hidden", 256)), int(genes.get("layers", 2))
        channels = int(genes.get("channels", 32))
        if not 1 <= hidden <= 4096 or not 1 <= layers <= 8 or not 1 <= channels <= 128:
            raise ValueError("hidden must be 1..4096, layers 1..8 and channels 1..128")
        return PlainBrain(hidden, layers, genes.get("eyes", "flat"), channels)
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


def choose(net: nn.Module, obs: torch.Tensor, allowed: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """(best allowed button per copy, whether all scores were real numbers). Stays on the GPU: no waiting."""
    with torch.no_grad():
        scores = net(obs)
    ok = torch.isfinite(torch.where(allowed, scores, 0.0)).all()
    return torch.where(allowed, scores, -torch.inf).argmax(1), ok


def act(net: nn.Module, obs: torch.Tensor, allowed: torch.Tensor) -> torch.Tensor:
    """Pick the best-scoring allowed button for every copy; a brain giving NaN / infinity is broken."""
    buttons, ok = choose(net, obs, allowed)
    if not ok:
        raise BadBrain("the brain's button scores aren't all numbers")
    return buttons
