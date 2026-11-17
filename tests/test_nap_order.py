"""😴 Going to sleep says goodnight first, then lies down; hearing her name gets her up at once (in step with the ears)."""
import time

from nova.audio import voice
from nova.core import app


def body_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(app.avatar_client, "send", lambda do, **k: calls.append((do, k.get("on"))))
    return calls


def test_goodnight_before_lying_down(monkeypatch):
    calls = body_calls(monkeypatch)
    talking = {"v": True}
    monkeypatch.setattr(app.mouth, "talking", lambda: talking["v"])
    s = {**app.settings(), "voice": True}
    monkeypatch.setattr(app, "settings", lambda: s)
    app.go_to_sleep("asked")
    assert app.nap["asleep"]                      # asleep at once as far as the app is concerned...
    time.sleep(0.3)
    assert ("sleep", True) not in calls           # ...but her body waits while the goodnight is being said
    talking["v"] = False
    for _ in range(30):
        if ("sleep", True) in calls:
            break
        time.sleep(0.05)
    assert ("sleep", True) in calls


def test_woken_during_goodnight_stays_up(monkeypatch):
    calls = body_calls(monkeypatch)
    talking = {"v": True}
    monkeypatch.setattr(app.mouth, "talking", lambda: talking["v"])
    s = {**app.settings(), "voice": True}
    monkeypatch.setattr(app, "settings", lambda: s)
    app.go_to_sleep("asked")
    app.wake_body()
    talking["v"] = False
    time.sleep(0.4)
    assert ("sleep", True) not in calls


def test_name_heard_wakes_body_right_away(monkeypatch):
    woke = []
    ears = voice.WhisperEars(app.mouth, lambda t: None, None)
    ears.on_name = lambda: woke.append(1)
    ears.w = type("W", (), {"level": 0.1, "sure": 0.0, "voice_level": 0.0, "profile": None})()
    monkeypatch.setattr(voice.avatar_client, "send", lambda *a, **k: None)
    ears._handle("Hey Nova", early=True)
    assert woke
