"""WP 0.6: nightly exam + saved versions + rollback.

Done when: a deliberately damaged brain fails the exam and is rolled back automatically.
"""

import hashlib
import json

import pytest

torch = pytest.importorskip("torch")  # skipped under Nova's Python

from childbrain.brain.net import build, weights_to_json
from childbrain.guard import nightly as nightly_mod, policy
from childbrain.guard.exam import examine, run_exam
from childbrain.guard.log import check_chain
from childbrain.guard.nightly import nightly, rollback
from childbrain.guard.server import Guard
from childbrain.guard.versions import VersionStore
from childbrain.tests.brains import catch_follower
from childbrain.worlds.base import LEFT, RIGHT

LINEAR = {"kind": "linear"}


def good():
    return weights_to_json(catch_follower())


def blank():
    net = build(LINEAR)
    with torch.no_grad():
        for p in net.parameters():
            p.zero_()
    return weights_to_json(net)


def swapped(weights):
    """Damage: left and right are swapped, so it runs away from what it should catch."""
    w = json.loads(json.dumps(weights))
    shape, flat = w["1.weight"]
    n = shape[1]
    rows = [flat[i * n:(i + 1) * n] for i in range(shape[0])]
    rows[LEFT], rows[RIGHT] = rows[RIGHT], rows[LEFT]
    w["1.weight"] = [shape, [x for r in rows for x in r]]
    return w


def with_nans(weights):
    w = json.loads(json.dumps(weights))
    w["1.bias"][1] = [float("nan")] * len(w["1.bias"][1])
    return w


def quick_exam(folder):
    return examine(folder, copies=32)


# ---------- the "done when": a damaged brain fails the exam and is rolled back ----------

def test_damaged_brain_fails_the_exam_and_is_rolled_back(tmp_path):
    store = VersionStore(tmp_path / "versions")

    # Night 1: a blank brain is the first version (nothing to compare with yet).
    store.submit(LINEAR, blank(), note="blank")
    n1 = nightly(store)
    assert n1["kept"] == ["0001"] and n1["current"] == "0001"

    # Night 2: a better brain (catches everything in Catch) replaces it.
    store.submit(LINEAR, good(), note="catch follower")
    n2 = nightly(store)
    assert n2["kept"] == ["0002"] and store.card("0002")["exam"]["items"]["catch"] == 10.0

    # Night 3: a damaged copy (left and right swapped) is handed in. It's worse, so it's thrown away.
    store.submit(LINEAR, swapped(good()), note="damaged")
    n3 = nightly(store)
    assert n3["thrown_away"][0]["version"] == "0003" and "worse" in n3["thrown_away"][0]["why"]
    assert store.current == "0002"

    # Night 4: the version in use gets damaged in place (and someone even fixes up its fingerprints
    # so the files look untouched). Its exam score changes, so it's rolled back to the last good one.
    folder = store.path("0002")
    (folder / "weights.json").write_text(json.dumps(swapped(good())), encoding="utf-8")
    card = store.card("0002")
    card["files"]["weights.json"] = hashlib.sha256((folder / "weights.json").read_bytes()).hexdigest()
    (folder / "card.json").write_text(json.dumps(card), encoding="utf-8")
    n4 = nightly(store)
    print("\n".join((store.root / "report.txt").read_text().splitlines()))
    assert n4["rolled_back"]["from"] == "0002" and n4["rolled_back"]["to"] == "0001"
    assert "exam score changed" in n4["rolled_back"]["why"]
    assert store.current == "0001" and store.card("0002")["status"] == "damaged"
    assert "rolled back: yes (0002 -> 0001)" in n4["line"]
    assert check_chain(store.root / "nightly_log.jsonl") == []


def test_exam_runs_in_its_own_process_and_repeats_exactly(tmp_path):
    store = VersionStore(tmp_path / "v")
    vid = store.submit(LINEAR, good())
    a, b = run_exam(store.path(vid)), run_exam(store.path(vid))
    assert a["broken"] is None and a["items"] == b["items"] and a["items"]["catch"] == 10.0
    assert set(a["items"]) >= {"key-door", "pong-lite", "maze-enemy"}, "the sealed games are in the exam"


