"""Nova must not take noise or her own voice as a request (conversation mode listens without "Hey Nova")."""
import sys
import types

import numpy as np
import pytest

from nova.audio import voice

RATE = voice.WhisperTranscriber.RATE
N = int(RATE * voice.WhisperTranscriber.BLOCK)


def tone(secs, level):
    t = np.arange(int(RATE * secs)) / RATE
    return (np.sin(2 * np.pi * 220 * t) * level).astype(np.float32)


class FakeMic:
    """sounddevice.InputStream that plays back a script; `talking` says, per block, whether Nova was speaking."""
    def __init__(self, audio, talking):
        self.audio, self.talking, self.i = audio, talking, 0
        self.blocks = len(audio) // N

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

    def nova_talking(self):
        return self.talking(min(self.i, self.blocks - 1) * N / RATE)


@pytest.fixture
def mic(monkeypatch):
    def make(audio, talking):
        fake = FakeMic(np.concatenate([audio, np.zeros(RATE * 30, np.float32)]), talking)
        monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=fake))
        w = voice.WhisperTranscriber()
        w.self_voice = fake.nova_talking
        return w
    return make


def test_her_own_voice_is_never_recorded(mic):
    quiet = np.zeros(RATE, np.float32)
    # 0-1 s room, 1-4 s Nova reads a notification (loud), then silence
    w = mic(np.concatenate([quiet, tone(3, 0.05)]), talking=lambda t: 1.0 <= t < 4.3)
    assert w._record(start_wait=2.0, max_len=10) is None          # nothing but her voice: nobody spoke


def test_user_after_her_voice_is_kept_alone(mic):
    quiet = np.zeros(RATE, np.float32)
    # Nova speaks 1-3 s; the user answers 4-5.5 s
    audio = np.concatenate([quiet, tone(2, 0.05), quiet, tone(1.5, 0.02)])
    w = mic(audio, talking=lambda t: 1.0 <= t < 3.3)
    rec = w._record(start_wait=3.0, max_len=10)
    assert rec is not None
    assert len(rec) / RATE < 3.0                                 # only the user's words (+pre-roll and end pause)
    assert float(np.abs(rec).max()) < 0.03                       # her louder voice isn't in it


@pytest.mark.parametrize("text, sure", [
    ("What's on my screen?", -0.79),          # Whisper repeating its hint over a chime
    ("I'll see you next time.", -0.97),       # stock video ending made up from a hum
    ("See you next time.", -0.4),             # ...even when it sounds sure
    ("Huh?", -0.6), ("Hmm.", -0.9), ("Oh, oh, oh, oh, oh.", -1.4), ("Hey!", -0.63),   # sounds, not words
    ("For more information, visit www.fema.org.", -1.28),
    ("Open the flow.", -1.4),                 # unsure words
])
def test_noise_is_doubted(text, sure):
    assert voice.WhisperTranscriber.doubt(text, sure, voice.WHISPER_HINT)


@pytest.mark.parametrize("text, sure", [
    ("What's on my screen?", -0.25), ("What time is it?", -0.14), ("Open Chrome.", -0.43),
    ("No.", -1.1), ("Yes.", -0.45), ("Read the selected text.", -0.36), ("Hey Nova, what's the time?", -0.5),
])
def test_real_requests_pass(text, sure):
    assert voice.WhisperTranscriber.doubt(text, sure, voice.WHISPER_HINT) == ""
