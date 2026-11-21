"""The Child brain tab: finds the child brain's folder, lists its runs, and its controls (start, finish, kill,
off switch) do what they say - without ever importing the child brain (it has its own Python)."""
import json
import sys
from pathlib import Path

import pytest
from werkzeug.exceptions import HTTPException

from nova.core.app import childbrain_tab as cb, config


def _fake_childbrain(folder, learner=True, python=False, runs=()):
    (folder / "childbrain" / "measure").mkdir(parents=True)
    (folder / "childbrain" / "__init__.py").write_text("", encoding="utf-8")
    if learner:
        (folder / "childbrain" / "measure" / "learn_alone.py").write_text("", encoding="utf-8")
    if python:
        (folder / cb.VENV).parent.mkdir(parents=True)
        (folder / cb.VENV).write_text("", encoding="utf-8")
    for name, card, results in runs:
        d = folder / "childbrain" / "runs" / name
        d.mkdir(parents=True)
        (d / "card.json").write_text(json.dumps(card), encoding="utf-8")
        if results is not None:
            (d / "results.json").write_text(json.dumps(results), encoding="utf-8")
    return folder


CARD = {"experiment": "childbrain.measure.learn_alone:learn_alone", "name": "snake_a", "created": "2026-11-21T10:00:00",
        "seed": 0, "device": "cuda", "settings": {"game": "snake", "gamma": 0.99}}
