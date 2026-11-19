"""Barge-in: talk over Nova ("Nova, stop", "wait", "ruko") and she stops and listens - but her own voice coming
back from the speakers never stops her."""
import sys
import threading
import time
import types

import numpy as np

from nova.audio import voice
from nova.core import app

from test_listen_timing import FakeMic, tone

kind = voice.BargeIn.barge_kind


# ---------------------------------------------------------------- the words: only what she wasn't saying counts
def test_her_name_or_stop_over_her_voice_counts():
    own = "Okay, I opened Chrome and searched for lofi music."
    assert kind("Okay I opened Chrome, Nova stop, searched for", own) == "stop"
    assert kind("and searched for... Nova, wait", own) == "name"
    assert kind("wait wait hold on", own) == "name"
    assert kind("ruko", own) == "stop"


def test_her_own_words_never_count():
    own = "Should I stop the music, or wait until the song ends? I'm Nova, by the way."
    assert kind("Should I stop the music or wait until the song ends", own) == ""
    assert kind("I'm Nova by the way", own) == ""
    assert kind("", own) == ""
    assert kind("Okay I opened Chrome", "Okay, I opened Chrome.") == ""


def test_with_music_playing_only_her_name_counts():
    own = "Your battery is at sixty percent."
    assert kind("Your battery stop in the name of love", own, music=True) == ""
    assert kind("Your battery wait for me", own, music=True) == ""
    assert kind("Nova stop", own, music=True) == "stop"
    assert kind("Hey Nova", own, music=True) == "name"


def test_unrelated_new_words_dont_count():
    assert kind("Your battery is at sixty percent pizza tonight", "Your battery is at sixty percent.") == ""


# ---------------------------------------------------------------- the sound: her echo alone never asks Whisper
def barge(**kw):
    b = voice.BargeIn(types.SimpleNamespace(), None, lambda *a: None, **kw)
    return b


def test_her_echo_alone_never_triggers_a_check():
    b, rng = barge(), np.random.default_rng(0)
    t = 0.0
    for i in range(2000):                           # 60 s of her talking: echo = gain x played, give or take 3x
        played = rng.uniform(0.0, 0.3)
        rms = b.GAIN0 * played * rng.uniform(0.3, 3.0) + 0.00003
        t += b.BLOCK
        assert not b.unexplained(t, rms, played)


def test_you_speaking_in_her_pauses_triggers_a_check():
    b = barge()
    t, fired = 0.0, False
    for i in range(10):                             # she is mid-word: her echo explains the sound
        t += b.BLOCK
        b.unexplained(t, 0.004, 0.2)
    for i in range(4):                              # a pause between her words, and your voice (0.006) in it
        t += b.BLOCK
        fired = b.unexplained(t, 0.006, 0.0)
    assert fired


def test_you_much_louder_than_her_echo_triggers_a_check():
    b = barge()
    t, fired = 0.0, False
    for i in range(5):
        t += b.BLOCK
        fired = b.unexplained(t, 0.05, 0.2)        # 0.2 played -> her echo ~0.005; 0.05 is ten times that
    assert fired


def test_a_louder_room_or_speaker_is_learned_not_taken_for_you():
    b, rng = barge(), np.random.default_rng(1)
    t, fired = 0.0, []
    for i in range(600):                            # the volume went up: her echo is 4x what it was
        played = rng.uniform(0.05, 0.3)
        t += b.BLOCK
        fired.append(b.unexplained(t, b.GAIN0 * 4 * played * rng.uniform(0.5, 1.5), played))
    assert b.gain > b.GAIN0 * 2
    assert not any(fired[200:])                    # (once learned - after ~3 s of her talking - it stays quiet)


def test_one_click_is_not_enough():
    b = barge()
    assert not b.unexplained(1.0, 0.05, 0.0)
    assert not b.unexplained(3.0, 0.05, 0.0)        # far apart: each one alone
    assert not b.unexplained(5.0, 0.05, 0.0)


# ---------------------------------------------------------------- the checks and what fires
class FakeWhisper:
    def __init__(self, text):
        self.text = text

    def quick_english(self, audio, wait=0.0):
        return self.text


class TalkingMouth:
    neural = True
    gen = 7

    def __init__(self, words):
        self.words = words

    def talking(self):
        return True

    def words_between(self, t0, t1):
        return self.words


def test_check_fires_once_for_a_real_barge():
    got = []
    b = voice.BargeIn(TalkingMouth("Your battery is at sixty percent."), FakeWhisper("Your battery Nova stop"),
                      lambda k, text: got.append(k), others_playing=lambda: False)
    b._check(np.zeros(100, np.float32), time.time(), 7)
    assert got == ["stop"] and b.fired_gen == 7
    assert not b._active()                          # one barge per reply: the same reply can't fire again


