"""👋 Presence: here / away / back decisions, camera only when needed, the lock warning, and the welcome-back line."""
import numpy as np
import pytest

from nova.actions import presence
from nova.core import app


@pytest.fixture(autouse=True)
def fresh():
    presence.reset()
    yield
    presence.reset()


class Cam:
    """A fake camera: pops the next answer (True face / False nobody / None can't tell)."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def __call__(self):
        self.calls += 1
        return self.answers.pop(0) if self.answers else False


def run(cam, start, idle_at, until, step=5, lock_on=False):
    """Tick from `start` to `until`; idle_at(t) gives the idle seconds. Returns [(t, event)]."""
    out, t = [], start
    while t <= until:
        for e in presence.tick(t, idle_at(t), False, cam, lock_on=lock_on):
            out.append((t, e))
        t += step
    return out


def test_typing_means_here_and_no_camera():
    cam = Cam()
    assert run(cam, 0, lambda t: 2, 600) == []
    assert cam.calls == 0 and not presence.state["away"]


def test_watching_a_film_is_not_away():
    cam = Cam(*[True] * 20)
    ev = run(cam, 0, lambda t: t, 1200)          # no input for 20 minutes, but a face every check
    assert ev == [] and not presence.state["away"]
    assert 5 <= cam.calls <= 12                  # checks slow down to every ~2 min once a face is seen


def test_empty_chair_twice_is_away_then_back():
    cam = Cam(False, False)
    ev = run(cam, 0, lambda t: t, 200)
    assert [e for _, e in ev] == ["away"]
    assert ev[0][0] == 90                        # checks at 60 s and 90 s
    presence.missed("Reminder: call mom")
    ev = run(cam, 600, lambda t: 1, 600)         # mouse moved 10 minutes later
    assert [e for _, e in ev] == ["back"]
    assert presence.welcome("Vikrant") == "Welcome back, Vikrant! While you were away: Reminder: call mom"
    assert presence.state["missed"] == []


def test_short_absence_gets_no_greeting():
    run(Cam(False, False), 0, lambda t: t, 100)
    assert presence.state["away"]
    ev = run(Cam(), 110, lambda t: 1, 110)                    # left at 0, back at 110 s: under 2 minutes
    assert [e for _, e in ev] == ["back_quiet"] and not presence.state["away"]


def test_time_away_counts_from_when_they_left():
    """Live test: left at 17:03:09, marked away at 17:04:44, back at ~17:05 - the clock must start at 17:03:09."""
    run(Cam(False, False), 0, lambda t: t, 100)
    assert presence.state["since"] == 0                        # their last input, not the 90 s decision
    ev = run(Cam(), 125, lambda t: 1, 125)
    assert [e for _, e in ev] == ["back"]


def test_face_while_away_counts_as_back():
    run(Cam(False, False), 0, lambda t: t, 100)
    ev = run(Cam(True), 400, lambda t: t, 400)               # sat down, hasn't touched anything yet
    assert [e for _, e in ev] == ["back"]


def test_unknown_camera_never_decides_quickly_and_never_locks():
    cam = Cam(*[None] * 40)
    assert run(cam, 0, lambda t: t, 590, lock_on=True) == [] and not presence.state["away"]
    ev = run(cam, 595, lambda t: t, 700, lock_on=True)          # 10 minutes without input: away, but no lock
    assert [e for _, e in ev] == ["away"]


def test_locked_screen_is_away_without_camera():
    cam = Cam()
    assert presence.tick(10, 500, True, cam) == ["away"]
    assert cam.calls == 0


def test_lock_after_warning_unless_they_move():
    cam = Cam(False, False, False)
    ev = run(cam, 0, lambda t: t, 140, lock_on=True)
    assert [e for _, e in ev] == ["away", "lock_warn", "lock"]
    assert ev[-1][0] - ev[1][0] == presence.LOCK_GRACE


def test_moving_the_mouse_cancels_the_lock():
    run(Cam(False, False), 0, lambda t: t, 90, lock_on=True)
    assert presence.state["locking_at"]
    ev = presence.tick(95, 1, False, Cam(), lock_on=True)
    assert "lock_cancel" in ev and not presence.state["locking_at"]


def test_face_at_the_last_look_cancels_the_lock():
    run(Cam(False, False), 0, lambda t: t, 90, lock_on=True)
    ev = presence.tick(115, 115, False, Cam(True), lock_on=True)
    assert "lock" not in ev and "lock_cancel" in ev


def test_face_finder_ignores_empty_pictures():
    blank = np.full((480, 640, 3), 128, np.uint8)
    noise = np.random.default_rng(1).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    kind, _ = presence.detector()
    if not kind:
        assert presence.faces_in(blank) is None          # no model: "can't tell", never "nobody"
        pytest.skip("face model not downloaded yet (~/.cache/nova-face)")
    assert presence.faces_in(blank) == 0 and presence.faces_in(noise) == 0


def test_without_a_model_the_camera_is_not_opened(monkeypatch, tmp_path):
    monkeypatch.setattr(presence, "FACE_DIR", tmp_path / "none")
    monkeypatch.setattr(presence, "_det", {"yunet": None, "haar": [], "failed": "", "loading": True})
    monkeypatch.setattr(presence.threading, "Thread", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no download in tests")))
    from nova.actions import tools
    monkeypatch.setattr(tools.eyes, "_camera_users", lambda: [])
    import cv2
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a: (_ for _ in ()).throw(AssertionError("camera opened")))
    assert presence.look() is None and "downloading" in presence.state["note"]


def test_bad_download_is_thrown_away(monkeypatch, tmp_path):
    monkeypatch.setattr(presence, "FACE_DIR", tmp_path / "face")

    class R:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, n): return b"<html>not a model</html>" + b"x" * 100_000
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: R())
    assert presence.download_model() is None
    assert "couldn't download" in presence._det["failed"] and not list((tmp_path / "face").iterdir())


def test_screen_is_not_locked_while_tests_run():
    assert presence.screen_locked() is False


def test_welcome_lists_several_things():
    presence.state["away"] = True
    presence.missed("Reminder: stretch")
    presence.missed("✅ Your research is ready.")
    assert presence.welcome() == "Welcome back! 2 things happened while you were away: Reminder: stretch • ✅ Your research is ready."


# ------------------------------------------------------------------ in Nova

def test_back_greets_and_wakes(monkeypatch):
    monkeypatch.setattr(app.integrations, "_first_name", lambda: "Vikrant")
    woke = []
    monkeypatch.setattr(app.voice_io, "wake_body", lambda: woke.append(1))
    app.nap["asleep"] = True
    app._greeted["at"] = 0
    presence.state.update(away=True, missed=["Reminder: water"])
    presence.state["away"] = False
    app.on_presence("back")
    assert woke and not app.nap["asleep"]
    assert app.mouth.said[-1] == "Welcome back, Vikrant! While you were away: Reminder: water"
    assert app.events[-1]["kind"] == "suggestion"
    app.on_presence("back")                               # not twice in 10 minutes
    assert len(app.mouth.said) == 1


def test_lock_event_locks(monkeypatch):
    locked = []
    monkeypatch.setattr(app.tools.system, "lock_screen", lambda: locked.append(1) or "Locked.")
    app.on_presence("lock_warn")
    assert "lock the laptop" in app.mouth.said[-1].lower() or "locking" in app.mouth.said[-1].lower()
    app.on_presence("lock")
    assert locked == [1]


def test_talking_to_nova_counts_as_here(monkeypatch):
    monkeypatch.setattr(app.background, "user_idle_secs", lambda: 900)
    app.convo["on"] = True
    assert app.presence_idle() == 0
    app.convo["on"] = False
    app.convo["last_heard"] = __import__("time").time() - 30
    assert 25 <= app.presence_idle() <= 35
    app.convo["last_heard"] = 0.0
    assert app.presence_idle() == 900


def test_missed_lines_are_kept_only_while_away(monkeypatch):
    monkeypatch.setattr(app.background, "user_idle_secs", lambda: 900)
    app.tell_away("Reminder: one")                          # idle, but presence says not away: no summary entry
    assert presence.state["missed"] == []
    presence.state["away"] = True
    app.tell_away("Reminder: two")
    assert presence.state["missed"] == ["Reminder: two"]


def test_settings_switches(client):
    r = client.post("/api/setup", json={"presence": True, "presence_lock": True, "error_help": False})
    assert r.status_code == 200
    s = client.get("/api/status").get_json()
    assert s["presence"] is True and s["presence_lock"] is True and s["error_help"] is False and s["away"] is False


def test_asked_to_sleep_means_coming_back_doesnt_wake_her(monkeypatch):
    """'Nova, go to sleep' is the user's choice: only 'Hey Nova' or a typed message wakes her, not the camera."""
    monkeypatch.setattr(app.integrations, "_first_name", lambda: "Vikrant")
    app._greeted["at"] = 0
    app.go_to_sleep("asked", quiet=True)
    presence.state.update(away=True, missed=["Reminder: water"])
    app.on_presence("back")
    assert app.nap["asleep"] and app.mouth.said == []
    assert presence.state["missed"] == ["Reminder: water"]          # kept for the next welcome
    app.note_task()                                                 # "Hey Nova" / a typed message wakes her
    assert not app.nap["asleep"] and app.nap["reason"] is None


def test_napping_after_idle_still_gets_the_welcome(monkeypatch):
    monkeypatch.setattr(app.integrations, "_first_name", lambda: "")
    app._greeted["at"] = 0
    app.go_to_sleep("idle", quiet=True)
    app.on_presence("back")
    assert not app.nap["asleep"] and app.mouth.said[-1] == "Welcome back! 👋"


def test_her_own_voice_is_not_the_user_coming_back(monkeypatch):
    """Live: a notification she read out while the user was away made her think they were back."""
    monkeypatch.setattr(app.background, "user_idle_secs", lambda: 400)
    monkeypatch.setattr(app.mouth, "busy", lambda: True)
    app.convo.update(on=False, thinking=False, last_heard=0.0)
    assert app.presence_idle() == 400
