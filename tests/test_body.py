"""Live 3D body: moves the brain invents (checked and saved) and her look."""
import pytest

from nova import store
from nova.actions import tools
from nova.avatar import avatar_client, body
from nova.core import app

SHRUG = [{"at": 0.7, "right_arm_out": 45, "left_arm_out": 45}, {"at": 0.3, "right_arm_out": 45, "eyes": "up",
                                                                  "head_tilt": 15, "symbol": "question"}]


@pytest.fixture(autouse=True)
def clean_files():
    for f in (store.current().moves, store.current().look):
        f.unlink(missing_ok=True)
    yield
    for f in (store.current().moves, store.current().look):
        f.unlink(missing_ok=True)


def test_clean_move_sorts_clamps_and_names():
    move, err = body.clean_move("Big Shrug!", 1.5, SHRUG + [{"at": 0.5, "right_arm_out": 999, "jump": -5}])
    assert not err and move["name"] == "big_shrug"
    assert [k["at"] for k in move["keys"]] == [0.3, 0.5, 0.7]
    assert move["keys"][1]["right_arm_out"] == body.ANGLES["right_arm_out"][1] and move["keys"][1]["jump"] == 0


def test_clean_move_accepts_keys_as_json_text():
    move, err = body.clean_move("wiggle", 1, '[{"at": 0.5, "lean_side": 10}]')
    assert not err and move["keys"][0]["lean_side"] == 10


@pytest.mark.parametrize("name,duration,keys,why", [
    ("", 1, SHRUG, "name"), ("dance", 1, SHRUG, "built-in"), ("x", "long", SHRUG, "duration"),
    ("x", 1, [], "list"), ("x", 1, [{"at": 0.5, "eyes": "sparkly"}], "eyes"), ("x", 1, [{"at": 0.5, "elbow": 3}], "sets nothing"),
])
def test_clean_move_explains_what_is_wrong(name, duration, keys, why):
    move, err = body.clean_move(name, duration, keys)
    assert move is None and why in err


def test_moves_are_saved_and_forgotten():
    move, _ = body.clean_move("shrug", 1.2, SHRUG)
    body.save_move(move)
    assert body.load_moves()["shrug"]["duration"] == 1.2
    assert body.forget_move("shrug") and body.load_moves() == {}


def test_look_preset_then_changes_are_saved():
    look, problems = body.change_look({"hair": "Hot Pink", "glasses": "yes"}, preset="forest")
    assert not problems and look["beanie"] and look["hair"] == "hotpink" and look["glasses"] is True
    assert body.load_look() == look


def test_look_reports_bad_colours_and_unknown_parts():
    look, problems = body.change_look({"hair": "rgb(1,2,3)", "wings": True})
    assert len(problems) == 2 and look["hair"] == body.DEFAULT_LOOK["hair"]


def test_change_look_tool_saves_even_without_the_body():
    out = tools.change_look(cat_ears=True, hoodie="teal")
    assert "cat ears" in out and "isn't on screen" in out
    assert body.load_look()["hoodie"] == "teal"


def test_new_move_tool_checks_the_move_first():
    assert "Error in new_move" in tools.new_move("dance", 1, SHRUG)
    assert "isn't on screen" in tools.new_move("shrug", 1, SHRUG)


def test_new_move_saves_only_when_it_played(monkeypatch):
    monkeypatch.setattr(avatar_client, "running", lambda: True)
    monkeypatch.setattr(avatar_client, "send", lambda *a, **k: {"ok": True, "played": False})
    assert "live 3D body is off" in tools.new_move("shrug", 1, SHRUG) and body.load_moves() == {}
    monkeypatch.setattr(avatar_client, "send", lambda *a, **k: {"ok": True, "played": True})
    assert "saved" in tools.new_move("shrug", 1, SHRUG) and "shrug" in body.load_moves()


def test_saved_move_beats_the_alias_and_is_listed():
    assert tools.reaction_name("shrug") == "confused"
    move, _ = body.clean_move("shrug", 1, SHRUG)
    body.save_move(move)
    assert tools.reaction_name("Shrug") == "shrug"
    assert "my own moves: shrug" in tools.react("moonwalk")


def test_chat_reaction_accepts_an_invented_move():
    move, _ = body.clean_move("salute", 1, [{"at": 0.5, "right_arm_forward": 140}])
    body.save_move(move)
    out = app.queue_reaction({"reaction": "salute"}, [])
    assert out.startswith("OK") and app.state["react_after"]["name"] == "salute"
    assert "new_move" in app.queue_reaction({"reaction": "moonwalk"}, [])


def test_new_move_in_a_chat_waits_until_she_has_spoken(monkeypatch):
    sent, played = [], []
    monkeypatch.setattr(app.avatar_client, "running", lambda: True)
    monkeypatch.setattr(app.avatar_client, "status", lambda: {"live": True})
    monkeypatch.setattr(app.avatar_client, "send", lambda do, **k: sent.append((do, k)))
    monkeypatch.setattr(app, "react_later", lambda name, wait_for_speech=True, repeat=None: played.append(name))
    out = app._execute_one({"name": "new_move", "args": {"name": "bounce", "duration": 2, "keys": SHRUG}},
                           True, [], "m")
    assert "after you finish speaking" in out
    assert [(do, k.get("play")) for do, k in sent] == [("move", False)]      # learned, not acted out yet
    assert "bounce" in body.load_moves() and not played
    app.after_turn("do a new move", {"reply": "Check this out!", "activity": []})
    assert played == ["bounce"]                                               # only now, after her answer


def test_new_move_in_a_chat_needs_live_3d(monkeypatch):
    monkeypatch.setattr(app.avatar_client, "running", lambda: True)
    monkeypatch.setattr(app.avatar_client, "status", lambda: {"live": False})
    app.state.pop("react_after", None)
    out = app._execute_one({"name": "new_move", "args": {"name": "bounce", "duration": 2, "keys": SHRUG}},
                           True, [], "m")
    assert "live 3D body is off" in out and "react_after" not in app.state and body.load_moves() == {}
