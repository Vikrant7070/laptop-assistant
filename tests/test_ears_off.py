"""👂 off in the chat = she doesn't listen at all: no "Hey Nova", no conversation listening, no "yes?" listening."""
import pytest

from nova.core import app


@pytest.fixture
def setup(monkeypatch):
    sent, calls = [], []
    s = dict(app.settings(), wake=True)
    monkeypatch.setattr(app, "settings", lambda: s)
    monkeypatch.setattr(app, "save_settings", lambda x: s.update(x))
    monkeypatch.setattr(app.avatar_client, "send", lambda do, timeout=20, **a: sent.append((do, a)))
    monkeypatch.setattr(app.ears, "start", lambda: calls.append("start"))
    monkeypatch.setattr(app.ears, "stop", lambda: calls.append("stop"))
    yield s, calls
    app.convo["on"] = False
    app.listen_state["single"] = False
    app.listen_state["ears_off"] = False
    if app.whisper:
        app.whisper.muted = False


def test_ears_off_ends_conversation_listening(setup):
    s, calls = setup
    app.convo["on"] = True
    app.listen_state["single"] = True
    app.app.test_client().post("/api/setup", json={"wake": False})
    assert calls == ["stop"] and not s["wake"]
    assert not app.convo["on"] and not app.listen_state["single"]
    if app.whisper:
        assert app.whisper.muted
        assert app.whisper.listen() == ("silence", "ears off")      # the mic isn't even opened


def test_ears_on_again(setup):
    s, calls = setup
    app.app.test_client().post("/api/setup", json={"wake": False})
    app.app.test_client().post("/api/setup", json={"wake": True})
    assert calls == ["stop", "start"] and s["wake"]
    if app.whisper:
        assert not app.whisper.muted


def test_silent_loop_does_not_listen_with_ears_off(setup, monkeypatch):
    app.app.test_client().post("/api/setup", json={"wake": False})
    app.convo["on"] = True
    heard = []
    monkeypatch.setattr(app.transcriber, "listen", lambda: heard.append(1) or ("silence", ""))
    app.silent_loop()
    assert not heard
