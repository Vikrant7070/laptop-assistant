"""⚙ Senses "How loud I must be": the slider (mic_level 1-10) moves the loudness that starts a recording."""
import sys
import types

import numpy as np

from nova.audio import voice
from nova.core import app
from tests.test_listen_timing import RATE, FakeMic, tone

W = voice.WhisperTranscriber


def room(secs, level=0.00003, seed=1):
    """This laptop's silent room: a faint hiss far below speech."""
    return (np.random.default_rng(seed).standard_normal(int(RATE * secs)) * level).astype(np.float32)


def record(monkeypatch, audio, mic_level):
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=FakeMic(audio)))
    w = W()
    w.scale = W.level_scale(mic_level)
    rec = w._record(start_wait=1.5, max_len=10)
    return None if rec is None else len(rec) / RATE


def test_level_5_is_the_old_behaviour():
    w = W()
    assert W.level_scale(5) == 1.0
    assert w.thresholds() == (W.START_MIN, 3.5, W.KEEP_MIN, 2.5)


def test_the_scale_runs_the_right_way():
    assert W.level_scale(1) == 0.25 and W.level_scale(10) > 5.5
    w = W()
    w.scale = W.level_scale(1)
    start_x = w.thresholds()[1]
    assert start_x >= 2.0                     # a soft setting still never starts on steady room noise


def test_a_soft_voice_is_caught_on_a_low_setting_and_ignored_on_a_high_one(monkeypatch):
    soft = np.concatenate([room(0.5), tone(1.0, 0.0006) + room(1.0, seed=2), room(3.0, seed=3)])   # RMS ~0.0004
    assert record(monkeypatch, soft, 2) is not None
    assert record(monkeypatch, soft, 5) is not None        # (as today)
    assert record(monkeypatch, soft, 9) is None            # too quiet: she doesn't start listening


def test_a_loud_setting_does_not_cut_off_a_softer_ending(monkeypatch):
    audio = np.concatenate([room(0.5), tone(1.0, 0.02), tone(0.6, 0.0057), room(3.0, seed=4)])
    secs = record(monkeypatch, audio, 10)
    assert secs is not None and secs > 1.0 + 0.6 + 0.3     # the soft last 0.6 s is in the recording


def test_the_setting_is_clamped_and_reaches_her_ears():
    if not app.whisper:
        return
    app.apply_listening({"mic_level": 99})
    assert app.whisper.scale == W.level_scale(app.MIC_LEVEL[1])
    app.apply_listening({"mic_level": 0})
    assert app.whisper.scale == W.level_scale(app.MIC_LEVEL[0])
    app.apply_listening({"mic_level": "loud"})              # junk: back to normal
    assert app.whisper.scale == 1.0
    app.apply_listening({})
    assert app.whisper.scale == 1.0


def test_setup_saves_the_mic_level(monkeypatch):
    saved = {}
    s = dict(app.settings())
    monkeypatch.setattr(app.config, "settings", lambda: s)
    monkeypatch.setattr(app.config, "save_settings", lambda x: saved.update(x))
    client = app.app.test_client()
    client.post("/api/setup", json={"mic_level": 8})
    assert saved["mic_level"] == 8
    assert client.get("/api/status").get_json()["mic_level"] == 8
    client.post("/api/setup", json={"mic_level": 42})
    assert saved["mic_level"] == app.MIC_LEVEL[1]
    app.apply_listening({})


def test_the_meter_reports_the_start_level(monkeypatch):
    if not app.whisper:
        return
    monkeypatch.setattr(app.whisper, "meter", (0.001, 0.0003, __import__("time").time()))
    m = app.app.test_client().get("/api/mic_level").get_json()
    assert m["live"] and m["rms"] == 0.001 and m["start"] == 0.0003
    monkeypatch.setattr(app.whisper, "meter", (0.001, 0.0003, 0.0))     # nothing listening lately
    m = app.app.test_client().get("/api/mic_level").get_json()
    assert not m["live"] and m["rms"] == 0.0
