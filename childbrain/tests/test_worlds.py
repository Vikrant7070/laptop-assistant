"""WP 0.3: Snake and Maze (the shared format tests for every game are in test_format.py).

Done when: 10,000 Snake steps run in well under a second, and a random player gets the expected low score.
"""

import random
import time

import pytest

torch = pytest.importorskip("torch")  # skipped under Nova's Python

from childbrain.runcard import rerun, run
from childbrain.worlds import Maze, Snake, play_once, random_actions
from childbrain.worlds.base import DOWN, EXTRA, ITEMS, LEFT, ME, RIGHT, STAY, UP
from childbrain.worlds.maze import maze_bank

GPU = "cuda" if torch.cuda.is_available() else "cpu"


def rand_player(device="cpu", seed=0):
    g = torch.Generator(device=device)
    g.manual_seed(seed)
    return lambda obs, allowed: random_actions(allowed, g)


# ---------- speed ----------

def test_10000_snake_steps_well_under_a_second():
    w = Snake(n=1000, device=GPU, seed=0)
    play = rand_player(GPU)
    for _ in range(5):  # warm-up
        w.step(play(None, w.allowed()))
    if GPU == "cuda":
        torch.cuda.synchronize()
    start = time.perf_counter()
    for _ in range(10):  # 1,000 copies x 10 steps = 10,000 steps
        w.step(play(None, w.allowed()))
    if GPU == "cuda":
        torch.cuda.synchronize()
    took = time.perf_counter() - start
    print(f"10,000 Snake steps on {GPU}: {took * 1000:.1f} ms")
    assert took < 0.5  # usually ~30 ms; generous because Nova may be using the GPU at the same time


# ---------- Snake rules ----------

def snake_at(head, direction, food=(7, 7), size=8):
    w = Snake(n=1, size=size, seed=0)
    w.body.zero_()
    w.head[0] = torch.tensor(head)
    w.body[0, head[0], head[1]] = 1
    w.length[0], w.direction[0] = 1, direction
    w.food[0] = torch.tensor(food)
    return w


def test_snake_dies_at_the_edge():
    w = snake_at((0, 3), UP)
    res = w.step(torch.tensor([STAY]))
    assert res.reward.item() == -1 and res.done.item()


def test_snake_eats_grows_and_new_food_is_not_on_it():
    w = snake_at((3, 3), RIGHT, food=(3, 4))
    res = w.step(torch.tensor([STAY]))
    assert res.reward.item() == 1 and not res.done.item()
    assert w.length.item() == 2 and (w.body > 0).sum().item() == 2
    assert w.body[0, w.food[0, 0], w.food[0, 1]].item() == 0
    assert not w.allowed()[0, LEFT], "can't turn straight back into your own body"
    assert res.obs[0, ITEMS].sum() == 1 and res.obs[0, EXTRA].max() == 1


def test_snake_tail_moves_on():
    w = snake_at((3, 3), RIGHT, food=(3, 4))
    w.step(torch.tensor([STAY]))            # eat: length 2 at (3,3),(3,4)
    w.food[0] = torch.tensor([7, 7])
    w.step(torch.tensor([DOWN]))            # head (4,4); tail leaves (3,3)
    assert w.body[0, 3, 3].item() == 0 and w.body[0, 3, 4].item() == 1 and w.body[0, 4, 4].item() == 2


def test_snake_bites_itself():
    w = snake_at((3, 3), RIGHT)
    w.body[0, 3, 3], w.length[0] = 0, 5
    # body: (3,3)=5 head, (4,3)=4, (4,4)=3, (3,4)=2 ... moving RIGHT from (3,3) hits (3,4)
    w.body[0, 3, 3], w.body[0, 4, 3], w.body[0, 4, 4], w.body[0, 3, 4] = 5, 4, 3, 2
    res = w.step(torch.tensor([RIGHT]))
    assert res.reward.item() == -1 and res.done.item()