# ---------- more ways to fail ----------

def test_brain_that_gives_nonsense_fails(tmp_path):
    store = VersionStore(tmp_path / "v")
    store.submit(LINEAR, good())
    nightly(store, exam=quick_exam)
    store.submit(LINEAR, with_nans(good()))
    night = nightly(store, exam=quick_exam)
    assert "aren't all numbers" in night["thrown_away"][0]["why"] and store.current == "0001"


def test_changed_files_are_noticed_and_the_version_in_use_is_rolled_back(tmp_path):
    store = VersionStore(tmp_path / "v")
    store.submit(LINEAR, blank())
    nightly(store, exam=quick_exam)
    store.submit(LINEAR, good())
    nightly(store, exam=quick_exam)
    (store.path("0002") / "weights.json").write_text(json.dumps(with_nans(good())), encoding="utf-8")
    night = nightly(store, exam=quick_exam)
    assert night["rolled_back"] == {"from": "0002", "to": "0001", "why": "its files changed after it was saved"}


def test_a_version_made_by_a_rule_breaking_run_is_thrown_away(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "guard_summary.json").write_text(json.dumps({"stopped": "too many blocked actions"}))
    store = VersionStore(tmp_path / "v")
    store.submit(LINEAR, good(), source=str(run))
    night = nightly(store, exam=quick_exam)
    assert "broke a guard rule" in night["thrown_away"][0]["why"] and store.current is None


def test_two_failed_nights_drop_the_trust_level(tmp_path):
    store = VersionStore(tmp_path / "v")
    store.submit(LINEAR, good())
    nightly(store, exam=quick_exam)
    for _ in range(2):
        store.submit(LINEAR, swapped(good()))
        night = nightly(store, exam=quick_exam)
    assert store.state()["level_drops"] == 1 and "trust level drops" in night["line"]


def test_no_exam_when_the_seals_are_broken(tmp_path, monkeypatch):
    monkeypatch.setattr(nightly_mod.seals, "verify", lambda: ["key-door: exam changed"])
    store = VersionStore(tmp_path / "v")
    store.submit(LINEAR, good())
    night = nightly(store, exam=quick_exam)
    assert "no exam tonight" in night["line"] and store.current is None and store.candidates() == ["0001"]


def test_a_person_can_go_back_to_any_saved_version(tmp_path):
    store = VersionStore(tmp_path / "v")
    store.submit(LINEAR, blank())
    nightly(store, exam=quick_exam)
    store.submit(LINEAR, good())
    nightly(store, exam=quick_exam)
    assert rollback("0001", store) == "in use: 0001 (was 0002)" and store.current == "0001"
    with pytest.raises(ValueError):
        rollback("0099", store)


# ---------- the brain hands in versions only through the guard ----------

def test_brain_submits_a_version_through_the_guard_and_the_night_keeps_it(tmp_path):
    s = Guard(policy=policy.load(), runs_dir=tmp_path).run("childbrain.tests.brains:trainer")
    assert s.stopped == "brain finished" and s.allowed.get("submit") == 1, open(f"{s.folder}/brain_output.txt").read()
    store = VersionStore(tmp_path / "versions")
    assert store.candidates() == ["0001"] and store.card("0001")["source"] == s.folder
    night = nightly(store, exam=quick_exam)
    assert night["kept"] == ["0001"]


def test_submitting_garbage_is_refused(tmp_path):
    g = Guard(policy=policy.load(), runs_dir=tmp_path)
    g.files = tmp_path / "files"
    g.files.mkdir()
    g.folder = tmp_path
    (g.files / "bad.json").write_text(json.dumps({"genes": {"kind": "rocket"}, "weights": {}}))
    with pytest.raises(ValueError):  # in a real run the guard turns this into a refusal ("bad arguments")
        g._do_submit({"file": "bad.json"})
    ok, reason, _ = g._do_submit({"file": "../escape.json"})
    assert not ok and "saved earlier" in reason
