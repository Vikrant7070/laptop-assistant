"""Save and resume tasks (manager plan 4-pre b): restart, safe resume, one task at a time, cut-off replies."""
import json
import time

import pytest

from nova import db, store, task_state as ts
from nova.actions import checks
from nova.core import app
from nova.core.app import resume


@pytest.fixture
def trusted():
    s = app.settings()
    s["autonomy"] = "trusted"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


def _stopped(goal, runs=(), plan=(), status="stopped", app_="", age=0):
    """A saved card, as an earlier session left it."""
    card = ts.new(goal, plan, "deep brain" if plan else "none", tid=f"{time.time() - age:.6f}")
    for tool, args, out, ok in runs:
        rid = ts.start(card, {"name": tool, "args": args})
        if out is None:
            ts.leave_open(card, rid)          # Stop / restart in the middle of this step
        else:
            ts.finish(card, out, ok, run=rid)
    card["app"] = app_
    ts.mark(card, status, "test")
    card["updated"] -= age
    db.save_task(card)
    return card


def _user_notes(fake):
    return [m["content"] for m in fake.seen[0] if m["role"] == "user"]


# --- saved cards ------------------------------------------------------------------------------------------------

def test_a_card_is_saved_and_a_restart_marks_it_interrupted(brain, trusted):
    brain([("", [("click_element", {"name": "a"})]), ("", [("click_element", {"name": "b"})]), ("All done.", [])])
    app.answer("tidy up my desktop icons")
    card = app.state["task"]["card"]
    saved = db.load_tasks()[0]
    assert saved["id"] == card["id"] and saved["status"] == "done" and len(saved["runs"]) == 2
    saved["status"] = "running"                       # as if Nova had died in the middle of it
    db.save_task(saved)
    app.state.update(messages=[], pending=None)
    app.state.pop("task")
    line = resume.on_start()
    assert line == "Before the restart I was on “tidy up my desktop icons”. Should I continue?"
    assert db.load_tasks()[0]["status"] == "interrupted"
    fake, _ = brain([("Carrying on - done now.", [])])
    out, _ = app.answer("yes")
    assert out["reply"] == "Carrying on - done now."
    asked = _user_notes(fake)[-1]
    assert asked.startswith("yes") and "carry on with this task" in asked and "TASK: tidy up my desktop icons" in asked
    assert app.state["task"]["goal"] == "tidy up my desktop icons"
    assert app.state["task"]["card"]["id"] == card["id"]


def test_she_never_carries_on_by_herself_after_a_restart(brain):
    _stopped("clean my laptop", [("get_system_status", {}, "Disk 77%", True)], status="running")
    assert resume.on_start().startswith("Before the restart I was on")
    fake, _ = brain([("Hi!", [])])
    app.answer("hello")                               # not an answer to "should I continue?"
    assert app.state["task"]["goal"] == "hello" and "carry on" not in _user_notes(fake)[-1]
    assert db.load_tasks(statuses=("interrupted",))[0]["goal"] == "clean my laptop"     # still there for later


def test_an_old_card_version_loads_and_a_newer_one_is_read_only():
    old = {"goal": "open notepad", "status": "stopped", "runs": [{"tool": "open_app", "state": "done"}]}
    card = ts.upgrade(old)
    assert card["version"] == ts.VERSION and card["runs"][0]["id"] == 1 and ts.resumable(card)
    assert "open_app" in ts.render(card)
    newer = ts.upgrade({"goal": "x", "version": ts.VERSION + 1, "status": "stopped", "runs": [{"tool": "a"}]})
    assert newer["read_only"] and not ts.resumable(newer)
    assert ts.upgrade({"no": "goal"}) is None


# --- safe resume --------------------------------------------------------------------------------------------------

def test_a_half_done_changing_step_is_never_redone_without_asking(brain):
    _stopped("write a letter in notepad", [("open_app", {"name": "notepad"}, "Opened Notepad", True),
                                           ("type_text", {"text": "Dear Sam"}, None, None)])
    fake, _ = brain([])
    out, _ = app.answer("continue")
    assert "in the middle of a step (type_text Dear Sam)" in out["reply"] and out["reply"].endswith("again?")
    assert fake.seen == []                            # code asked, no brain turn
    fake, _ = brain([("Typed it.", [])])
    out, _ = app.answer("yes")
    assert out["reply"] == "Typed it."
    assert "redo the half-done step" in _user_notes(fake)[-1]


def test_no_to_the_redo_question_skips_that_step(brain):
    _stopped("write a letter in notepad", [("type_text", {"text": "Dear Sam"}, None, None)])
    brain([])
    app.answer("continue")
    fake, _ = brain([("Okay, skipping it.", [])])
    app.answer("no")
    assert "NOT to redo" in _user_notes(fake)[-1]
    assert app.state["task"]["card"]["runs"][0]["state"] == "skipped"


def test_a_half_done_read_only_step_is_simply_redone(brain):
    _stopped("check my disk", [("get_system_status", {}, None, None)])
    fake, _ = brain([("Disk is fine.", [])])
    out, _ = app.answer("continue")
    assert out["reply"] == "Disk is fine." and "carry on with this task" in _user_notes(fake)[-1]


def test_a_task_that_is_already_done_is_not_carried_on(brain):
    _stopped("open Spotify", [("open_app", {"name": "spotify"}, "Opened", True)])     # checks pass by default (conftest)
    fake, _ = brain([])
    out, _ = app.answer("continue")
    assert out["reply"].startswith("That's already done") and fake.seen == []
    assert db.load_tasks()[0]["status"] == "done"