def test_snake_cant_loop_forever_without_food():
    w = Snake(n=8, seed=0, hunger_limit=10, max_steps=1000)
    w.food[:] = torch.tensor([7, 7])
    # Run in a tight circle; each copy should end by hunger within 10 steps, never later.
    w.body.zero_()
    w.head[:] = torch.tensor([2, 2])
    w.body[:, 2, 2], w.length[:], w.direction[:] = 1, 1, RIGHT
    circle = [RIGHT, DOWN, LEFT, UP]
    ended_at = None
    for t in range(12):
        res = w.step(torch.full((8,), circle[t % 4]))
        if res.done.all():
            ended_at = t + 1
            break
    assert ended_at == 10


# ---------- Maze rules ----------

def test_maze_wall_blocks_and_goal_scores():
    w = Maze(n=1, seed=0)
    w.walls.fill_(True)
    w.walls[0, 1, 1:4] = False          # a corridor (1,1)-(1,3)
    w.pos[0], w.goal[0] = torch.tensor([1, 1]), torch.tensor([1, 3])
    assert w.step(torch.tensor([UP])).obs[0, ME, 1, 1] == 1       # wall: didn't move
    assert w.step(torch.tensor([RIGHT])).reward.item() == 0
    res = w.step(torch.tensor([RIGHT]))
    assert res.reward.item() == 1 and res.done.item()


def test_maze_banks_are_perfect_mazes_and_exam_mazes_are_unseen():
    train, test = maze_bank(11, "train"), maze_bank(11, "test")
    assert train.shape == test.shape == (8192, 11, 11)
    assert train[:, 0].all() and train[:, :, 0].all()           # border walls
    open_cells = (~train[:16]).flatten(1).sum(1)
    assert (open_cells == 2 * 25 - 1).all()                     # a tree: 25 rooms + 24 doors
    key = lambda m: bytes(m.flatten().to(torch.uint8).tolist())  # noqa: E731
    seen = {key(m) for m in train}
    assert not any(key(m) in seen for m in test), "an exam maze (or a rotation of it) is in the practice bank"
    assert len({key(m) for m in train[:1024]}) == 1024, "no repeats inside a bank"


def shortest_path_player(w: Maze):
    """Perfect player for the mazes as they are now (first episode only):
    walks the shortest path, using distances spread out from the goal."""
    s, inf = w.size, 10_000
    dist = torch.full((w.n, s, s), inf, device=w.device)
    dist[w.rows, w.goal[:, 0], w.goal[:, 1]] = 0
    for _ in range(s * s):
        p = torch.nn.functional.pad(dist, (1, 1, 1, 1), value=inf)
        near = torch.stack([p[:, :-2, 1:-1], p[:, 2:, 1:-1], p[:, 1:-1, :-2], p[:, 1:-1, 2:]]).amin(0) + 1
        dist = torch.where(w.walls, inf, torch.minimum(dist, near))

    def act(obs, allowed):
        nxt = (w.pos[:, None, :] + w.moves[:4][None]).clamp(0, s - 1)
        d = dist[w.rows[:, None], nxt[..., 0], nxt[..., 1]]
        return d.argmin(1)

    return act


def test_maze_is_solvable_and_perfect_play_wins():
    w = Maze(n=256, seed=0)
    assert (play_once(w, shortest_path_player(w), reset=False) == 1).all()


# ---------- the random player gets the expected low score ----------