def test_check_on_her_own_echo_does_nothing():
    got = []
    b = voice.BargeIn(TalkingMouth("Your battery is at sixty percent."), FakeWhisper("Your battery is at sixty"),
                      lambda k, text: got.append(k), others_playing=lambda: False)
    b._check(np.zeros(100, np.float32), time.time(), 7)
    assert got == [] and "not for me" in b.note


def test_check_after_she_moved_on_does_nothing():
    got = []
    b = voice.BargeIn(TalkingMouth(""), FakeWhisper("Nova stop"), lambda k, text: got.append(k),
                      others_playing=lambda: False)
    b._check(np.zeros(100, np.float32), time.time(), 6)    # (gen 6: she was stopped and started a new reply)
    assert got == []


# ---------------------------------------------------------------- her voice's timeline
def bare_mouth():
    m = object.__new__(voice.RealMouth)
    m.played, m.said_lines, m._out_end = voice.collections.deque(maxlen=3000), voice.collections.deque(maxlen=12), 0.0
    return m


def test_pieces_written_back_to_back_play_back_to_back():
    m = bare_mouth()
    piece = np.full(1200, 0.1, np.float32)          # 50 ms at 24 kHz
    a = m._note_played(piece, 24000, 0.09)
    b = m._note_played(piece, 24000, 0.09)
    assert abs(b - (a + 0.05)) < 1e-6
    assert len(m.played) == 10
    assert abs(m.echo_at(a, a + 0.1) - 0.1) < 1e-6
    assert m.echo_at(a + 1, a + 2) == 0.0


def test_after_a_pause_the_time_comes_from_the_clock():
    m = bare_mouth()
    m._out_end = time.time() - 5                    # she stopped talking five seconds ago
    start = m._note_played(np.zeros(240, np.float32), 24000, 0.09)
    assert abs(start - (time.time() + 0.09)) < 0.05


def test_words_between_finds_the_sentence_being_said():
    m = bare_mouth()
    m.said_lines.extend([(10.0, 12.0, "First one."), (12.0, 15.0, "Second one."), (20.0, 22.0, "Later.")])
    assert m.words_between(11.0, 13.0) == "First one. Second one."
    assert m.words_between(16.0, 18.0) == ""


def test_quick_stop_shortens_the_grace_so_she_hears_you_at_once():
    m = bare_mouth()
    m.stop_flag, m.lock, m.gen, m.pending = False, threading.Lock(), 0, 2
    m.ready_secs = m.play_until = m.bubble_until = m.hold_until = 0.0
    m.q, m.audio, m.tells = voice.queue.Queue(), voice.queue.Queue(), voice.queue.Queue()
    m.lips, m.speaking, m.grace, m.last_end = False, True, 0.8, 0.0
    m.stop(quick=True)
    assert m.gen == 1 and not m.speaking
    time.sleep(0.25)
    assert not m.busy()
    m.say("Hello again.")
    assert m.grace == 0.8                           # the next reply has the normal grace again


# ---------------------------------------------------------------- the listening after a barge
def test_after_a_barge_the_recording_starts_on_your_voice(monkeypatch):
    you = tone(1.0, 0.01)                           # you are already talking when the microphone opens
    audio = np.concatenate([you, np.zeros(int(voice.WhisperTranscriber.RATE * 3), np.float32)])
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=FakeMic(audio)))
    w = voice.WhisperTranscriber()
    w.next_floor = 0.0001                           # the room's noise, measured while she was talking
    rec = w._record(start_wait=2.0, max_len=10)
    assert rec is not None and np.abs(rec[:4000]).max() > 0.005     # your words are in it from the start
    assert w.next_floor is None                     # (used once)


def test_without_the_room_noise_your_ongoing_voice_was_missed(monkeypatch):
    you = tone(1.0, 0.01)
    audio = np.concatenate([you, np.zeros(int(voice.WhisperTranscriber.RATE * 3), np.float32)])
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=FakeMic(audio)))
    rec = voice.WhisperTranscriber()._record(start_wait=2.0, max_len=10)
    assert rec is None                              # the old behaviour this fixes: your voice was taken as the room


# ---------------------------------------------------------------- the app
def test_stop_while_working_stops_the_task(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "stop_now", lambda: calls.append("stop_now"))
    monkeypatch.setattr(app, "on_wake", lambda **k: calls.append("listen"))
    with app.lock:
        app.on_barge("stop", "Nova stop")
    assert calls == ["stop_now"]


