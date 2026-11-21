"""Practice-by-reward (WP 1.1): a plain, standard PPO-style actor-critic.

How one round of practice works, in simple words:
  1. Play: thousands of copies of a game play `horizon` steps with the brain, picking buttons by chance in
     proportion to how much the brain likes them (so it keeps trying things).
  2. Look back: for every step, was it better or worse than the brain's own guess (the critic)?
     That difference is the "advantage" (worked out with GAE, which blends short and long views).
  3. Learn: nudge the brain toward the better-than-expected choices and its guesses toward what really
     happened. PPO's "clip" stops any one round changing the brain too much.

Only allowed buttons are ever picked: the others get a huge negative score before choosing.
Runs entirely on one device and never asks the GPU to wait, except once per round for the progress numbers.
Brain-side code (see guard/codecheck.py): no files, no os, no network — only torch and the worlds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import torch
from torch import nn

from ..brain.net import PlainBrain
from ..worlds.base import N_ACTIONS, World

NOT_ALLOWED = -1e9  # big but finite, so the maths never makes NaN


@dataclass
class Settings:
    horizon: int = 64          # steps per copy per round
    epochs: int = 4            # passes over each round's steps
    minibatches: int = 8
    lr: float = 3e-4
    gamma: float = 0.99        # how much later score counts
    lam: float = 0.95          # GAE blend
    clip: float = 0.2
    entropy: float = 0.01      # a small push to keep trying different buttons
    value_weight: float = 0.5
    max_grad: float = 0.5
    anneal: bool = True        # learning rate goes down to 0 over the planned rounds


@dataclass
class Progress:
    """What happened in each round (kept small: plain numbers)."""
    steps: list = field(default_factory=list)          # total game steps so far
    score: list = field(default_factory=list)          # average score of episodes that ended this round
    episodes: list = field(default_factory=list)       # how many ended
    sureness: list = field(default_factory=list)       # average chance given to the picked-most button


def masked(scores: torch.Tensor, allowed: torch.Tensor) -> torch.Tensor:
    return torch.where(allowed, scores, NOT_ALLOWED)


def sample(scores: torch.Tensor, gen: torch.Generator | None = None) -> torch.Tensor:
    """Pick one button per copy with chance = softmax(scores). Gumbel trick: no CPU-GPU wait."""
    u = torch.rand(scores.shape, generator=gen, device=scores.device).clamp_(1e-10, 1.0 - 1e-7)
    return (scores - torch.log(-torch.log(u))).argmax(1)


class Learner:
    """Practice one game with one plain brain."""

    def __init__(self, brain: PlainBrain, world: World, settings: Settings | None = None,
                 total_rounds: int = 0):
        self.brain, self.world = brain, world
        self.s = settings or Settings()
        self.opt = torch.optim.Adam(brain.parameters(), lr=self.s.lr, eps=1e-5)
        self.total_rounds = total_rounds
        self.rounds = 0
        self.steps = 0
        self.obs = world.observe()
        self.progress = Progress()

    def play(self) -> dict:
        """Play one round; return the round's steps as tensors."""
        w, T, dev = self.world, self.s.horizon, self.world.device
        n = w.n
        obs = torch.empty((T, *self.obs.shape), device=dev)
        allowed = torch.empty((T, n, N_ACTIONS), dtype=torch.bool, device=dev)
        acts = torch.empty((T, n), dtype=torch.long, device=dev)
        logp = torch.empty((T, n), device=dev)
        values = torch.empty((T + 1, n), device=dev)
        rewards = torch.empty((T, n), device=dev)
        dones = torch.empty((T, n), device=dev)
        ended_sum = torch.zeros((), device=dev)
        ended_n = torch.zeros((), device=dev)
        sure = torch.zeros((), device=dev)
        with torch.no_grad():
            for t in range(T):
                ok = w.allowed()
                scores, v = self.brain.both(self.obs)
                scores = masked(scores, ok)
                a = sample(scores)
                lp = torch.log_softmax(scores, 1)
                obs[t], allowed[t], acts[t], values[t] = self.obs, ok, a, v
                logp[t] = (lp * nn.functional.one_hot(a, lp.shape[1])).sum(1)
                sure += lp.exp().max(1).values.mean()
                res = w.step(a)
                rewards[t], dones[t] = res.reward, res.done.float()
                ended_sum += torch.where(res.done, res.info["episode_return"], 0.0).sum()
                ended_n += res.done.sum()
                self.obs = res.obs
            values[T] = self.brain.both(self.obs)[1]
        self.steps += T * n
        return {"obs": obs, "allowed": allowed, "acts": acts, "logp": logp, "values": values,
                "rewards": rewards, "dones": dones, "ended_sum": ended_sum, "ended_n": ended_n,
                "sure": sure / T}

    def advantages(self, r: dict) -> tuple[torch.Tensor, torch.Tensor]:
        """GAE. An episode that ended (or timed out) doesn't look past its end."""
        T = r["rewards"].shape[0]
        adv = torch.zeros_like(r["rewards"])
        last = torch.zeros_like(r["rewards"][0])
        for t in reversed(range(T)):
            keep = 1.0 - r["dones"][t]
            delta = r["rewards"][t] + self.s.gamma * r["values"][t + 1] * keep - r["values"][t]
            last = delta + self.s.gamma * self.s.lam * keep * last
            adv[t] = last
        return adv, adv + r["values"][:-1]

    def learn(self, r: dict) -> dict:
        s = self.s
        adv, ret = self.advantages(r)
        obs = r["obs"].flatten(0, 1)
        allowed = r["allowed"].flatten(0, 1)
        acts = r["acts"].flatten()
        old_logp = r["logp"].flatten()
        adv, ret = adv.flatten(), ret.flatten()
        total = acts.shape[0]
        size = total // s.minibatches
        if self.total_rounds and s.anneal:
            for g in self.opt.param_groups:
                g["lr"] = s.lr * max(0.0, 1.0 - self.rounds / self.total_rounds)
        stats = torch.zeros(3, device=obs.device)
        for _ in range(s.epochs):
            order = torch.randperm(total, device=obs.device)
            for i in range(s.minibatches):
                idx = order[i * size:(i + 1) * size]
                scores, v = self.brain.both(obs[idx])
                lp_all = torch.log_softmax(masked(scores, allowed[idx]), 1)
                lp = (lp_all * nn.functional.one_hot(acts[idx], lp_all.shape[1])).sum(1)
                a = adv[idx]
                a = (a - a.mean()) / (a.std() + 1e-8)
                ratio = (lp - old_logp[idx]).exp()
                policy_loss = -torch.min(ratio * a, ratio.clamp(1 - s.clip, 1 + s.clip) * a).mean()
                value_loss = 0.5 * (v - ret[idx]).pow(2).mean()
                entropy = -(lp_all.exp() * lp_all).sum(1).mean()
                loss = policy_loss + s.value_weight * value_loss - s.entropy * entropy
                self.opt.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.brain.parameters(), s.max_grad)
                self.opt.step()
                stats += torch.stack([policy_loss.detach(), value_loss.detach(), entropy.detach()])
        return {"stats": stats / (s.epochs * s.minibatches)}

    def round(self) -> None:
        """Play, then learn. Records progress (the one place the CPU waits for the GPU)."""
        r = self.play()
        self.learn(r)
        self.rounds += 1
        ended_n = float(r["ended_n"])
        p = self.progress
        p.steps.append(self.steps)
        p.episodes.append(int(ended_n))
        p.score.append(float(r["ended_sum"]) / ended_n if ended_n else None)
        p.sureness.append(float(r["sure"]))


def greedy(brain: nn.Module):
    """The brain's own best button (what the guard's exam uses)."""
    def policy(obs, allowed):
        with torch.no_grad():
            return masked(brain(obs), allowed).argmax(1)
    return policy


def by_chance(brain: nn.Module, gen: torch.Generator):
    """Buttons by chance in proportion to how much the brain likes them (how it practises)."""
    def policy(obs, allowed):
        with torch.no_grad():
            return sample(masked(brain(obs), allowed), gen)
    return policy
