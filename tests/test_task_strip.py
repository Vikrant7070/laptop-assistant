"""The live task strip (manager plan 4-pre c): task events, GET /api/task, its Continue / Drop buttons, the page."""
import json
import time

import pytest

from nova import db, store, task_state as ts
from nova.core import app
from nova.core.app import eventbus


@pytest.fixture
def trusted():
    s = app.settings()
    s["autonomy"] = "trusted"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


def _saved(goal, status="stopped", age=0, runs=(("get_system_status", "Disk 77%"),)):
    card = ts.new(goal, tid=f"{time.time() - age:.6f}")
    for tool, out in runs:
        ts.record(card, {"name": tool, "args": {}}, out, True)
    ts.mark(card, status, "test")
    card["updated"] -= age
    db.save_task(card)
    return card


def _task_events(after=0):
    q = eventbus.bus.subscribe(after)
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev["kind"] == "task":
            out.append(ev["data"])
    eventbus.bus.unsubscribe(q)
    return out


def test_every_step_and_status_change_is_published(brain, trusted):
    brain([("", [("click_element", {"name": "a"})]), ("All done.", [])], tool_output="Clicked it")
    app.answer("tidy up my desktop icons")
    evs = _task_events()
    assert evs and all(e["goal"] == "tidy up my desktop icons" for e in evs)
    assert any(e["status"] == "running" and e["now"] == "click_element a" for e in evs)       # while it ran
    assert evs[-1]["status"] == "done" and evs[-1]["actions"] == [] and evs[-1]["runs"][0]["result"] == "Clicked it"
    assert evs[-1]["brain"]


def test_api_task_shows_the_running_task_then_the_one_to_carry_on(brain, trusted, client):
    assert client.get("/api/task").get_json() == {}
    brain([("", [("click_element", {"name": "a"})]), ("All done.", [])])
    app.answer("tidy up my desktop icons")
    assert client.get("/api/task").get_json()["status"] == "done"             # just finished: still shown
    app.state["task"]["card"]["updated"] -= 60
    old = _saved("clean my laptop", status="interrupted")
    j = client.get("/api/task").get_json()
    assert j["id"] == old["id"] and j["actions"] == ["continue", "drop"] and j["why"] == "test"


def test_drop_takes_a_task_out_of_continue(brain, client):
    card = _saved("clean my laptop")
    j = client.post("/api/task/drop", json={"id": card["id"]}).get_json()
    assert j == {}                                                         # nothing left to show
    assert db.load_tasks()[0]["status"] == "dropped"
    fake, _ = brain([("Okay!", [])])
    app.answer("continue")
    assert app.state["task"]["goal"] == "continue"                          # an ordinary request now


def test_continue_on_the_strip_picks_that_task_even_with_two_stopped(brain, client):
    first = _saved("sort my photos", age=60)
    _saved("sort my downloads")
    assert client.post("/api/task/pick", json={"id": first["id"]}).get_json() == {"ok": True}
    fake, _ = brain([("All sorted.", [])])
    out, _ = app.answer("continue")                                        # what the page sends after the pick
    assert out["reply"] == "All sorted." and app.state["task"]["goal"] == "sort my photos"


def test_pick_refuses_a_task_that_cannot_be_carried_on(client):
    card = _saved("clean my laptop", status="done")
    assert client.post("/api/task/pick", json={"id": card["id"]}).status_code == 404
    assert client.post("/api/task/drop", json={"id": "nope"}).status_code == 404


def test_an_approval_shows_as_waiting(brain):
    brain([("", [("click_element", {"name": "Delete"})])])
    app.answer("click delete")
    assert _task_events()[-1]["status"] == "waiting" and _task_events()[-1]["why"] == "an approval"


def test_the_redo_question_shows_as_waiting_and_moving_on_stops_it(brain):
    card = ts.new("write a letter", tid="t1")
    rid = ts.start(card, {"name": "type_text", "args": {"text": "Dear Sam"}})
    ts.leave_open(card, rid)
    ts.mark(card, "stopped", "Stop pressed")
    db.save_task(card)
    brain([])
    app.answer("continue")
    saved = [c for c in db.load_tasks() if c["id"] == "t1"]
    assert saved[0]["status"] == "waiting" and "do that step again" in saved[0]["why"]
    brain([("Hi!", [])])
    app.answer("what's the weather like")
    saved = [c for c in db.load_tasks() if c["id"] == "t1"]
    assert saved[0]["status"] == "stopped" and saved[0]["why"] == "you moved on"


def test_nothing_secret_on_the_strip():
    card = ts.new("log in to my bank")
    ts.record(card, {"name": "type_text", "args": {"text": "password hunter2"}}, "Your one-time code is 123456", True)
    text = json.dumps(ts.strip(card))
    assert "hunter2" not in text and "123456" not in text


def test_the_page_has_the_strip_and_listens_for_it():
    static = app.ROOT / "static"
    page = (static / "index.html").read_text(encoding="utf-8")
    js = (static / "nova.js").read_text(encoding="utf-8")
    strip = (static / "taskstrip.js").read_text(encoding="utf-8")
    assert 'id="taskStrip"' in page and page.index("nova.js") < page.index("taskstrip.js")
    assert 'on("task", showTask)' in js and "setInterval(loadTask" in js
    assert "/api/task/pick" in strip and "/api/task/drop" in strip and 'send("continue")' in strip
    assert "innerHTML" not in strip                                         # only put(html`...`): escaped
    css = (static / "nova.css").read_text(encoding="utf-8")
    assert ".taskstrip.waiting" in css and "max-width:600px" in css
