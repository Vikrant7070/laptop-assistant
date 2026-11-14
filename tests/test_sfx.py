"""Reaction sound effects (sfx.py) and the mic hold that keeps Nova from hearing them."""
import json
import wave

import numpy as np

import sfx
import voice
from shared import DATA, REACTIONS


def test_every_reaction_has_a_clean_sound(tmp_path, monkeypatch):
    monkeypatch.setattr(sfx, "DIR", tmp_path)
    sfx.build(force=True)
    assert set(REACTIONS) == set(sfx.SOUNDS)
    for name in REACTIONS:
        with wave.open(str(tmp_path / f"{name}.wav"), "rb") as w:
            a = np.frombuffer(w.readframes(w.getnframes()), "<i2") / 32767
        assert 0.2 < len(a) / sfx.RATE < 7, name
        assert abs(a).max() <= sfx.VOLUME + 0.01, name                 # never louder than set
        assert abs(a[:10]).max() < 0.05 and abs(a[-10:]).max() < 0.05, name   # fades in/out: no clicks
        assert 0.1 < sfx.length(name) < 7


def test_sounds_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setattr(sfx, "DIR", tmp_path)
    played = []
    import winsound
    monkeypatch.setattr(winsound, "PlaySound", lambda *a: played.append(a))
    settings = DATA / "settings.json"
    old = settings.read_text(encoding="utf-8") if settings.exists() else None
    try:
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"reaction_sounds": True}), encoding="utf-8")
        assert sfx.play("laugh") and len(played) == 1
        assert not sfx.play("not-a-reaction")
        settings.write_text(json.dumps({"reaction_sounds": False}), encoding="utf-8")
        assert not sfx.play("laugh") and len(played) == 1
        assert sfx.length("laugh") == 0
    finally:
        settings.unlink(missing_ok=True) if old is None else settings.write_text(old, encoding="utf-8")


def test_mouth_hold_counts_as_busy():
    m = voice.RealMouth.__new__(voice.RealMouth)
    voice.RealMouth.__init__(m)
    m.last_end = 0.0
    assert not m.busy()
    m.hold(0.5)
    assert m.busy()


def test_voice_clips_are_used_when_present():
    for name in ("laugh", "wave", "bow", "sad"):
        assert sfx._clip(name) is not None, name
    assert sfx._clip("dance") is None          # no clip: the made-in-code tune