def test_a_missing_window_on_resume_triggers_one_re_plan(brain, monkeypatch):
    monkeypatch.setattr(checks, "evaluate", lambda c, ctx=None, **k: (c["check"] != "window_open", "no such window"))
    plan = [{"text": "type the letter", "tool": "type_text"}, {"text": "save it", "tool": "press_keys"}]
    _stopped("write the letter", [("open_app", {"name": "notepad"}, "Opened", True)], plan=plan, app_="notepad")
    asked = []

    def make_plan(s, goal, on_think=None):
        asked.append(goal)
        return "1. Open Notepad again - open_app - window open\n2. Type the letter - type_text - text shows", "deep"
    monkeypatch.setattr(app.router, "make_plan", make_plan)
    fake, _ = brain([("On it.", [])])
    app.answer("continue")
    assert len(asked) == 1 and "notepad isn't open any more" in asked[0] and "step 1 type the letter: pending" in asked[0]
    assert "Opened" not in asked[0]                   # outcomes only, never raw tool results
    card = app.state["task"]["card"]
    assert card["replanned"] and [s["text"] for s in card["plan"]] == ["Open Notepad again", "Type the letter"]
    assert "plan above was redone" in _user_notes(fake)[-1]


# --- one laptop task at a time ------------------------------------------------------------------------------------

def test_continue_with_two_stopped_tasks_asks_which(brain):
    _stopped("clean my laptop", [("get_system_status", {}, "Disk 77%", True)], age=60)
    _stopped("sort my downloads", [("list_folder", {"path": "Downloads"}, "12 files", True)])
    fake, _ = brain([])
    out, _ = app.answer("carry on")
    assert out["reply"] == ("I stopped more than one task. Which one should I carry on with? "
                            "one: sort my downloads, two: clean my laptop, three: Not now.")
    assert fake.seen == []
    fake, _ = brain([("Cleaning now.", [])])
    app.answer("the second one")
    assert app.state["task"]["goal"] == "clean my laptop"


def test_a_new_request_leaves_the_waiting_task_stopped(brain):
    brain([("", [("click_element", {"name": "Delete"})])])
    out, _ = app.answer("click delete in my mail")
    assert out.get("pending")
    brain([("Hi!", [])])
    app.answer("hello")
    old = [c for c in db.load_tasks() if c["goal"] == "click delete in my mail"][0]
    assert old["status"] == "stopped" and old["why"] == "a new request came"


def test_continue_with_nothing_stopped_is_an_ordinary_request(brain):
    fake, _ = brain([("Okay!", []), ("Sure!", [])])
    app.answer("hello")
    app.answer("continue")
    assert app.state["task"]["goal"] == "continue"


# --- cut-off replies ----------------------------------------------------------------------------------------------

class Mouth:
    def __init__(self, lines):
        self.said_lines = lines


def test_continue_after_a_cut_off_says_the_rest_with_no_brain_turn(brain):
    reply = "Tea is very old. It came from China, long ago. People everywhere love it now."
    brain([(reply, [])])
    app.answer("tell me about tea")
    t0 = app.state["last_reply"]["t0"]
    resume.cut_off(t0 + 5, Mouth([(t0 + 1, t0 + 2, "Tea is very old."), (t0 + 2, t0 + 7, "It came from China, long ago.")]))
    stored = app.state["messages"][-1]["content"]
    assert stored == "Tea is very old. " + resume.CUT_MARK      # only what was really said
    fake, _ = brain([])
    out, _ = app.answer("continue, you were saying something")
    assert out["reply"] == "It came from China, long ago. People everywhere love it now."
    assert fake.seen == []
    assert [m["content"] for m in app.state["messages"][-3:]] == [stored, "continue, you were saying something",
                                                                   out["reply"]]       # the way it happened


def test_no_cut_off_when_she_wasnt_saying_that_reply(brain):
    brain([("Tea is very old. It came from China.", [])])
    app.answer("tell me about tea")
    t0 = app.state["last_reply"]["t0"]
    resume.cut_off(t0 + 9, Mouth([(t0 + 1, t0 + 2, "Tea is very old.")]))      # a gap: nothing playing at the stop
    resume.cut_off(t0 + 1.5, Mouth([]))                                         # voice off / nothing played
    assert app.state["messages"][-1]["content"] == "Tea is very old. It came from China."
    assert "cutoff" not in app.state


def test_unsaid_part_finds_where_she_stopped():
    r = "One thing. Second thing here! And a third."
    assert resume.unsaid_part(r, []) == r
    assert resume.unsaid_part(r, ["One thing."]) == "Second thing here! And a third."
    assert resume.unsaid_part(r, ["One thing.", "Second thing here!"]) == "And a third."
    assert resume.unsaid_part(r, ["Something else entirely."]) is None


# --- the chat after a restart -------------------------------------------------------------------------------------

def test_the_chat_comes_back_after_a_restart_without_hidden_notes_or_secrets(brain):
    brain([("Hello Asha!", []), ("Noted.", [])])
    app.answer("hi, I'm Asha")
    app.answer("my password is hunter2")
    saved = store.current().chat_tail.read_text(encoding="utf-8")
    assert "hunter2" not in saved and "[open windows" not in saved
    app.state.update(messages=[])
    resume.on_start()
    assert [m["content"] for m in app.state["messages"]] == ["hi, I'm Asha", "Hello Asha!", "Noted."]


def test_a_reset_forgets_the_saved_chat(brain, client):
    brain([("Hello!", [])])
    app.answer("hi")
    assert store.current().chat_tail.exists()
    client.post("/api/reset")
    assert not store.current().chat_tail.exists()
