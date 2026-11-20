"""WP 0.4: sealed exam games are exam-only and stored with fingerprints."""

import json

import pytest

torch = pytest.importorskip("torch")  # skipped under Nova's Python

from childbrain.runcard import run
from childbrain.worlds import PRACTICE, SEALED, make, sealed
from childbrain.worlds.key_door import KeyDoor


def test_three_sealed_games_apart_from_practice():
    assert set(SEALED) == {"key-door", "pong-lite", "maze-enemy"}
    assert not set(SEALED) & set(PRACTICE)


@pytest.mark.parametrize("name", list(SEALED))
def test_sealed_games_refuse_practice(name):
    with pytest.raises(PermissionError):
        make(name, n=4, split="train")
    with pytest.raises(PermissionError):
        SEALED[name](n=4)  # building it directly defaults to practice, so it refuses
    assert make(name, n=4).split == "test"


def test_run_cards_cant_practice_on_sealed_games(tmp_path):
    with pytest.raises(PermissionError):
        run("childbrain.measure.random_play:random_play", {"game": "key-door", "copies": 8}, runs_dir=tmp_path)


def test_fingerprints_match_the_seal():
    assert sealed.verify() == [], "run `python -m childbrain verify`; re-seal only after checking the change"


def test_a_changed_rule_breaks_the_seal(monkeypatch):
    real = KeyDoor._step

    def easier(self, actions):
        reward, done = real(self, actions)
        return reward + 0.5, done  # a sneaky extra reward

    monkeypatch.setattr(KeyDoor, "_step", easier)
    assert "key-door: exam changed" in sealed.verify()


def test_a_changed_seal_file_is_noticed(tmp_path):
    saved = json.loads(sealed.SEAL_FILE.read_text(encoding="utf-8"))
    saved["pong-lite"]["code"] = "0" * 64
    del saved["maze-enemy"]
    fake = tmp_path / "sealed.json"
    fake.write_text(json.dumps(saved), encoding="utf-8")
    problems = sealed.verify(fake)
    assert "pong-lite: code changed" in problems and "maze-enemy: not sealed" in problems
    assert sealed.verify(tmp_path / "missing.json") == ["missing.json is missing"]
