"""The Judge (manager plan phase 1): checks made for the goal, and the order of trust - a failed deterministic check
means not done (vision can't overrule it), passing checks that cover the goal mean done with no screen look, and the
screen look runs at most once, at the end, as advice only."""
import json

import pytest

from nova.actions import checks, judge
from nova.core import app


# --- goal -> checks -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("goal, want, covers", [
    ("open Spotify", [("window_open", "spotify")], True),
    ("hey nova, can you please open my downloads folder", [("window_open", "downloads")], True),
    ("go to youtube.com", [("window_open", "youtube")], True),
    ("launch the calculator app", [("window_open", "calculator")], True),
    ("turn on dark mode", [("dark_mode", "on")], True),
    ("switch to light mode", [("dark_mode", "off")], True),
    ("turn off dark mode", [("dark_mode", "off")], True),
    ("open youtube in full screen", [("window_open", "youtube"), ("fullscreen", "")], True),
    ("open notepad and type hello", [("window_open", "notepad")], False),     # only part of the goal is checkable
    ("open calculator and paint", [("window_open", "calculator"), ("window_open", "paint")], True),   # "open" carries
    ("open notepad, paint and type hi", [("window_open", "notepad"), ("window_open", "paint")], False),
    ("play some lofi music", [], False),
    ("open it", [], False),                                                   # too vague for a title check
    ("open a new tab", [], False),
])
def test_goal_checks(goal, want, covers):
    made = judge.goal_checks(goal)
    assert [(c["check"], c["arg"]) for c in made["checks"]] == want
    assert made["covers"] is covers


def test_brain_proposed_checks_are_validated_not_fixed():
    text = '- window_open "WhatsApp"\n2. page_has "Chats"\nclick_it "Send"\nresult_has "x"\npage_has\nfullscreen'
    got = judge.parse_proposed(text)
    assert got == [{"check": "window_open", "arg": "WhatsApp"}, {"check": "page_has", "arg": "Chats"},
                   {"check": "fullscreen", "arg": ""}]          # unknown, result_has and arg-less page_has dropped
    assert judge.parse_proposed("NONE") == []
    assert len(judge.parse_proposed("\n".join(f'window_open "w{i}"' for i in range(6)))) == 3


# --- the order of trust ---------------------------------------------------------------------------------------

def _r(ok, check="window_open", arg="Spotify"):
    return {"check": check, "arg": arg, "ok": ok, "seen": "seen"}


def test_failed_check_beats_vision():
    v = judge.decide([_r(False)], covers=True, vision=True)
    assert v["done"] is False and v["source"] == "checks" and not v["advisory"]
    assert not judge.needs_vision([_r(False)], True)


def test_passing_checks_that_cover_the_goal_need_no_look():
    assert judge.decide([_r(True)], covers=True)["done"] is True
    assert not judge.needs_vision([_r(True)], True)


def test_partial_checks_leave_it_to_an_advisory_look():
    assert judge.needs_vision([_r(True)], covers=False)
    v = judge.decide([_r(True)], covers=False, vision=False)
    assert v["done"] is False and v["source"] == "vision" and v["advisory"]
    assert judge.decide([], covers=False)["done"] is None


def test_record_shape():
    v = judge.decide([_r(True)], covers=True)
    rec = judge.record("open Spotify", [{"check": "window_open", "arg": "Spotify"}], [_r(True)], "", v)
    assert set(rec) == {"goal", "checks", "results", "vision_note", "verdict", "source"}
    assert rec["checks"] == ['window_open "Spotify"'] and rec["verdict"] is True and rec["source"] == "checks"


# --- in the task loop -----------------------------------------------------------------------------------------

@pytest.fixture
def looks(monkeypatch):
    """Screen looks allowed and counted; each returns `answer` ('yes' / 'no'). Clicks run without an Allow box."""
    s = app.settings()
    s["autonomy"] = "trusted"
    app.SETTINGS_FILE.write_text(json.dumps(s), encoding="utf-8")
    seen = {"n": 0, "answer": "yes"}

    def fake_see(img, question, preferred=None, kind="camera"):
        seen["n"] += 1
        return f"SCREEN: a window\nSTEP WORKED: yes\nTASK DONE: {seen['answer']}"
    monkeypatch.setattr(app.tools, "_screen_allowed", lambda: True)
    monkeypatch.setattr(app.tools, "_grab_screen_jpeg", lambda: "img")
    monkeypatch.setattr(app.llm, "see", fake_see)
    return seen


