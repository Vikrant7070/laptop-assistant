"""The task card (manager plan 4-pre a): written by code from real tool results, shown to the brain on every step."""
import json

import pytest

from nova import store, task_state as ts
from nova.core import app

KNOWN = {"get_system_status", "clean_temp_files", "empty_recycle_bin", "open_app", "type_text"}

PLAN = """1. Check how full the disk is - get_system_status - shows the disk %
2. Clean temp files - clean_temp_files - says how much was freed
3. Defragment the drive - defrag_drive - drive optimised
4. Empty the recycle bin - empty_recycle_bin - bin is empty
5. Open Chrome to show the result - Chrome - a Chrome window is open
RISKS: deleting the wrong files.
DONE WHEN: the user is told how much space was freed."""


def _call(tool, **args):
    return {"name": tool, "args": args}


# --- plans -----------------------------------------------------------------------------------------------------

def test_unknown_tools_are_dropped_from_a_plan():
    steps = ts.steps_from_plan(PLAN, KNOWN)
    assert [s["tool"] for s in steps] == ["get_system_status", "clean_temp_files", "empty_recycle_bin", ""]
    assert all("Defragment" not in s["text"] for s in steps)          # defrag_drive isn't one of Nova's tools
    assert steps[-1]["text"] == "Open Chrome to show the result"       # an app in the tool slot is fine
    assert not any("RISKS" in s["text"] or "DONE" in s["text"] for s in steps)


def test_skill_steps_become_the_plan():
    skill = {"steps": [{"tool": "open_app", "args": {"name": "notepad"}}, {"say": "hi"},
                       {"tool": "type_text", "args": {"text": "hello"}}]}
    assert ts.steps_from_skill(skill) == [{"text": "open_app notepad", "tool": "open_app"},
                                          {"text": "type_text hello", "tool": "type_text"}]


# --- only real tool runs tick a step ----------------------------------------------------------------------------

def test_card_is_built_from_real_results_only():
    card = ts.new("clean my laptop", ts.steps_from_plan(PLAN, KNOWN), "deep brain")
    rid = ts.start(card, _call("get_system_status"))
    assert card["plan"][0]["state"] == "started"
    ts.finish(card, "Disk C: 77% full\nmore lines", True, run=rid)
    rid = ts.start(card, _call("clean_temp_files"))
    ts.finish(card, "Error: access denied", False, run=rid)
    text = ts.render(card)
    assert "1 Check how full the disk is ✅" in text and "2 Clean temp files ❌" in text
    assert "DONE (really): get_system_status → Disk C: 77% full" in text
    assert "FAILED: clean_temp_files → Error: access denied" in text
    assert "NOW: step 2 - Clean temp files" in text
    assert text.startswith("TASK: clean my laptop\nPLAN (deep brain): ")


def test_a_failed_retry_never_undoes_a_step_that_worked():
    card = ts.new("x", [{"text": "open it", "tool": "open_app"}])
    ts.record(card, _call("open_app", name="a"), "Opened a", True)
    ts.record(card, _call("open_app", name="a"), "Error", False)
    assert card["plan"][0]["state"] == "done"


def test_not_run_or_declined_is_skipped_and_the_step_stays_to_do():
    card = ts.new("x", [{"text": "type it", "tool": "type_text"}])
    rid = ts.start(card, _call("type_text", text="hi"))
    ts.finish(card, "The user declined this action. Do not do it.", False, run=rid)
    assert card["runs"][0]["state"] == "skipped" and card["plan"][0]["state"] == "pending"
    ts.record(card, _call("type_text", text="hi"), "Not done - nothing pressed: notepad isn't open", False)
    assert card["runs"][1]["state"] == "skipped" and "FAILED" not in ts.render(card)


def test_a_stopped_step_stays_started_not_confirmed():
    card = ts.new("type a letter", [{"text": "type it", "tool": "type_text"}])
    rid = ts.start(card, _call("type_text", text="Dear Sam"))
    ts.leave_open(card, rid)
    ts.finish(card, "ok", True, run=rid)                     # too late: Stop already left it unconfirmed
    assert card["runs"][0]["state"] == "started" and card["plan"][0]["state"] == "started"
    text = ts.render(card)
    assert "STARTED, NOT CONFIRMED: type_text Dear Sam" in text and "1 type it ⏳" in text


