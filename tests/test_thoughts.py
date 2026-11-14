"""What Nova is thinking: the live log the chat shows per request, and the thought cloud over 3D Nova."""
from nova.core import app


def texts_since(last):
    return [t["text"] for t in app.thoughts["list"] if t["id"] > last]


def test_steps_are_logged_for_one_request(brain):
    last = app.thoughts["next"] - 1
    turn = app.thoughts["turn"]
    brain([("", [("get_datetime", {})]), ("It's noon.", [])])
    app.answer("hmm, how late is it")
    new = [t for t in app.thoughts["list"] if t["id"] > last]
    assert new and all(t["turn"] == turn + 1 for t in new)
    said = " | ".join(t["text"] for t in new)
    assert "Working out what you need" in said
    assert "Using get_datetime" in said
    assert "Thought for" in said


def test_reasoning_is_cut_into_lines(monkeypatch):
    got = []
    monkeypatch.setattr(app, "think", lambda text, cloud_too=True: got.append(text))
    s = app.ThoughtStream()
    for piece in ["The user wants the ti", "me. I should call get_datetime.\nThen ", "answer briefly"]:
        s.feed(piece)
    s.flush()
    assert got == ["🧠 The user wants the time.", "🧠 I should call get_datetime.", "🧠 Then answer briefly"]


def test_api_starts_from_now_then_streams(client, brain):
    first = client.get("/api/thoughts").get_json()
    assert first["thoughts"] == []
    brain([("Hello!", [])])
    app.answer("hi")
    j = client.get(f"/api/thoughts?after={first['last']}").get_json()
    assert j["thoughts"] and j["last"] == j["thoughts"][-1]["id"] and j["busy"] is False


def test_cloud_gets_thoughts_and_is_cleared_after_the_answer(brain, monkeypatch):
    sent = []
    monkeypatch.setattr(app.avatar_client, "send", lambda do, timeout=20, **a: sent.append((do, a)))
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    brain([("Hello!", [])])
    app.answer("hi")
    import time as _t
    end = _t.time() + 3
    while _t.time() < end and not any(do == "thought" and a["text"] == "" for do, a in sent):
        _t.sleep(0.02)
    clouds = [a["text"] for do, a in sent if do == "thought"]
    assert clouds and clouds[-1] == ""


def test_log_is_bounded():
    for i in range(app.MAX_THOUGHTS + 50):
        app.think(f"thought {i}", cloud_too=False)
    assert len(app.thoughts["list"]) == app.MAX_THOUGHTS
