"""Noise must not come through as words: stock phrases, a bare "No." nobody asked for, far-away voices,
a hum read as her name, and short unsure results from Windows' recognizer. Real speech must still get through."""
import sys
import types

import numpy as np
import pytest

from nova.audio import voice

W = voice.WhisperTranscriber
RATE = W.RATE
N = int(RATE * W.BLOCK)


# ---------------------------------------------------------------- words that are noise, whatever the score

@pytest.mark.parametrize("text", ["Thank you!", "you.", "Bye bye.", "The end.", "Hmm, hmm.", "Uh...", "",
                                  "Thanks for watching!", "Please subscribe."])
def test_stock_phrases_are_noise(text):
    assert voice.noise_words(text)


@pytest.mark.parametrize("text", ["Open Chrome.", "What time is it?", "No.", "Thank you, open YouTube.",
                                  "Hey Nova", "Stop."])
def test_real_words_are_not_noise(text):
    assert voice.noise_words(text) == ""


# ---------------------------------------------------------------- bare yes/no only when she asked something

def test_unsure_bare_no_is_dropped_when_nothing_was_asked():
    assert W.doubt("No.", -1.1, voice.WHISPER_HINT, expecting=False) == "not sure enough"


def test_unsure_bare_no_counts_as_an_answer_after_a_question():
    assert W.doubt("No.", -1.1, voice.WHISPER_HINT, expecting=True) == ""


def test_clear_bare_no_always_counts():
    assert W.doubt("No.", -0.3, voice.WHISPER_HINT, expecting=False) == ""


def test_stop_cuts_her_off_even_when_nothing_was_asked():
    assert W.doubt("Stop.", -1.0, None, expecting=False) == ""


def test_transcriber_asks_the_app_whether_a_reply_is_expected(monkeypatch):
    w = W()
    seen = []
    w.expect_reply = lambda: seen.append(1) or False
    seg = types.SimpleNamespace(text=" No.", avg_logprob=-1.1, no_speech_prob=0.1, compression_ratio=1.0)
    monkeypatch.setattr(w, "_decode", lambda audio, hint, force=None: [seg])
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", types.SimpleNamespace(
        VadOptions=lambda **k: None, get_speech_timestamps=lambda audio, opts: [{"start": 0, "end": RATE}]))
    audio = (np.sin(np.arange(RATE) / 5) * 0.1).astype(np.float32)
    assert w._transcribe(audio, voice.WHISPER_HINT, strict=True) == ""
    assert seen and "not sure enough" in w.rejected
    w.expect_reply = lambda: True
    assert w._transcribe(audio, voice.WHISPER_HINT, strict=True) == "No."
    assert w.sure == pytest.approx(-1.1)


# ---------------------------------------------------------------- her name on its own must be clearly said

def test_hum_read_as_her_name_does_not_wake_her():
    assert voice.WhisperEars.wake_doubt("", -1.3) == "not sure enough"


def test_her_name_said_clearly_wakes_her():
    assert voice.WhisperEars.wake_doubt("", -0.4) == ""
    assert voice.WhisperEars.wake_doubt("wake up", -0.6) == ""


def test_one_breath_request_gets_the_request_check():
    assert voice.WhisperEars.wake_doubt("what's the time", -0.4) == ""
    assert voice.WhisperEars.wake_doubt("see you next time", -0.3)     # ghost phrase after a misheard "Noah"
    assert voice.WhisperEars.wake_doubt("open the flow", -1.4)


class FakeW:
    def __init__(self, sure, level=0.002):
        self.sure, self.level, self.voice_level = sure, 0.1, level
        self.profile = None


def make_ears(sure, monkeypatch, tmp_path):
    w = FakeW(sure)
    w.profile = voice.VoiceProfile(path=tmp_path / "vp.json")
    got = []
    ears = voice.WhisperEars(types.SimpleNamespace(busy=lambda: False), got.append, w)
    ears.on_wake = lambda **k: got.append("WAKE")
    monkeypatch.setattr(voice.threading, "Thread", lambda target, args=(), kwargs=None, daemon=None:
                        types.SimpleNamespace(start=lambda: target(*args, **(kwargs or {}))))
    return ears, w, got


def test_unsure_name_is_ignored_and_noted(monkeypatch, tmp_path):
    ears, w, got = make_ears(-1.4, monkeypatch, tmp_path)
    ears._handle("Noah.")
    assert got == [] and "ignored noise" in ears.heard[-1]
    assert w.profile.levels == []                     # noise doesn't teach it what the user sounds like


def test_clear_name_wakes_her_and_learns_the_voice_level(monkeypatch, tmp_path):
    ears, w, got = make_ears(-0.3, monkeypatch, tmp_path)
    ears._handle("Hey Nova.")
    assert got == ["WAKE"]
    assert w.profile.levels == [0.002]


def test_name_spotted_mid_sentence_is_trusted(monkeypatch, tmp_path):
    ears, w, got = make_ears(-1.4, monkeypatch, tmp_path)
    ears._handle("Nova", early=True)
    assert got == ["WAKE"]


# ---------------------------------------------------------------- loudness: the user's own voice level

def test_profile_waits_for_enough_samples(tmp_path):
    p = voice.VoiceProfile(path=tmp_path / "vp.json")
    for _ in range(p.MIN_SAMPLES - 1):
        p.learn(0.01)
    assert p.usual() is None and p.doubt(0.0001) == ""


