"""WP 0.4: the rules of the 8 new games, the mixed games and the changed versions."""

import pytest

torch = pytest.importorskip("torch")  # skipped under Nova's Python

from childbrain.worlds import make, play_once, random_actions
from childbrain.worlds.base import DOWN, ENEMIES, GOAL, LEFT, ME, RIGHT, STAY, UP, USE
from childbrain.worlds.connect4 import four_in_a_row

A = torch.tensor


def one(name, **kw):
    return make(name, n=1, seed=0, **kw)


def me_at(obs, layer=ME):
    idx = int(obs[0, layer].flatten().argmax())
    return divmod(idx, obs.shape[-1])


# ---------- Catch ----------

def test_catch_follow_the_thing_catches_everything():
    w = make("catch", n=256, seed=0)
    follow = lambda obs, allowed: torch.where(w.thing[:, 1] > w.me, RIGHT, torch.where(w.thing[:, 1] < w.me, LEFT, STAY))  # noqa: E731
    assert (play_once(w, follow) == w.drops).all()


def test_catch_miss_costs_one():
    w = one("catch")
    w.me[0], w.thing[0] = 0, A([w.size - 2, 5])
    assert w.step(A([STAY])).reward.item() == -1


# ---------- Push-the-box ----------

def test_push_box_pushes_and_scores():
    w = one("push-box")
    w.me[0], w.box[0], w.target[0] = A([3, 1]), A([3, 2]), A([3, 4])
    w.step(A([RIGHT]))
    assert w.box[0].tolist() == [3, 3] and w.me[0].tolist() == [3, 2]
    res = w.step(A([RIGHT]))
    assert res.reward.item() == 1 and res.done.item()


def test_push_box_cant_push_into_wall():
    w = one("push-box")
    w.me[0], w.box[0], w.target[0] = A([3, 4]), A([3, 5]), A([1, 1])
    w.step(A([RIGHT]))
    assert w.box[0].tolist() == [3, 5] and w.me[0].tolist() == [3, 4]


# ---------- Key-and-door (sealed) ----------

def test_key_door_needs_the_key():
    w = one("key-door")
    mid = w.mid
    w.door[0], w.me[0], w.key[0], w.goal[0] = A([4, mid]), A([4, mid - 1]), A([2, 1]), A([4, mid + 1])
    w.step(A([RIGHT]))
    assert w.me[0].tolist() == [4, mid - 1], "the door is shut without the key"
    assert not w.allowed()[0, USE]
    w.me[0] = A([2, 1])
    assert w.allowed()[0, USE], "'use' is allowed when standing on the key"
    w.step(A([USE]))
    assert w.has_key[0]
    w.me[0] = A([4, mid - 1])
    w.step(A([RIGHT]))
    res = w.step(A([RIGHT]))
    assert res.reward.item() == 1 and res.done.item()


# ---------- Avoid-the-enemy ----------

def test_avoid_caught_costs_one_and_ends():
    w = one("avoid-enemy")
    w.me[0], w.enemies[0] = A([3, 3]), A([[3, 4], [7, 7]])
    res = w.step(A([RIGHT]))
    assert res.reward.item() == -1 and res.done.item()


def test_avoid_surviving_the_whole_episode_is_worth_one():
    w = make("avoid-enemy", n=64, seed=0, chase=0.0)
    w.enemies[:] = A([7, 7])
    w.me[:] = A([0, 0])
    total = torch.zeros(64)
    for _ in range(w.max_steps):
        res = w.step(torch.full((64,), STAY))
        total += res.reward
    survived = res.info["timeout"]
    assert torch.allclose(total[survived], torch.ones(int(survived.sum())))


# ---------- Collect-coins and Colour-match ----------

