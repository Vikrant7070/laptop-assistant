"""3D Nova's body in sync with sound: her own voice (gestures, mouth), laptop music (beat), and move sounds."""
import numpy as np
import pytest

from nova.audio import sfx, voice
from nova.avatar import body, hearing
from nova.core import app

SHRUG = [{"at": 0.5, "right_arm_out": 45}]


@pytest.mark.parametrize("text,want", [
    ("Yes boss, done!", "nod"), ("I'm not sure about that.", "confused"), ("Haha, good one", "laugh"),
    ("Sorry, my mistake.", "facepalm"), ("Thanks a lot!", "bow"), ("Wow, really?", "surprised"),
    ("What should I open?", "gesture_ask"), ("Awesome, it worked!", "gesture_excited"),
    ("You can try it now.", "gesture_point_you"),
])
def test_sentence_picks_a_gesture(text, want):
    g = body.speech_gesture(text, 2.0)
    assert (g if isinstance(g, str) else g["name"]) == want


def test_plain_sentences_alternate_hands_and_calm():
    plain = "The weather today is sunny with light winds."
    picks = [body.speech_gesture(plain, 2.0, n) for n in range(3)]
    assert picks[0]["name"] == "gesture_explain" and picks[1]["name"] == "gesture_explain_one" and picks[2] is None
    assert body.speech_gesture(plain, 0.3) is None                # too short to gesture


def test_gesture_moves_are_valid_moves():
    for name in body.GESTURES:
        g = body.gesture_move(name, 2.0)
        clean, err = body.clean_move("x_" + name, g["duration"], g["keys"])
        assert not err and 1.0 <= g["duration"] <= 3.2


def test_speech_envelope_follows_loudness():
    rate = 24000
    t = np.arange(rate) / rate
    samples = np.sin(2 * np.pi * 220 * t) * (t > 0.5)              # silent half, then loud
    env = voice.speech_envelope(samples, rate)
    assert len(env) == voice.SPEECH_FPS and max(env[:12]) == 0 and min(env[-12:]) > 0.9
    assert voice.speech_envelope([], rate) == []


def _music(bpm, secs=8, t0=1000.0):
    b = hearing.Beat()
    period, rng = 60 / bpm, np.random.default_rng(1)
    for i in range(hearing.RATE * secs):
        t = t0 + i / hearing.RATE
        b.add(max(0.0, 0.15 + 0.5 * np.exp(-((t - t0) / period % 1) * 12) + 0.03 * rng.standard_normal()), t)
    return b, t


@pytest.mark.parametrize("bpm", [90, 120, 150])
def test_beat_tempo_and_phase(bpm):
    b, t = _music(bpm)
    assert abs(b.music["bpm"] - bpm) <= 3
    true_phase = ((t - 1000.0) / (60 / bpm)) % 1
    assert 0 <= b.phase(t) < 2                                    # counts two beats (left-right sway)
    err = abs(b.phase(t) % 1 - true_phase)
    assert min(err, 1 - err) < 0.15


def test_no_beat_in_speech_or_silence_and_a_sudden_bang():
    b = hearing.Beat()
    rng = np.random.default_rng(2)
    for i in range(hearing.RATE * 8):
        b.add(float(rng.choice([0.0, 0.2, 0.4]) * (rng.random() > .3)), 2000 + i / hearing.RATE)
    assert b.music is None
    q = hearing.Beat()
    events = [q.add(0.0, 3000 + i / 25) for i in range(60)] + [q.add(0.8, 3000 + 60 / 25)]
    assert events[-1] == "surprised" and not any(events[:-1])


def test_pitch_names():
    assert body.pitch_hz("A4") == 440 and body.pitch_hz("C5") == 523.25 and body.pitch_hz("bb3") == 233.08
    assert body.pitch_hz("H9") is None and body.pitch_hz(10) is None and body.pitch_hz(True) is None


def test_sound_recipe_is_checked_and_rendered():
    snd, err = body.clean_sound({"notes": [{"type": t, "pitch": "C5", "at": i * .1} for i, t in enumerate(body.SOUND_TYPES)]})
    assert not err and len(snd["notes"]) == len(body.SOUND_TYPES)
    samples = sfx.render_notes(snd["notes"])
    assert 0.8 < len(samples) / sfx.RATE < 3 and np.abs(samples).max() > 0
    assert body.clean_sound({"notes": [{"type": "laser"}]})[1]
    assert body.clean_sound({"say": "Yay!"}) == ({"say": "Yay!"}, None)
    assert body.clean_sound(None) == (None, None)