def test_far_away_voice_is_doubted_and_near_voice_is_not(tmp_path):
    p = voice.VoiceProfile(path=tmp_path / "vp.json")
    for lvl in (0.008, 0.01, 0.012, 0.01, 0.009, 0.011):
        p.learn(lvl)
    assert p.usual() == pytest.approx(0.01)
    assert "quieter than your voice" in p.doubt(0.0015)      # a TV across the room
    assert p.doubt(0.004) == ""                              # the user speaking softly
    assert p.doubt(0.03) == ""                               # ...or loudly


def test_profile_is_kept_between_runs_and_capped(tmp_path):
    p = voice.VoiceProfile(path=tmp_path / "vp.json")
    for i in range(p.KEEP + 5):
        p.learn(0.001 * (i + 1))
    again = voice.VoiceProfile(path=tmp_path / "vp.json")
    assert len(again.levels) == p.KEEP and again.levels == p.levels


def test_profile_ignores_a_broken_file(tmp_path):
    f = tmp_path / "vp.json"
    f.write_text("not json", encoding="utf-8")
    assert voice.VoiceProfile(path=f).levels == []


def test_sound_barely_above_the_room_is_doubted(tmp_path):
    p = voice.VoiceProfile(path=tmp_path / "vp.json")
    assert "barely louder than the room" in p.doubt(0.002, noise=0.001)
    assert p.doubt(0.01, noise=0.001) == ""


# ---------------------------------------------------------------- the recorder measures voice and room levels

def tone(secs, level):
    t = np.arange(int(RATE * secs)) / RATE
    return (np.sin(2 * np.pi * 220 * t) * level).astype(np.float32)


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


def record(monkeypatch, audio):
    mic = FakeMic(np.concatenate([audio, np.zeros(RATE * 5, np.float32)]))
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=mic))
    w = W()
    return w, w._record(start_wait=3.0, max_len=10)


def test_record_measures_the_voice_and_the_room(monkeypatch):
    rng = np.random.default_rng(0)
    room = (rng.standard_normal(RATE) * 0.0001).astype(np.float32)
    w, rec = record(monkeypatch, np.concatenate([room, tone(1.0, 0.01)]))
    assert rec is not None
    assert w.voice_level == pytest.approx(0.01 / np.sqrt(2), rel=0.1)    # RMS of the tone
    assert 0.00005 < w.noise < 0.0003                                     # the room before it


def test_strict_listen_drops_a_far_away_voice_before_whisper_runs(monkeypatch, tmp_path):
    w = W()
    mic = FakeMic(np.concatenate([np.zeros(RATE // 2, np.float32), tone(1.0, 0.001), np.zeros(RATE * 5, np.float32)]))
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=mic))
    w.profile = voice.VoiceProfile(path=tmp_path / "vp.json")
    for _ in range(6):
        w.profile.learn(0.02)
    monkeypatch.setattr(w, "load", lambda: None)
    monkeypatch.setattr(w, "_transcribe", lambda *a, **k: pytest.fail("Whisper should not run"))
    assert w.hear(3.0, 10.0, voice.WHISPER_HINT, strict=True) == ("unclear", "")
    assert "quieter than your voice" in w.rejected


# ---------------------------------------------------------------- Windows' recognizer (backup)

@pytest.mark.parametrize("text, conf", [("Hmm.", "High"), ("Thank you.", "Medium"), ("Open flow", "Low")])
def test_windows_noise_is_doubted(text, conf):
    assert voice.Transcriber.doubt(text, conf)


@pytest.mark.parametrize("text, conf", [("Open Chrome", "Medium"), ("No", "Low"),
                                        ("what is the weather like today", "Low")])
def test_windows_real_speech_passes(text, conf):
    assert voice.Transcriber.doubt(text, conf) == ""


def test_windows_transcriber_turns_noise_into_unclear(monkeypatch):
    t = voice.Transcriber()
    monkeypatch.setattr(t, "start", lambda: True)
    t.proc = types.SimpleNamespace(stdin=types.SimpleNamespace(write=lambda s: None, flush=lambda: None))
    t.lines.put("RESULT:Success|Medium|Thank you.")
    assert t.listen() == ("unclear", "Success") and "Thank you" in t.rejected
    t.lines.put("RESULT:Success|High|Open Chrome")
    assert t.listen() == ("text", "Open Chrome") and t.rejected == ""


def test_windows_wake_word_needs_half_confidence():
    assert '-eq "wake" -and $res.Confidence -ge 0.5' in voice.LISTEN_PS1


# ---------------------------------------------------------------- the app tells the ears when a yes/no is expected

def test_app_expects_a_reply_only_after_a_question(brain, monkeypatch):
    from nova.core import app
    monkeypatch.setattr(app.avatar_client, "send", lambda *a, **k: None)
    monkeypatch.setitem(app.convo, "asked", 0.0)
    monkeypatch.setitem(app.offer, "job", None)
    assert not app.expecting_reply()
    brain([("Want me to open it?", []), ("Done, it's open.", [])])
    app.heard("find my resume", via="silent")
    assert app.expecting_reply()                     # her answer was a question: "no" may follow
    app.heard("yes please", via="silent")
    assert not app.expecting_reply()                 # a plain answer: a stray "No." now needs to be clear
    app.state["pending"] = [{"name": "shutdown", "args": {}}]
    assert app.expecting_reply()                     # an Allow/No question


def test_app_wires_the_hook_into_whisper():
    from nova.core import app
    src = open(app.home_of("expecting_reply").__file__, encoding="utf-8").read()   # app is a package: voice_io.py
    assert "whisper.expect_reply = lambda: expecting_reply()" in src
