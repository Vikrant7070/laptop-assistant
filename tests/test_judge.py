"""The Judge (manager plan phase 1): checks made for the goal, and the order of trust - a failed deterministic check
means not done (vision can't overrule it), passing checks that cover the goal mean done with no screen look, and the
screen look runs at most once, at the end, as advice only."""
import json

import pytest

from nova import store
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
    # Part B audit: the common ways people ask for the four high-traffic goals all get a check...
    ("start notepad", [("window_open", "notepad")], True),
    ("open up the file explorer", [("window_open", "file explorer")], True),  # not "explorer": that runs the taskbar
    ("open explorer", [("window_open", "file explorer")], True),
    ("open windows explorer", [("window_open", "file explorer")], True),      # never in a Windows 11 title
    ("open internet explorer", [("window_open", "internet explorer")], True),
    ("visit github.com", [("window_open", "github")], True),
    ("make youtube full screen", [("fullscreen", "")], True),
    ("enable dark theme", [("dark_mode", "on")], True),
    ("switch windows to dark mode", [("dark_mode", "on")], True),
    ("make windows dark", [("dark_mode", "on")], True),
    ("change the theme to dark", [("dark_mode", "on")], True),
    ("go back to light theme", [("dark_mode", "off")], True),
    ("switch everything to light mode", [("dark_mode", "off")], True),
    # ...and the near misses don't
    ("open a file", [], False),                                               # "file" alone is too vague
    ("make it darker", [], False),                                            # brightness, not the colour mode
    ("make the screen darker", [], False),
    ("dark mode please", [], False),                                          # no verb: left to the screen look
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


# Every combination, in one table: (results, covers, strict, vision) -> may it look?, done, source.
# A failed code-made check is final; a failed brain-proposed check is only "not sure"; passing checks that cover the
# goal are final; otherwise the screen look decides, and only as advice.
@pytest.mark.parametrize("results, covers, strict, vision, looks, done, source", [
    ([], False, True, None, True, None, "none"),               # nothing checkable, no look happened
    ([], False, True, True, True, True, "vision"),
    ([], False, True, False, True, False, "vision"),
    ([_r(True)], True, True, None, False, True, "checks"),     # covering checks passed: done, no look
    ([_r(True)], True, True, False, False, True, "checks"),    # ...even if a look had said no
    ([_r(True)], False, True, True, True, True, "vision"),     # partial checks passed: the look decides
    ([_r(True)], False, True, None, True, None, "none"),
    ([_r(False)], True, True, True, False, False, "checks"),   # code check failed: not done, whatever the look says
    ([_r(False)], False, True, None, False, False, "checks"),
    ([_r(True), _r(False, "fullscreen", "")], True, True, True, False, False, "checks"),   # one failure is enough
    ([_r(True)], True, False, None, False, True, "checks"),    # brain checks that pass still prove it
    ([_r(False)], True, False, True, True, True, "vision"),    # a failed brain check is only "not sure"
    ([_r(False)], True, False, None, True, None, "none"),
])
def test_decision_matrix(results, covers, strict, vision, looks, done, source):
    assert judge.needs_vision(results, covers, strict) is looks
    v = judge.decide(results, covers, vision, strict)
    assert (v["done"], v["source"]) == (done, source)
    assert v["advisory"] is (source != "checks")


@pytest.mark.parametrize("value, on", [(None, True), ("1", True), ("0", False), (" 0 ", False), ("yes", True)])
def test_vision_switch(monkeypatch, value, on):
    if value is None:
        monkeypatch.delenv(judge.VISION_ENV, raising=False)
    else:
        monkeypatch.setenv(judge.VISION_ENV, value)
    assert judge.vision_on() is on


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
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")
    seen = {"n": 0, "answer": "yes"}

    def fake_see(img, question, preferred=None, kind="camera"):
        seen["n"] += 1
        return f"SCREEN: a window\nSTEP WORKED: yes\nTASK DONE: {seen['answer']}"
    monkeypatch.setattr(app.tools.eyes, "_screen_allowed", lambda: True)
    monkeypatch.setattr(app.tools.eyes, "_grab_screen_jpeg", lambda: "img")
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
    monkeypatch.setattr(app.tools.windows, "_windows", lambda: [(1, "Untitled - Notepad", "Notepad.exe")])
    fake, _ = brain([("", [("type_text", {"text": "hi", "into_window": "Untitled - Notepad"})]),
                     ("", [("press_keys", {"keys": "enter", "into_window": "Untitled - Notepad"})]),
                     ("Typed it.", [])])
    out, _ = app.answer("type hi in notepad")
    tool_results = " ".join(str(m.get("content", "")) for m in fake.seen[-1])
    assert "Not true yet" not in tool_results                    # a brain check doesn't drive more steps
    assert looks["n"] == 1 and out["reply"] == "Typed it."        # the one advisory look said yes
    assert app.state["task"]["verdict"]["source"] == "vision"
    assert app.state["task"]["window"] == "Untitled - Notepad"   # page checks look at the window she worked in


def test_vision_switch_off_means_no_look_and_her_answer_stands(brain, looks, monkeypatch):
    # NOVA_VISION_CHECK=0 (A/B runs): the task that would get its one advisory look gets none, nothing nudges her,
    # and the task log says why.
    monkeypatch.setenv(judge.VISION_ENV, "0")
    looks["answer"] = "no"                         # had it looked, it would have pushed back
    brain([("", [("click_element", {"name": "a"})]), ("", [("click_element", {"name": "b"})]),
           ("Your playlist is playing.", [])])
    out, _ = app.answer("play my workout playlist")
    assert looks["n"] == 0 and out["reply"] == "Your playlist is playing."
    assert app.state["task"]["verdict"]["source"] == "none" and not app.state["task"]["nudged"]
    assert f"FINAL CHECK skipped: {judge.VISION_ENV}=0" in store.current().task_log.read_text(encoding="utf-8")


def test_vision_switch_off_keeps_the_checks(brain, looks, monkeypatch):
    # The switch only removes the screen look: a failed deterministic check still means "not done".
    monkeypatch.setenv(judge.VISION_ENV, "0")
    monkeypatch.setattr(checks, "evaluate", lambda c, ctx=None, **k: (False, "no such window"))
    brain([("", [("open_app", {"name": "spotify"})]), ("Spotify is open!", []), ("Spotify is open now!", [])])
    out, _ = app.answer("open Spotify")
    assert looks["n"] == 0 and "don't think I finished" in out["reply"]
