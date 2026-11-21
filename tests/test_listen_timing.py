"""She stops listening soon after you stop talking - even with a TV or fan on - and both waits are settings."""
import sys
import types

import numpy as np

from nova.audio import voice
from nova.core import app

RATE = voice.WhisperTranscriber.RATE
N = int(RATE * voice.WhisperTranscriber.BLOCK)


def tone(secs, level, hz=220):
    t = np.arange(int(RATE * secs)) / RATE
    return (np.sin(2 * np.pi * hz * t) * level).astype(np.float32)


class FakeMic:
    def __init__(self, audio):
        self.audio, self.i, self.blocks = audio, 0, len(audio) // N

    def __call__(self, **kw):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        i = min(self.i, self.blocks - 1)
        self.i += 1
        return self.audio[i * N:(i + 1) * N].reshape(-1, 1), False


def recorded_secs(monkeypatch, audio, end_pause=None, max_len=10):
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=FakeMic(audio)))
    w = voice.WhisperTranscriber()
    if end_pause:
        w.END_PAUSE = end_pause
    rec = w._record(start_wait=3.0, max_len=max_len)
    return len(rec) / RATE, w


def tv(secs, level=0.0012, seed=1):
    """A TV across the room: talking in bursts (louder lines, quieter gaps) - far softer than you, but well above
    the room's own noise. The room was measured in one of its quiet gaps, before you spoke."""
    rng = np.random.default_rng(seed)
    n = int(RATE * secs)
    env = np.repeat(rng.uniform(.3, 1.0, n // 4800 + 1), 4800)[:n]          # a new loudness every 0.3 s
    return (rng.standard_normal(n) * level * env).astype(np.float32)


def test_with_the_tv_on_she_stops_soon_after_you_do(monkeypatch):
    room = tv(1.0, level=0.0002)                       # a quiet moment on the TV before you spoke
    you = tone(2.0, 0.01) + tv(2.0, seed=2)
    secs, w = recorded_secs(monkeypatch, np.concatenate([room, you, tv(15.0, seed=3)]))
    assert secs < 2.0 + 0.35 + w.END_PAUSE + 0.3       # not the old 10-20 s run to the time limit
    assert w.timing["pause"] >= w.END_PAUSE            # it ended on a pause


def test_end_pause_is_the_setting(monkeypatch):
    audio = np.concatenate([np.zeros(RATE // 2, np.float32), tone(1.0, 0.01), np.zeros(RATE * 5, np.float32)])
    short, _ = recorded_secs(monkeypatch, audio, end_pause=0.4)
    long, _ = recorded_secs(monkeypatch, audio, end_pause=1.5)
    assert 1.0 <= long - short <= 1.2


def test_a_short_breath_mid_sentence_does_not_end_it(monkeypatch):
    audio = np.concatenate([np.zeros(RATE // 2, np.float32), tone(1.0, 0.01), np.zeros(int(RATE * .3), np.float32),
                            tone(1.0, 0.01), np.zeros(RATE * 3, np.float32)])
    secs, _ = recorded_secs(monkeypatch, audio, end_pause=0.7)
    assert secs > 2.3                                  # both halves are in the recording


def test_settings_reach_her_ears(monkeypatch):
    if not app.whisper:
        return
    app.apply_listening({"listen_pause": 1.2, "listen_max": 15})
    assert app.whisper.END_PAUSE == 1.2 and app.whisper.MAX_LEN == 15
    app.apply_listening({"listen_pause": 9, "listen_max": 1})      # out of range: kept sensible
    assert app.whisper.END_PAUSE == app.LISTEN_PAUSE[1] and app.whisper.MAX_LEN == app.LISTEN_MAX[0]
    app.apply_listening({})
    assert app.whisper.END_PAUSE == 0.7 and app.whisper.MAX_LEN == 10


def test_setup_saves_the_listening_times(monkeypatch):
    saved = {}
    s = dict(app.settings())
    monkeypatch.setattr(app.config, "settings", lambda: s)
    monkeypatch.setattr(app.config, "save_settings", lambda x: saved.update(x))
    app.app.test_client().post("/api/setup", json={"listen_pause": 0.5, "listen_max": 8})
    assert saved["listen_pause"] == 0.5 and saved["listen_max"] == 8
    st = app.app.test_client().get("/api/status").get_json()
    assert st["listen_pause"] == 0.5 and st["listen_max"] == 8
    app.apply_listening({})
