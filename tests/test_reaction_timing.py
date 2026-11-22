"""The brain's react tool waits until she has finished speaking (a laugh comes after the joke, not before)."""
from nova.core import app
from nova.audio import voice


def test_react_tool_is_saved_for_after_the_answer(monkeypatch):
    sent, played = [], []
    monkeypatch.setattr(app.avatar_client, "running", lambda: True)
    monkeypatch.setattr(app.avatar_client, "send", lambda *a, **k: sent.append((a, k)))
    monkeypatch.setattr(app.body, "react_later", lambda name, wait_for_speech=True, repeat=None: played.append((name, repeat)))
    activity = []
    out = app._execute_one({"name": "react", "args": {"reaction": "laughing"}}, True, activity, "m")
    assert "after you finish speaking" in out
    # nothing acted out yet (the thought-cloud worker may still clear an earlier test's cloud: not a reaction)
    assert [a for a in sent if a[0][0] != "thought"] == []
    assert app.state["react_after"] == {"name": "laugh", "repeat": None}
    app.after_turn("tell me a joke", {"reply": "Why did the... ha!", "activity": activity})
    assert played == [("laugh", None)]                    # played once, after the answer
    assert "react_after" not in app.state


def test_automatic_reaction_when_brain_picked_none(monkeypatch):
    played = []
    monkeypatch.setattr(app.body, "react_later", lambda name, wait_for_speech=True, repeat=None: played.append(name))
    app.state.pop("react_after", None)
    app.after_turn("thank you so much", {"reply": "Anytime!", "activity": []})
    assert played == ["bow"]


def test_unknown_reaction_is_refused(monkeypatch):
    monkeypatch.setattr(app.avatar_client, "running", lambda: True)
    app.state.pop("react_after", None)
    out = app._execute_one({"name": "react", "args": {"reaction": "backflip"}}, True, [], "m")
    assert "can't" in out and "react_after" not in app.state


def test_talking_has_no_grace_period():
    m = voice.RealMouth.__new__(voice.RealMouth)
    voice.RealMouth.__init__(m)
    import time
    m.last_end = time.time()                              # just stopped
    assert m.busy() and not m.talking()                   # mic still waits; the reaction can go now
