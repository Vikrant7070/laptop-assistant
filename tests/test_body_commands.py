"""'Dance!' is played by code, not left to the brain: the small model used to *say* it was dancing without calling
react, and insisted it was when told it wasn't (2026-11-18). A move she doesn't have makes her ask."""
import pytest

from nova.core import app

S = "[spoken; speech recognition may have misheard words] "


@pytest.fixture
def body(monkeypatch):
    monkeypatch.setattr(app.avatar_client, "running", lambda: True)
    monkeypatch.setattr(app.body_module, "load_moves", lambda: {"bhangra_bounce": {}, "star_jump": {}})
    from nova.avatar import body as b
    monkeypatch.setattr(b, "load_moves", lambda: {"bhangra_bounce": {}, "star_jump": {}})
    for k in ("react_after", "body_ask", "remote"):
        app.state.pop(k, None)
    app.state["messages"] = [{"role": "user", "content": "x"}]
    yield
    for k in ("react_after", "body_ask"):
        app.state.pop(k, None)


@pytest.mark.parametrize("text,name,times", [
    (S + "Noah, can you dance?", "dance", None),
    (S + "Hey Nova, dance for me", "dance", None),
    ("dance", "dance", None),
    ("can you do a little dance please", "dance", None),
    ("do your usual dance", "dance", None),
    ("clap twice", "clap", 2),
    ("spin three times", "spin", 3),
    ("can you laugh", "laugh", None),
    ("do the bhangra", "bhangra_bounce", None),
    ("do a star jump", "star_jump", None),
    (S + "You are not dancing, Noah.", "dance", None),
    ("you didn't dance", "dance", None),
    ("I can't see you dancing", "dance", None),
])
def test_plain_body_requests_are_played(body, text, name, times):
    out = app.body_command(text)
    assert out and out["activity"] == ["Acted it out"]
    assert app.state["react_after"] == {"name": name, "repeat": times}


@pytest.mark.parametrize("text", [
    "open chrome", "what's the time", "dance music on youtube please play it now",
    "play some dance songs", "I am not dancing today", "can you search for salsa classes near me",
    "you are not listening", "take a screenshot",
])
def test_other_requests_go_to_the_brain(body, text):
    assert app.body_command(text) is None
    assert "react_after" not in app.state


def test_unknown_move_asks_instead_of_pretending(body):
    out = app.body_command(S + "No, do a desi thumka.")
    assert out["reply"].startswith("I don't know a desi thumka yet") and out["reply"].endswith("?")
    assert "react_after" not in app.state
    # "your usual one" -> her dance
    out = app.body_command("just do your usual dance")
    assert app.state["react_after"]["name"] == "dance"


def test_unknown_move_then_make_it_up_goes_to_brain_with_new_move(body):
    app.body_command("do a robot dance")
    app.state["task"] = {"tools": ["react"]}
    assert app.body_command("yes make one up") is None
    assert "new_move" in app.state["task"]["tools"]
    assert "invent the move 'robot dance'" in app.state["messages"][-1]["content"]


def test_no_body_on_screen_says_so(body, monkeypatch):
    monkeypatch.setattr(app.avatar_client, "running", lambda: False)
    out = app.body_command("dance")
    assert "can't move" in out["reply"] and "react_after" not in app.state


def test_said_dancing_without_react_still_dances(body, monkeypatch):
    played = []
    monkeypatch.setattr(app, "react_later", lambda name, wait_for_speech=True, repeat=None: played.append(name))
    monkeypatch.setattr(app, "review_turn", lambda *a, **k: None)
    app.after_turn("can you do something fun", {"reply": "Reacting now... I'm dancing!", "activity": []})
    assert played == ["dance"]


def test_asked_move_beats_correction_facepalm(body, monkeypatch):
    played = []
    monkeypatch.setattr(app, "react_later", lambda name, wait_for_speech=True, repeat=None: played.append(name))
    monkeypatch.setattr(app, "review_turn", lambda *a, **k: None)
    app.state["react_after"] = {"name": "dance", "repeat": None}
    app.after_turn("you didn't dance", {"reply": "Sorry!", "activity": ["Acted it out"]})
    assert played == ["dance"]


def test_go_back_to_sleep_is_understood():
    from nova.audio import voice
    assert app.wants_sleep(S + "Noah, go back to sleep.")
    assert voice.EARS_SLEEP_RE.search("noah, go back to sleep.")