def exact_random_maze_score(w: Maze) -> float:
    """Exact chance that a random player reaches the goal in time (probability spread, no dice)."""
    s = w.size
    prob = torch.zeros(w.n, s, s, dtype=torch.float64)
    prob[w.rows, w.pos[:, 0], w.pos[:, 1]] = 1
    won = torch.zeros(w.n, dtype=torch.float64)
    open_ = ~w.walls
    for _ in range(w.max_steps):
        new = prob / 5  # "stay"
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            target_open = torch.roll(open_, shifts=(-dr, -dc), dims=(1, 2))  # is the cell I'd move to open?
            moving = prob / 5 * target_open
            new = new + torch.roll(moving, shifts=(dr, dc), dims=(1, 2)) + prob / 5 * ~target_open
        won += new[w.rows, w.goal[:, 0], w.goal[:, 1]]
        new[w.rows, w.goal[:, 0], w.goal[:, 1]] = 0
        prob = new
    return won.mean().item()


def test_random_maze_score_matches_the_exact_chance():
    w = Maze(n=4096, seed=1)
    expected = exact_random_maze_score(w)
    got = play_once(w, rand_player(seed=1), reset=False).mean().item()
    print(f"maze random player: {got:.3f}, exact chance {expected:.3f}")
    assert 0.05 < expected < 0.35, "random play should rarely find the goal"
    assert abs(got - expected) < 0.03


def reference_snake_random_score(episodes=4000, size=8, max_steps=200, seed=0) -> float:
    """Plain-Python Snake with the same rules, written separately, to check the GPU version."""
    rng = random.Random(seed)
    moves = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    reverse = [1, 0, 3, 2]
    total = 0
    for _ in range(episodes):
        body = [(rng.randrange(size), rng.randrange(size))]  # head first
        direction = rng.randrange(4)
        empty = [(r, c) for r in range(size) for c in range(size) if (r, c) not in body]
        food = rng.choice(empty)
        score = hunger = 0
        for _ in range(max_steps):
            choices = [a for a in range(5) if not (a < 4 and len(body) > 1 and a == reverse[direction])]
            a = rng.choice(choices)
            direction = a if a < 4 else direction
            r, c = body[0][0] + moves[direction][0], body[0][1] + moves[direction][1]
            if not (0 <= r < size and 0 <= c < size):
                score -= 1
                break
            eat = (r, c) == food
            if not eat:
                body.pop()
            if (r, c) in body:
                score -= 1
                break
            body.insert(0, (r, c))
            hunger = 0 if eat else hunger + 1
            if eat:
                score += 1
                if len(body) == size * size:
                    break
                food = rng.choice([(i, j) for i in range(size) for j in range(size) if (i, j) not in body])
            if hunger >= 2 * size * size:
                break
        total += score
    return total / episodes


def test_random_snake_score_matches_a_plain_python_snake():
    expected = reference_snake_random_score()
    got = play_once(Snake(n=4096, seed=2), rand_player(seed=2)).mean().item()
    print(f"snake random player: {got:.3f}, plain-Python snake {expected:.3f}")
    assert expected < -0.5, "a random snake mostly crashes before eating"
    assert abs(got - expected) < 0.05


def test_greedy_snake_beats_random():
    def greedy(w):
        def act(obs, allowed):
            new = w.head[:, None, :] + w.moves[:4][None]
            inside = ((new >= 0) & (new < w.size)).all(2)
            c = new.clamp(0, w.size - 1)
            safe = inside & (w.body[w.rows[:, None], c[..., 0], c[..., 1]] <= 1) & allowed[:, :4]
            dist = (new - w.food[:, None, :]).abs().sum(2).float()
            return torch.where(safe, -dist, -1e9).argmax(1)
        return act

    w = Snake(n=1024, seed=3)
    smart = play_once(w, greedy(w)).mean().item()
    dumb = play_once(Snake(n=1024, seed=3), rand_player(seed=3)).mean().item()
    print(f"snake greedy {smart:.2f} vs random {dumb:.2f}")
    assert smart > dumb + 5


# ---------- run cards work with games ----------

def test_random_play_run_repeats(tmp_path):
    for game in ("snake", "maze"):
        folder = run("childbrain.measure.random_play:random_play", {"game": game, "copies": 512},
                     seed=11, runs_dir=tmp_path)
        report = rerun(folder)
        assert report.same, report.differences