RES = {"game": "snake", "target": 5.0, "random_floor": -0.88, "exam_score_greedy": 6.1, "passed": True,
       "steps_practised": 1000, "clock_seconds": 12.0, "checks_steps": [100, 200], "checks_score": [1.0, 5.5],
       "practice_steps": [50, 100], "practice_score": [0.1, None], "weights": {"big": [1] * 10}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "nova"
    root.mkdir()
    monkeypatch.setattr(config, "ROOT", root)
    store = {}
    monkeypatch.setattr(config, "settings", lambda: dict(store))
    monkeypatch.setattr(config, "save_settings", lambda s: store.update(s))
    monkeypatch.setattr(cb, "info", lambda h: {"experiments": {"learn-alone": {"defaults": {"game": "snake"}}},
                                               "games": {"snake": {"target": 5.0, "floor": -0.88, "best": "+17"},
                                                         "maze": {"target": 0.7, "floor": 0.16, "best": "1"}}})
    cb._summary_cache.clear()
    return root


def test_picks_the_folder_with_the_learner_and_python(setup):
    _fake_childbrain(setup, learner=False)
    wt = _fake_childbrain(setup / ".claude" / "worktrees" / "cb", python=True)
    assert cb.home() == wt
    assert cb.python_exe() == wt / cb.VENV
    config.save_settings({"childbrain_home": str(setup)})           # a person can pick another copy
    assert cb.home() == setup and cb.python_exe() == wt / cb.VENV   # its code, the other copy's Python


def test_status_runs_scoreboard_and_detail(setup, client):
    _fake_childbrain(setup, python=True, runs=[("20261121-100000_snake_a", CARD, RES),
                                               ("20261121-110000_maze_b", dict(CARD, name="maze_b"), None)])
    s = client.get("/api/childbrain/status").get_json()
    assert s["found"] and not s["off_switch"] and s["job"] is None
    o = client.get("/api/childbrain/overview").get_json()
    assert [r["run"] for r in o["runs"]] == ["20261121-110000_maze_b", "20261121-100000_snake_a"]
    assert o["runs"][0]["done"] is False and o["runs"][1]["exam"] == 6.1
    board = {b["game"]: b for b in o["board"]}
    assert board["snake"]["passed"] and board["snake"]["best_exam"] == 6.1 and board["maze"]["runs"] == 0
    d = client.get("/api/childbrain/run/20261121-100000_snake_a").get_json()
    assert d["checks"] == [[100, 1.0], [200, 5.5]] and d["practice"] == [[50, 0.1]]
    assert "weights" not in d["results"]
    assert client.get("/api/childbrain/run/..").status_code == 400
    assert client.get("/api/childbrain/run/nope").status_code == 404


def test_train_builds_the_command_and_checks_input(setup, client, monkeypatch):
    _fake_childbrain(setup, python=True)
    started = []
    monkeypatch.setattr(cb, "_start", lambda h, kind, label, args: started.append((kind, label, args)) or {"ok": 1})
    monkeypatch.setattr(cb, "gpu", lambda: {"ok": True, "free": True})
    r = client.post("/api/childbrain/train", json={"experiment": "learn-alone", "device": "cuda", "seed": 3,
                                                   "name": "try2", "settings": {"game": "snake", "gamma": 0.9}})
    assert r.status_code == 200
    kind, label, args = started[0]
    assert kind == "train" and "snake" in label
    assert args == ["-m", "childbrain", "run", "learn-alone", "--device", "cuda", "--seed", "3", "--name", "try2",
                    "--set", 'game="snake"', "gamma=0.9"]
    assert client.post("/api/childbrain/train", json={"settings": {"bad key": 1}}).status_code == 400
    assert client.post("/api/childbrain/train", json={"name": "../x"}).status_code == 400
    assert client.post("/api/childbrain/train", json={"experiment": "rm -rf"}).status_code == 400


def test_busy_graphics_card_asks_first(setup, client, monkeypatch):
    _fake_childbrain(setup, python=True)
    monkeypatch.setattr(cb, "_start", lambda *a: {"ok": 1})
    monkeypatch.setattr(cb, "gpu", lambda: {"ok": True, "free": False, "why": "90% busy"})
    r = client.post("/api/childbrain/train", json={"settings": {"game": "snake"}})
    assert r.status_code == 409 and r.get_json()["gpu_busy"]
    assert client.post("/api/childbrain/train", json={"force": True, "settings": {"game": "snake"}}).status_code == 200


def test_a_real_job_is_killed_and_the_off_switch_blocks_new_ones(setup, client, monkeypatch):
    """A real process: started, seen running, killed; the off switch then refuses new jobs until cleared."""
    h = _fake_childbrain(setup, python=True)
    monkeypatch.setattr(cb, "python_exe", lambda h=None: Path(sys.executable))
    cb._start(h, "train", "training snake", ["-c", "import time; print('run folder nowhere', flush=True); time.sleep(30)"])
    assert cb.current_job(h)["running"]
    with pytest.raises(HTTPException):
        cb._start(h, "nightly", "x", ["-c", "pass"])                # one job at a time
    assert client.post("/api/childbrain/kill").get_json()["ok"]
    assert not cb.current_job(h)["running"] and cb.current_job(h)["state"] == "killed"
    monkeypatch.setattr(cb, "_childbrain_processes", lambda: [])
    monkeypatch.setattr(cb.time, "sleep", lambda s: None)
    assert client.post("/api/childbrain/off").get_json()["ok"]
    assert (h / "childbrain" / "runs" / "STOP").exists() and client.get("/api/childbrain/status").get_json()["off_switch"]
    assert client.post("/api/childbrain/nightly").status_code == 409
    assert "cleared" in client.post("/api/childbrain/clear").get_json()["msg"]
    assert not (h / "childbrain" / "runs" / "STOP").exists()


def test_finish_writes_the_finish_file(setup, client, monkeypatch):
    h = _fake_childbrain(setup, python=True, runs=[("20261121-120000_x", CARD, None)])
    folder = h / "childbrain" / "runs" / "20261121-120000_x"
    monkeypatch.setattr(cb, "current_job", lambda h=None: {"running": True, "kind": "train", "folder": str(folder),
                                                           "label": "t", "pid": 1})
    assert client.post("/api/childbrain/finish").status_code == 200
    assert (folder / "FINISH").exists()


def test_page_is_served(client):
    assert client.get("/childbrain").status_code == 200
