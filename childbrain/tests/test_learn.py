"""WP 1.1: the plain brain and practice-by-reward."""

import torch

from childbrain.brain.net import build, choose, from_json, weights_to_json
from childbrain.learn.ppo import Learner, Settings, greedy, masked, sample
from childbrain.measure.learn_alone import TARGETS, learn_alone
from childbrain.runcard import compare, rerun, run, set_seed
from childbrain.worlds import PRACTICE, play_once
from childbrain.worlds.base import ITEMS, LEFT, ME, RIGHT, STAY, UP, World


class GoRight(World):
    """A tiny test game: the thing is on the left or the right; press that way (+1), the other way (-1).
    It needs the picture, so a brain that ignores it can't score more than 0 on average."""

    name = "go-right"
    actions_used = (LEFT, RIGHT, STAY, UP)
    default_size = 5
    default_max_steps = 1

    def _setup(self):
        self.side = torch.zeros(self.n, dtype=torch.long, device=self.device)

    def _reset(self, which):
        new = torch.randint(0, 2, (self.n,), generator=self.gen, device=self.device)
        self.side = torch.where(which, new, self.side)

    def _step(self, actions):
        want = torch.where(self.side == 1, RIGHT, LEFT)
        reward = torch.where(actions == want, 1.0, torch.where(actions == STAY, 0.0, -1.0))
        reward = torch.where(actions == UP, -1.0, reward)
        return reward, torch.ones(self.n, dtype=torch.bool, device=self.device)

    def _draw(self, obs):
        obs[:, ME, 2, 2] = 1.0
        col = torch.where(self.side == 1, 4, 0)
        obs[self.rows, ITEMS, 2, col] = 1.0


def test_plain_brain_has_both_heads_and_saves_as_json():
    for eyes in ("flat", "grid"):
        genes = {"kind": "plain", "hidden": 16, "layers": 2, "eyes": eyes, "channels": 4}
        net = build(genes)
        obs = torch.rand(3, 6, 12, 12)
        scores, guess = net.both(obs)
        assert scores.shape == (3, 6) and guess.shape == (3,)
        again = from_json(genes, weights_to_json(net))
        assert torch.equal(again(obs), net(obs))


def test_a_new_brain_starts_unsure():
    net = build({"kind": "plain", "hidden": 32})
    probs = torch.softmax(net(torch.rand(64, 6, 12, 12)), 1)
    assert (probs - 1 / 6).abs().max() < 0.02


def test_never_picks_a_button_that_isnt_allowed():
    gen = torch.Generator().manual_seed(0)
    scores = torch.randn(5000, 6) * 5
    allowed = torch.rand(5000, 6, generator=gen) < 0.5
    allowed[:, 4] = True
    picks = sample(masked(scores, allowed), gen)
    assert allowed.gather(1, picks[:, None]).all()


def test_sampling_follows_the_brains_liking():
    gen = torch.Generator().manual_seed(1)
    scores = torch.log(torch.tensor([[0.7, 0.2, 0.1, 1e-9, 1e-9, 1e-9]])).expand(20000, -1)
    counts = torch.bincount(sample(scores, gen), minlength=6).float() / 20000
    assert torch.allclose(counts[:3], torch.tensor([0.7, 0.2, 0.1]), atol=0.02)


def test_advantages_stop_at_the_end_of_an_episode():
    net = build({"kind": "plain", "hidden": 4})
    learner = Learner(net, GoRight(n=1), Settings(gamma=0.5, lam=1.0))
    r = {"rewards": torch.tensor([[0.0], [1.0], [5.0]]), "dones": torch.tensor([[0.0], [1.0], [0.0]]),
         "values": torch.zeros(4, 1)}
    adv, ret = learner.advantages(r)
    # step 0 sees step 1's reward (halved); step 1 ended, so it never sees step 2's 5.
    assert torch.allclose(adv.flatten(), torch.tensor([0.5, 1.0, 5.0]))
    assert torch.allclose(ret, adv)


def test_practice_by_reward_learns_a_tiny_game():
    set_seed(0)
    net = build({"kind": "plain", "hidden": 32, "layers": 1})
    world = GoRight(n=256, seed=1)
    before = play_once(GoRight(n=512, seed=2), greedy(net)).mean().item()
    learner = Learner(net, world, Settings(horizon=8, lr=1e-3), total_rounds=60)
    for _ in range(60):
        learner.round()
    after = play_once(GoRight(n=512, seed=2), greedy(net)).mean().item()
    assert before < 0.5 and after > 0.95, (before, after)
    assert learner.progress.sureness[-1] > learner.progress.sureness[0]
    buttons, ok = choose(net, GoRight(n=4, seed=3).observe(), GoRight(n=4, seed=3).allowed())
    assert ok


def test_every_practice_game_has_a_target_between_random_and_best():
    assert set(TARGETS) == set(PRACTICE)
    for game, (target, floor, _) in TARGETS.items():
        assert target > floor, game


def test_learn_alone_run_repeats_exactly(tmp_path):
    settings = {"game": "catch", "copies": 32, "max_steps": 32 * 16 * 4, "horizon": 16, "check_every": 2,
                "check_copies": 32, "hidden": 16, "channels": 4, "minibatches": 2, "epochs": 1}
    folder = run("childbrain.measure.learn_alone:learn_alone", settings, seed=3, runs_dir=tmp_path)
    report = rerun(folder)
    assert report.same, report.summary()


def test_wall_clock_keys_are_not_compared():
    assert compare({"clock_seconds": 1.0, "x": 1}, {"clock_seconds": 2.0, "x": 1}) == []
    assert compare({"x": 1}, {"x": 2})


def test_sealed_games_cant_be_practised():
    import pytest
    with pytest.raises(ValueError):
        learn_alone({**learn_alone.defaults, "game": "key-door"}, "cpu")