def test_coins_greedy_collects_all():
    w = make("collect-coins", n=256, seed=0)

    def greedy(obs, allowed):
        cells = w.coins.flatten(1).float()
        r = torch.arange(w.size).repeat_interleave(w.size)
        c = torch.arange(w.size).repeat(w.size)
        dist = (r[None] - w.me[:, :1]).abs() + (c[None] - w.me[:, 1:]).abs()
        target = torch.where(cells > 0, dist.float(), 1e9).argmin(1)
        tr, tc = target // w.size, target % w.size
        return torch.where(tr < w.me[:, 0], UP, torch.where(tr > w.me[:, 0], DOWN,
                           torch.where(tc < w.me[:, 1], LEFT, RIGHT)))

    assert (play_once(w, greedy) == w.k).all()


def test_colour_match_right_and_wrong_tiles():
    w = one("colour-match")
    w.a.zero_(), w.b.zero_()
    w.a[0, 3, 4], w.a[0, 6, 6], w.b[0, 3, 2] = True, True, True
    w.me[0], w.want_a[0] = A([3, 3]), True
    obs = w.observe()
    assert obs[0, GOAL, :8, :8].all(), "goal layer lit = colour A wanted"
    assert w.step(A([LEFT])).reward.item() == -1
    w.me[0] = A([3, 3])
    res = w.step(A([RIGHT]))
    assert res.reward.item() == 1 and not res.done.item()


# ---------- Pong-lite (sealed) ----------

def landing_column(w):
    """Where the ball will be when it reaches the row above the paddle (following its bounces)."""
    s = w.size
    r, c = w.ball[:, 0].clone(), w.ball[:, 1].clone()
    vr, vc = w.vel[:, 0].clone(), w.vel[:, 1].clone()
    land = torch.full_like(c, -1)
    for _ in range(3 * s):
        vc = torch.where((c + vc < 0) | (c + vc >= s), -vc, vc)
        vr = torch.where(r + vr < 0, 1, vr)
        arrive = (r + vr == s - 1) & (vr == 1) & (land < 0)
        land = torch.where(arrive, c + vc, land)
        vr = torch.where(r + vr == s - 1, -1, vr)
        r, c = r + vr, c + vc
    return land


def test_pong_is_always_winnable():
    w = make("pong-lite", n=256, seed=0)
    hits = torch.zeros(256)
    for _ in range(w.max_steps - 1):
        target = landing_column(w).clamp(1, w.size - 2)
        a = torch.where(target > w.paddle, RIGHT, torch.where(target < w.paddle, LEFT, STAY))
        res = w.step(a)
        assert not (res.reward < 0).any(), "a paddle that goes where the ball will land never misses"
        hits += res.reward
    assert (hits >= 5).all()
    g = torch.Generator().manual_seed(0)
    assert play_once(make("pong-lite", n=256, seed=0), lambda o, al: random_actions(al, g)).mean() < 0.5


# ---------- Connect-4 ----------

def test_four_in_a_row():
    b = torch.zeros(4, 6, 7, dtype=torch.bool)
    b[0, 5, 1:5] = True              # across
    b[1, 2:6, 3] = True              # down
    for i in range(4):
        b[2, 5 - i, i] = True        # diagonal
    b[3, 5, 0:3] = True              # only three
    assert four_in_a_row(b).tolist() == [True, True, True, False]


def test_connect4_self_play_drop_win_and_full_column():
    w = one("connect-4", opponent="self")
    w.cursor[0] = 0
    for i in range(3):                       # player 1 builds in column 0, player 2 answers in column 1
        assert w.step(A([USE])).reward.item() == 0
        w.cursor[0] = 1
        res = w.step(A([USE]))
        assert res.info["player"].item() == 2
        w.cursor[0] = 0
    obs = w.observe()
    assert obs[0, ME, 1:7, 0].sum() == 3, "drawn from the side of whoever moves next"
    res = w.step(A([USE]))
    assert res.reward.item() == 1 and res.done.item()
    w.board[0, :, 2] = 1
    w.cursor[0] = 2
    assert not w.allowed()[0, USE], "can't drop into a full column"


