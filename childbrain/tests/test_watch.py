"""Watch it play: a saved brain's game is recorded move by move with what goes on inside it."""

import json

import pytest

torch = pytest.importorskip("torch")  # skipped under Nova's Python

from childbrain.brain.net import build, weights_to_json  # noqa: E402
from childbrain.watch import load_brain, main, record  # noqa: E402

GENES = {"kind": "plain", "hidden": 16, "layers": 1, "eyes": "grid", "channels": 4}


def _run(tmp_path, keep=True):
    folder = tmp_path / "20261121-100000_snake_t"
    folder.mkdir()
    torch.manual_seed(0)
    res = {"game": "snake", "genes": GENES, "exam_score_greedy": 1.0, "target": 5.0, "passed": False}
    if keep:
        res["weights"] = weights_to_json(build(GENES))
    (folder / "results.json").write_text(json.dumps(res), encoding="utf-8")
    (folder / "card.json").write_text(json.dumps({"name": "snake_t"}), encoding="utf-8")
    return folder


def test_records_every_move_with_the_brains_insides(tmp_path):
    data = record(_run(tmp_path), steps=25)
    assert data["game"] == "snake" and data["brain"]["name"] == "snake_t" and len(data["moves"]) == 25
    size = data["area"][2]
    for m in data["moves"]:
        allowed = [c for c, ok in zip(m["chance"], m["allowed"]) if ok]
        assert abs(sum(allowed) - 1) < 1e-3                       # chances over the allowed buttons
        assert all(c == 0 for c, ok in zip(m["chance"], m["allowed"]) if not ok)
        assert m["allowed"][m["pick"]] and m["chance"][m["pick"]] == max(m["chance"])  # plays like the exam
        assert len(m["neurons"]) == 16 and isinstance(m["guess"], float)
        assert len(m["focus"]) == size and max(max(r) for r in m["focus"]) <= 1.0
        assert all(0 <= k < 6 and 0 <= r < 12 and 0 <= c < 12 for k, r, c, _ in m["board"])


def test_same_brain_same_board_same_game(tmp_path):
    folder = _run(tmp_path)
    a, b = record(folder, steps=30), record(folder, steps=30)
    assert [m["pick"] for m in a["moves"]] == [m["pick"] for m in b["moves"]]


def test_refuses_without_weights_and_sealed_games(tmp_path):
    with pytest.raises(ValueError, match="weights"):
        load_brain(_run(tmp_path, keep=False))
    (tmp_path / "x").mkdir()
    folder = _run(tmp_path / "x")
    with pytest.raises(ValueError, match="practice game"):
        record(folder, game="key-door", steps=5)


def test_saves_where_asked(tmp_path):
    out = main(str(_run(tmp_path)), "catch", "new", 3, 12, str(tmp_path / "w.json"))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["game"] == "catch" and data["board"] == "new" and len(data["moves"]) == 12
