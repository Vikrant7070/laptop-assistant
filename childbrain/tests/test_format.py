"""WP 0.4: every game, and every changed version of it, passes the same format tests."""

import pytest

torch = pytest.importorskip("torch")  # skipped under Nova's Python

from childbrain.worlds import ACTIONS, CHANGES, LAYERS, MAX_SIZE, WORLDS, make, play_once, random_actions
from childbrain.worlds.base import ME, STAY, USE, WALLS

GPU = "cuda" if torch.cuda.is_available() else "cpu"
VARIANTS = [(name, change) for name in WORLDS for change in CHANGES]
EACH = pytest.mark.parametrize("name,change", VARIANTS, ids=[f"{n}{'~' + c if c else ''}" for n, c in VARIANTS])


def build(name, change, **kw):
    return make(name, change=change, change_seed=1, **kw)


def rand_player(device="cpu", seed=0):
    g = torch.Generator(device=device).manual_seed(seed)
    return lambda obs, allowed: random_actions(allowed, g)


def inner(w):
    return getattr(w, "world", w)


def layer_of(w, layer):
    """Where a layer ended up in the picture (look-changed worlds swap the layers' 'colours')."""
    layers = getattr(w, "layers", None)
    return layer if layers is None else int((layers == layer).nonzero())


@EACH
def test_picture_and_buttons(name, change):
    w = build(name, change, n=64, seed=0)
    obs = w.observe()
    assert obs.shape == (64, len(LAYERS), MAX_SIZE, MAX_SIZE) and obs.dtype == torch.float32
    assert obs.min() >= 0 and obs.max() <= 1
    if inner(w).one_me:
        assert (obs[:, layer_of(w, ME)].flatten(1).sum(1) == 1).all(), "exactly one 'me' per copy"
    r0, c0, s = w.area
    outside = torch.ones(MAX_SIZE, MAX_SIZE, dtype=torch.bool)
    outside[r0:r0 + s, c0:c0 + s] = False
    assert (obs[:, layer_of(w, WALLS)][:, outside] == 1).all(), "outside the playing area is wall"
    allowed = w.allowed()
    assert allowed.shape == (64, len(ACTIONS)) and allowed.dtype == torch.bool
    assert allowed[:, STAY].all(), "'stay' is always allowed"
    if USE not in inner(w).actions_used:
        assert not allowed[:, USE].any()


@EACH
def test_step_result(name, change):
    w = build(name, change, n=64, seed=0)
    res = w.step(torch.full((64,), STAY))
    assert res.obs.shape == (64, len(LAYERS), MAX_SIZE, MAX_SIZE)
    assert res.reward.shape == (64,) and res.reward.dtype == torch.float32
    assert res.done.shape == (64,) and res.done.dtype == torch.bool
    assert set(res.info) >= {"episode_return", "timeout", "length"}


@EACH
def test_step_limit_is_never_passed(name, change):
    w = build(name, change, n=128, seed=0, max_steps=20)
    play = rand_player()
    for _ in range(45):
        res = w.step(play(None, w.allowed()))
        assert (res.info["length"][res.done] <= 20).all()
        assert (inner(w).t < 20).all()


@EACH
def test_same_seed_same_game(name, change):
    def trace(seed):
        w, play, out = build(name, change, n=32, seed=seed), rand_player(seed=5), []
        for _ in range(30):
            res = w.step(play(None, w.allowed()))
            out += [res.obs, res.reward]
        return torch.cat([t.flatten() for t in out])

    assert torch.equal(trace(3), trace(3))
    assert not torch.equal(trace(3), trace(4))


@EACH
def test_exam_worlds_are_fixed(name, change):
    a, b = build(name, change, n=16, split="test"), build(name, change, n=16, split="test")
    assert torch.equal(a.observe(), b.observe())


@EACH
def test_not_allowed_button_means_stay(name, change):
    a, b = build(name, change, n=64, seed=9), build(name, change, n=64, seed=9)
    blocked = ~a.allowed()
    pick = torch.where(blocked.any(1), blocked.float().argmax(1), STAY)
    ra, rb = a.step(pick), b.step(torch.full((64,), STAY))
    assert torch.equal(ra.obs, rb.obs) and torch.equal(ra.reward, rb.reward)


@pytest.mark.skipif(GPU == "cpu", reason="no GPU")
@pytest.mark.parametrize("name", list(WORLDS))
def test_same_game_on_gpu(name):
    w = make(name, n=512, device=GPU, seed=0)
    scores = play_once(w, rand_player(GPU))
    assert scores.device.type == "cuda" and scores.shape == (512,) and torch.isfinite(scores).all()