def test_four_step_task_makes_at_most_one_look(brain, looks):
    steps = [("", [("click_element", {"name": f"b{i}"})]) for i in range(4)]
    brain(steps + [("All done.", [])])
    out, _ = app.answer("tidy up my desktop icons")
    assert looks["n"] <= 1 and out["reply"] == "All done."


def test_covering_checks_mean_no_look_at_all(brain, looks):
    brain([("", [("open_app", {"name": "spotify"})]), ("", [("click_element", {"name": "x"})]), ("Opened it.", [])])
    out, _ = app.answer("open Spotify")
    assert looks["n"] == 0 and out["reply"] == "Opened it."
    assert app.state["task"]["verdict"]["source"] == "checks" and app.state["task"]["verdict"]["verdict"] is True


def test_failed_window_check_cannot_be_made_done_by_vision(brain, looks, monkeypatch):
    monkeypatch.setattr(checks, "evaluate", lambda c, ctx=None, **k: (False, "no such window"))
    looks["answer"] = "yes"                    # the screen look would happily say it's done
    brain([("", [("open_app", {"name": "spotify"})]), ("Spotify is open!", []),
           ("Spotify is open now!", [])])
    out, _ = app.answer("open Spotify")
    assert looks["n"] == 0                                         # a failed check isn't sent to vision at all
    assert out["reply"] == 'I don\'t think I finished that - no "spotify" window is open. Want me to try again?'
    assert app.state["task"]["done"] is False and app.state["task"]["nudged"]


def test_cloud_brain_is_held_to_a_failed_check_too(brain, looks, monkeypatch):
    monkeypatch.setattr(checks, "evaluate", lambda c, ctx=None, **k: (False, "no such window"))
    monkeypatch.setattr(app, "brain", lambda s: "cloud:google:gemini-test")
    monkeypatch.setattr(app.cloud, "parse", lambda m: ("google", "gemini-test") if str(m).startswith("cloud:") else None)
    brain([("", [("open_app", {"name": "spotify"})]), ("Done!", []), ("Done!", [])])
    out, _ = app.answer("open Spotify")
    assert "don't think I finished" in out["reply"]


def test_vision_no_only_nudges_then_asks(brain, looks):
    looks["answer"] = "no"
    brain([("", [("click_element", {"name": "a"})]), ("", [("click_element", {"name": "b"})]),
           ("Your playlist is playing.", []), ("Your playlist is playing.", [])])
    out, _ = app.answer("play my workout playlist")
    assert looks["n"] == 1                                         # one look, not one per step
    assert out["reply"].startswith("Your playlist is playing.")    # her answer is kept, not overruled
    assert out["reply"].endswith("did it?")


def test_brain_proposed_checks_are_used(brain, looks, monkeypatch):
    monkeypatch.setattr(app, "ask_for_checks", lambda goal, windows, model: 'page_has "Inbox"')
    brain([("", [("click_element", {"name": "a"})]), ("", [("click_element", {"name": "b"})]), ("There you go.", [])])
    out, _ = app.answer("show me my unread mail in outlook")
    task = app.state["task"]
    assert task["checks"] == [{"check": "page_has", "arg": "Inbox"}] and task["covers"] and task["strict"] is False
    assert looks["n"] == 0 and task["verdict"]["source"] == "checks"


def test_failed_brain_check_is_only_advice(brain, looks, monkeypatch):
    # Live 2026-11-20: the small brain proposed front_window "Untitled - Notepad", which typing renames - it could
    # never pass, and treating it as the truth made Nova retype the text again and again.
    monkeypatch.setattr(app, "ask_for_checks", lambda goal, windows, model: 'front_window "Untitled - Notepad"')
    monkeypatch.setattr(checks, "evaluate", lambda c, ctx=None, **k: (False, "in front: 'Claude'"))
    fake, _ = brain([("", [("type_text", {"text": "hi", "into_window": "Untitled - Notepad"})]),
                     ("", [("press_keys", {"keys": "enter", "into_window": "Untitled - Notepad"})]),
                     ("Typed it.", [])])
    out, _ = app.answer("type hi in notepad")
    tool_results = " ".join(str(m.get("content", "")) for m in fake.seen[-1])
    assert "Not true yet" not in tool_results                    # a brain check doesn't drive more steps
    assert looks["n"] == 1 and out["reply"] == "Typed it."        # the one advisory look said yes
    assert app.state["task"]["verdict"]["source"] == "vision"
    assert app.state["task"]["window"] == "Untitled - Notepad"   # page checks look at the window she worked in