def test_runs_nest_like_a_skill_brain_step_inside_run_skill():
    card = ts.new("x")
    outer = ts.start(card, _call("run_skill", name="morning"))
    inner = ts.start(card, _call("open_app", name="mail"))
    ts.finish(card, "Opened Mail", True)                     # check_step: the newest open run
    ts.finish(card, "Opened Mail", True, "open_app", inner)  # execute afterwards: a no-op
    ts.finish(card, "Skill done", True, run=outer)
    assert [(r["tool"], r["state"]) for r in card["runs"]] == [("run_skill", "done"), ("open_app", "done")]
    assert card["open"] == []


def test_a_plan_arriving_later_ticks_what_already_ran():
    card = ts.new("clean my laptop")
    ts.record(card, _call("get_system_status"), "Disk 77%", True)
    ts.set_plan(card, ts.steps_from_plan(PLAN, KNOWN), "deep brain")
    assert card["plan"][0]["state"] == "done" and ts.current(card) == 1


def test_card_stays_under_its_size_limit_and_drops_the_oldest_results():
    card = ts.new("a very long task " * 30, ts.steps_from_plan(PLAN, KNOWN), "deep brain")
    for i in range(60):
        ts.record(card, _call("get_system_status"), f"result number {i} " + "x" * 200, True)
    text = ts.render(card)
    assert len(text) <= ts.MAX_CHARS
    assert "earlier)" in text and "result number 59" in text and "result number 0 " not in text
    assert len(card["runs"]) == ts.MAX_RUNS


def test_nothing_secret_on_the_card():
    card = ts.new("log in to my bank")
    ts.record(card, _call("type_text", text="my password is hunter2"), "Your one-time code is 123456", True)
    text = ts.render(card)
    assert "hunter2" not in text and "123456" not in text and ts._HIDDEN in text


def test_card_is_plain_json_with_a_version():
    card = ts.new("x", [{"text": "a", "tool": "open_app"}], "skill")
    ts.record(card, _call("open_app", name="a"), "ok", True)
    again = json.loads(ts.to_json(card))
    assert again["version"] == ts.VERSION and ts.render(again) == ts.render(card)
    assert ts.summary(card)["done"] == 1


# --- in the task loop ----------------------------------------------------------------------------------------

@pytest.fixture
def trusted():
    s = app.settings()
    s["autonomy"] = "trusted"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


def _tool_results(fake):
    return [m["content"] for m in fake.seen[-1] if m.get("role") == "tool" or str(m.get("content", "")).startswith("[")]


def test_the_brain_sees_the_card_after_each_step(brain, trusted):
    fake, _ = brain([("", [("click_element", {"name": "a"})]), ("", [("click_element", {"name": "b"})]),
                     ("All done.", [])], tool_output="Clicked it")
    app.answer("tidy up my desktop icons")
    seen = json.dumps(fake.seen[-1], ensure_ascii=False)
    assert "TASK: tidy up my desktop icons" in seen
    assert "DONE (really): click_element → Clicked it; click_element → Clicked it" in seen
    card = app.state["task"]["card"]
    assert [r["state"] for r in card["runs"]] == ["done", "done"] and card["status"] == "done"


def test_the_brains_own_done_claim_ticks_nothing(brain, trusted):
    brain([("All done, I opened it!", [])])
    app.answer("tidy up my desktop icons")
    card = app.state["task"]["card"]
    assert card["runs"] == [] and "DONE (really): nothing yet" in ts.render(card)


def test_an_error_result_is_a_failed_step(brain, trusted):
    brain([("", [("click_element", {"name": "a"})]), ("Sorry.", [])], tool_output="Error: no such button")
    app.answer("click the a button")
    assert app.state["task"]["card"]["runs"][0]["state"] == "failed"


def test_the_same_call_again_and_again_stops_and_asks(brain, trusted):
    brain([("", [("scroll_page_info", {"x": 1})])] * 6)
    out, _ = app.answer("find the setting")
    card = app.state["task"]["card"]
    # 4d: stuck -> the question card; the task waits for the answer ("Stuck at …")
    assert card["status"] == "waiting" and "Stuck at" in card["why"] and "scroll_page_info" in out["reply"]


def test_waiting_for_an_approval_shows_on_the_card(brain):
    brain([("", [("click_element", {"name": "Delete"})])])
    out, _ = app.answer("click delete")
    assert out.get("pending") and app.state["task"]["card"]["status"] == "waiting"