def test_her_name_stops_her_and_she_listens(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "stop_now", lambda: calls.append("stop_now"))
    monkeypatch.setattr(app, "on_wake", lambda **k: calls.append("listen"))
    with app.lock:
        app.on_barge("name", "Nova wait")
    assert calls == ["listen"]                      # her name only stops her talking, not the task


def test_in_a_conversation_she_keeps_listening_after_stop(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "on_wake", lambda **k: calls.append("listen"))
    app.convo["on"] = True
    app.on_barge("stop", "stop")
    assert calls == ["listen"]


def test_barge_in_needs_ears_voice_and_the_setting(client, monkeypatch):
    monkeypatch.setattr(app, "set_ears", lambda on: None)        # (never start the real microphone)
    client.post("/api/setup", json={"wake": False})
    assert not app.barge_flags["on"]
    client.post("/api/setup", json={"wake": True, "voice": True, "barge_in": True})
    assert app.barge_flags["on"]
    client.post("/api/setup", json={"barge_in": False})
    assert not app.barge_flags["on"]
    assert app.settings()["barge_in"] is False
    assert client.get("/api/status").get_json()["barge_in"] is False


def test_she_dips_softly_and_comes_back_without_a_click():
    m = bare_mouth()
    m.duck_until, m._gain = 0.0, 1.0
    piece = np.ones((1200, 1), np.float32)
    assert m._ducked(piece) is piece                 # normal: untouched
    m.duck(1.0)
    down = m._ducked(piece)
    assert down[0, 0] == 1.0 and abs(down[-1, 0] - voice.DUCK_LEVEL) < 1e-6     # fades down over one piece
    assert np.allclose(m._ducked(piece), voice.DUCK_LEVEL)
    m.duck_until = 0.0
    up = m._ducked(piece)
    assert abs(up[0, 0] - voice.DUCK_LEVEL) < 1e-6 and up[-1, 0] == 1.0         # and back up
    assert m._ducked(piece) is piece


def test_whispers_spellings_of_ruko_count():
    own = "Your battery is fine."
    for heard in ("Rucco Nova", "Ruconova.", "Ruko!", "roko", "rukho"):
        assert kind(heard, own) == "stop", heard


def test_a_sentence_of_your_own_over_her_voice_counts():
    own = "I found three big files in your Downloads folder."
    assert kind("I found three big open Chrome instead please", own) == "talk"
    assert kind("I found three big model", own) == ""          # one misheard word of hers is not you
    assert kind("open Chrome instead please", own, music=True) == ""


# ---------------------------------------------------------------- "Nova, stop" when she isn't talking
def test_bare_stop_phrases_are_commands():
    for said in ("Nova, stop.", "stop", "Hey Nova stop it", "[spoken; speech recognition may have misheard words] Nova, stop.",
                 "Ruko Nova", "Rucco.", "bas karo yaar", "chup", "Okay Nova, that's enough.", "stop talking please"):
        assert app.just_stop(said), said
    for said in ("stop the music", "Nova, stop the timer and open Chrome", "how do I stop smoking?",
                 "don't stop", "bus stop near me", "Nova"):
        assert not app.just_stop(said), said


def test_nova_stop_goes_nowhere_near_the_brain(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "answer", lambda *a, **k: calls.append("brain") or ({"reply": "chat"}, 200))
    monkeypatch.setattr(app, "stop_now", lambda: calls.append("stop_now") or True)
    app.heard("Nova, stop.", via="silent")
    assert calls == ["stop_now"]
    assert any("stopped" in str(e.get("reply", "")) for e in app.events)
    assert app.mouth.said == []                     # she says nothing back


def test_stop_the_music_still_goes_to_the_brain(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "answer", lambda *a, **k: calls.append("brain") or ({"reply": "Paused."}, 200))
    monkeypatch.setattr(app, "stop_now", lambda: calls.append("stop_now") or False)
    app.heard("stop the music", via="silent")
    assert calls == ["brain"]


def test_hey_nova_stop_from_the_wake_listener_stops_instead_of_listening():
    got = []
    ears = voice.WhisperEars(types.SimpleNamespace(busy=lambda: False), lambda t: got.append(("request", t)),
                             types.SimpleNamespace(level=0.01, sure=-0.2, profile=None, voice_level=0.0))
    ears.on_wake = lambda **k: got.append(("listen",))
    ears.is_stop, ears.on_stop = app.just_stop, lambda t: got.append(("stop", t))
    ears._handle("Hey Nova, stop.", early=True)
    time.sleep(0.2)
    assert got == [("stop", "Hey Nova, stop.")]
