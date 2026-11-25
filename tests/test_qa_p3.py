"""QA plan P3 (2026-11-25): "switch to the calculator" was answered "You're already in Calculator" without looking;
"what can you do?" got one vague line."""
from nova.core import app
from nova.core.app import body


def test_switch_to_an_open_window_is_done_by_code(brain, desktop):
    desktop.set_windows([(1, "Claude", "claude.exe"), (2, "Calculator", "CalculatorApp.exe")], front=1)
    fake, ran = brain([("You're in Calculator now.", [])])
    app.answer("switch to the calculator")
    assert ran[0] == ("window_action", {"title_or_app": "calculator", "action": "focus"})


def test_switch_to_something_with_no_window_is_left_to_the_brain(brain, desktop):
    desktop.set_windows([(1, "Claude", "claude.exe")], front=1)
    fake, ran = brain([("", [("set_dark_mode", {"enabled": True})]), ("Dark mode is on.", [])])
    app.answer("switch to dark mode")
    assert ran == [("set_dark_mode", {"enabled": True})]


def test_what_can_you_do_is_a_plain_list_without_the_brain(brain):
    fake, ran = brain([("I can do lots of things!", [])])
    out, _ = app.answer("what can you do?")
    assert out["reply"] == body.CAPABILITIES_REPLY and not ran and not fake.seen
    assert "ask before deleting" in out["reply"]


def test_a_specific_question_still_goes_to_the_brain(brain):
    fake, ran = brain([("I can make folders, move and rename files.", [])])
    out, _ = app.answer("what can you do with files")
    assert fake.seen and out["reply"].startswith("I can make folders")