def test_connect4_random_opponent_answers():
    w = make("connect-4", n=64, seed=1)
    before = (w.board != 0).flatten(1).sum(1)
    w.step(torch.full((64,), USE))
    after = (w.board != 0).flatten(1).sum(1)
    assert ((after - before) == 2).all(), "my disc + the random player's reply"


# ---------- mixed games ----------

def test_maze_coins_pays_for_coins_on_the_way():
    w = one("maze-coins")
    w.walls.fill_(True)
    w.walls[0, 1, 1:6] = False
    w.pos[0], w.goal[0] = A([1, 1]), A([1, 5])
    w.coins.zero_()
    w.coins[0, 1, 3] = True
    rewards = [w.step(A([RIGHT])).reward.item() for _ in range(4)]
    assert rewards == pytest.approx([0, 0.2, 0, 1])


def test_maze_enemy_is_sealed_uses_exam_mazes_and_bites():
    w = make("maze-enemy", n=128, seed=0)
    assert w.split == "test"
    for _ in range(50):
        w.step(random_actions(w.allowed(), torch.Generator().manual_seed(1)))
        assert not w.walls[w.rows, w.enemy[:, 0, 0], w.enemy[:, 0, 1]].any(), "enemies don't walk through walls"
    w = one("maze-enemy")
    w.walls.fill_(True)
    w.walls[0, 1, 1:6] = False
    w.pos[0], w.goal[0], w.enemy[0, 0] = A([1, 1]), A([1, 5]), A([1, 2])
    res = w.step(A([RIGHT]))
    assert res.reward.item() == -1 and res.done.item()
    assert w.observe()[0, ENEMIES].sum() == 1


# ---------- changed versions ----------

def test_look_changed_up_still_moves_up_on_screen():
    for seed in range(8):
        w = make("collect-coins", n=1, seed=0, change="look", change_seed=seed)
        me = int((w.layers == ME).nonzero())
        r0, c0, s = w.area
        for button, (dr, dc) in ((UP, (-1, 0)), (DOWN, (1, 0)), (LEFT, (0, -1)), (RIGHT, (0, 1))):
            before = me_at(w.observe(), me)
            inside = r0 < before[0] < r0 + s - 1 and c0 < before[1] < c0 + s - 1
            after = me_at(w.step(A([button])).obs, me)
            if inside and not w.world.t.item() == 0:
                assert (after[0] - before[0], after[1] - before[1]) == (dr, dc), f"look seed {seed}"


def test_look_changed_keeps_the_score():
    plain = make("collect-coins", n=512, seed=4)
    looked = make("collect-coins", n=512, seed=4, change="look", change_seed=3, size=8)
    g1, g2 = torch.Generator().manual_seed(0), torch.Generator().manual_seed(0)
    a = play_once(plain, lambda o, al: random_actions(al, g1)).mean()
    b = play_once(looked, lambda o, al: random_actions(al, g2)).mean()
    assert abs(a - b) < 0.4, "same rules, so a random player scores about the same"
    assert not torch.equal(plain.observe(), looked.observe())


def test_rules_changed_looks_the_same_but_plays_differently():
    a = make("collect-coins", n=64, seed=2)
    b = make("collect-coins", n=64, seed=2, change="controls", change_seed=0)
    assert torch.equal(a.observe(), b.observe()), "same look"
    assert not torch.equal(b.to_inner[:4], torch.arange(4)), "buttons shuffled"
    ra, rb = a.step(torch.full((64,), UP)), b.step(torch.full((64,), UP))
    assert not torch.equal(ra.obs, rb.obs)
    c = make("collect-coins", n=64, seed=2, change="reward")
    d = make("collect-coins", n=64, seed=2)
    for _ in range(20):
        rc, rd = c.step(torch.full((64,), RIGHT)), d.step(torch.full((64,), RIGHT))
        assert torch.equal(rc.reward, -rd.reward) and torch.equal(rc.obs, rd.obs)
