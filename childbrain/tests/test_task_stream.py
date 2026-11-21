"""WP 1.2: the task stream (one plain brain, games one after another), with tiny budgets on the CPU."""

import json

import pytest

from childbrain.measure.task_stream import CHART_FILE, ORDER, TABLE_FILE, task_stream
from childbrain.runcard import RESULTS_FILE, rerun, run

EXP = "childbrain.measure.task_stream:task_stream"
TINY = {"copies": 16, "horizon": 8, "hidden": 16, "channels": 4, "minibatches": 2, "epochs": 1,
        "curve_every": 16 * 8 * 2, "exam_copies": 64}


def tiny(games, **more):
    return {**TINY, "games": games, "budgets": {g: 16 * 8 * 3 for g in games}, **more}


def test_table_has_one_row_per_game_and_a_column_per_checkpoint(tmp_path):
    games = ["catch", "colour-match", "connect-4"]
    folder = run(EXP, tiny(games), seed=1, runs_dir=tmp_path)
    r = json.loads((folder / RESULTS_FILE).read_text(encoding="utf-8"))
    assert r["games"] == games
    assert r["checkpoints"] == ["start", "after catch", "after colour-match", "after connect-4"]
    assert set(r["exam_table"]) == set(games)
    assert all(len(row) == len(games) + 1 for row in r["exam_table"].values())
    for i, g in enumerate(games):
        p = r["per_game"][g]
        assert p["just_after"] == r["exam_table"][g][i + 1] and p["at_end"] == r["exam_table"][g][-1]
        target, floor = p["target"], p["random_floor"]
        assert p["forgetting"] == pytest.approx((p["just_after"] - p["at_end"]) / (target - floor))
    assert r["per_game"]["connect-4"]["forgetting"] == 0  # the last game can't be forgotten yet
    assert r["optimizer"] == "fresh" and r["total_moves"] == sum(r["moves_practised"].values())
    assert len(r["curve"]["steps"]) == len(r["curve"]["catch"]) > len(games) + 1
    assert (folder / CHART_FILE).stat().st_size > 1000
    assert "| catch |" in (folder / TABLE_FILE).read_text(encoding="utf-8")


def test_task_stream_run_repeats_exactly(tmp_path):
    folder = run(EXP, tiny(["catch", "snake"], optimizer="keep"), seed=3, runs_dir=tmp_path)
    report = rerun(folder)
    assert report.same, report.summary()


@pytest.mark.parametrize("maze", ["maze", "maze-coins"])
def test_mazes_are_refused(maze):
    with pytest.raises(ValueError, match="never reached its target"):
        task_stream({**task_stream.defaults, **tiny(["catch", maze])}, "cpu")


def test_bad_streams_are_refused():
    for games in ([], ["catch", "catch"], ["key-door"]):
        with pytest.raises(ValueError):
            task_stream({**task_stream.defaults, **tiny(games)}, "cpu")
    with pytest.raises(ValueError, match="budgets"):
        task_stream({**task_stream.defaults, **TINY, "games": ["catch"], "budgets": {"maze": 100}}, "cpu")


def test_default_stream_is_the_seven_games_that_passed_alone():
    assert task_stream.defaults["games"] == ORDER and len(ORDER) == 7
    assert not {"maze", "maze-coins"} & set(ORDER)
    assert task_stream.defaults["gamma"] == 0.9