def test_new_move_with_a_sound_in_a_chat(monkeypatch):
    sent = []
    monkeypatch.setattr(app.avatar_client, "running", lambda: True)
    monkeypatch.setattr(app.avatar_client, "status", lambda: {"live": True})
    monkeypatch.setattr(app.avatar_client, "send", lambda do, **k: sent.append((do, k)))
    sfx.move_sound_path("boing_jump").unlink(missing_ok=True)
    out = app._execute_one({"name": "new_move", "args": {"name": "boing jump", "duration": 1.5, "keys": SHRUG,
                                                        "sound": {"notes": [{"type": "boing", "pitch": "C4"}]}}},
                           True, [], "m")
    assert out.startswith("OK") and sfx.move_sound_path("boing_jump").exists()
    assert body.load_moves()["boing_jump"]["sound"]["notes"][0]["type"] == "boing"
    assert sfx.length("boing_jump") > 0.2
    body.forget_move("boing_jump")
    sfx.move_sound_path("boing_jump").unlink(missing_ok=True)


def test_voice_sound_without_a_voice_engine_is_silent_but_ok(monkeypatch):
    monkeypatch.setattr(app.avatar_client, "running", lambda: True)
    monkeypatch.setattr(app.avatar_client, "status", lambda: {"live": True})
    monkeypatch.setattr(app.avatar_client, "send", lambda do, **k: None)
    out = app._execute_one({"name": "new_move", "args": {"name": "cheer", "duration": 1, "keys": SHRUG,
                                                        "sound": {"say": "Yay!"}}}, True, [], "m")
    assert out.startswith("OK") and "silent" in out
    body.forget_move("cheer")


def test_ears_glow_exactly_while_listening(monkeypatch):
    import time as _time
    sent = []
    monkeypatch.setattr(app.avatar_client, "send", lambda do, **k: sent.append((do, k.get("on"), k.get("hearing"))))
    fake = type("T", (), {"hearing_at": 0.0})()
    monkeypatch.setattr(app.voice_io, "transcriber", fake, raising=False)
    def wait_for(ok, secs=5.0, keep_hearing=False):      # polls instead of fixed sleeps, so a busy laptop can't fail it
        end = _time.time() + secs
        while _time.time() < end and not ok():
            if keep_hearing:
                fake.hearing_at = _time.time()           # the voice keeps coming in until the heartbeat notices
            _time.sleep(0.02)
        return ok()

    off = app.ears_on()
    assert wait_for(lambda: len(sent) >= 1)
    assert wait_for(lambda: ("listen", True, True) in sent, keep_hearing=True)
    off()
    assert wait_for(lambda: sent[-1][:2] == ("listen", False))
    assert sent[0] == ("listen", True, False)            # on, waiting
    assert ("listen", True, True) in sent                # bigger waves while the voice comes in
    assert sent[-1][:2] == ("listen", False)             # off the moment listening ends


# ---- where she stays while idle (setting "avatar_roam") ----
AREA = (0, 0, 1920, 1040)


def test_default_is_to_stay_in_the_corner_and_the_spot_is_fixed():
    assert body.roam_mode({}) == "corner" and body.roam_mode({"avatar_roam": "nonsense"}) == "corner"
    spots = {body.roam_spot("corner", AREA, 300, 400, lambda a, b: (a + b) / 2) for _ in range(5)}
    assert spots == {(1920 - 300 * .42, 1040)}


def test_edge_mode_stays_on_the_bottom_right():
    for r in (0.0, 0.5, 1.0):
        x, y = body.roam_spot("edge", AREA, 300, 400, lambda a, b, r=r: a + (b - a) * r)
        assert y == 1040 and x >= 1920 - 480 and x <= 1920 - 300 * .42


def test_free_mode_can_still_go_anywhere():
    xs = {body.roam_spot("free", AREA, 300, 400, lambda a, b, r=r: a + (b - a) * r)[1] for r in (0.0, 0.9)}
    assert len(xs) == 2


def test_dialog_saves_the_roam_choice(client):
    j = client.post("/api/setup", json={"avatar_roam": "edge"}).get_json()
    assert j["avatar_roam"] == "edge"
    assert client.post("/api/setup", json={"avatar_roam": "bogus"}).get_json()["avatar_roam"] == "edge"
