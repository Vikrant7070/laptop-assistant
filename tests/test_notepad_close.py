"""QA plan P2-c: closing an app with unsaved work (QA 2026-11-25: Windows 11 Notepad asks inside its own window, so
Nova said "Closed", the read-back found it still open, and the brain tried taskkill in a terminal three times)."""
import json

from nova import store
from nova.actions.tools import forms, windows
from nova.core import app

NOTEPAD = (7, "*Untitled - Notepad", "notepad.exe")
PROMPT = [("Save", 100, 200), ("Don't save", 180, 200), ("Cancel", 260, 200)]


def _asks_inside(desktop, monkeypatch):
    """Notepad stays open on close and shows Save / Don't save / Cancel in its own window."""
    desktop.set_windows([NOTEPAD], front=7)
    clicked = []
    monkeypatch.setattr(desktop, "close", lambda hwnd: desktop.log.append(("close", hwnd)))     # it asks instead
    monkeypatch.setattr(forms, "buttons_in", lambda hwnd: PROMPT if desktop.is_window(hwnd) else [])

    def click(x, y, *a, label="", **k):
        clicked.append(label)
        if label == "Don't save":
            desktop.alive.discard(7)
            desktop.scene = []
    monkeypatch.setattr(forms, "_nova_click", click)
    return clicked


def test_a_save_prompt_inside_the_window_is_seen_and_left_to_the_user(desktop, monkeypatch):
    clicked = _asks_inside(desktop, monkeypatch)
    out = windows.window_action("notepad", "close")
    assert "asking whether to save" in out and "no terminal, taskkill" in out
    assert clicked == [] and desktop.is_window(7)                      # nothing thrown away


def test_without_saving_presses_dont_save(desktop, monkeypatch):
    clicked = _asks_inside(desktop, monkeypatch)
    out = windows.window_action("notepad", "close", discard_changes=True)
    assert out == "Closed '*Untitled - Notepad' without saving." and clicked == ["Don't save"]
    assert not desktop.is_window(7)


def test_a_window_with_nothing_to_save_just_closes(desktop, monkeypatch):
    desktop.set_windows([(8, "notes.txt - Notepad", "notepad.exe")], front=8)
    monkeypatch.setattr(forms, "buttons_in", lambda hwnd: [])
    assert windows.window_action("notepad", "close") == "Closed 'notes.txt - Notepad'."


def _trusted():
    s = app.settings()
    s["autonomy"] = "trusted"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


def test_the_brain_cant_discard_work_the_user_didnt_say_to(brain, desktop, monkeypatch):
    _trusted()
    clicked = _asks_inside(desktop, monkeypatch)
    ran = []
    real = app.tools.run_tool
    brain([("", [("window_action", {"title_or_app": "notepad", "action": "close", "discard_changes": True})]),
           ("It's asking whether to save.", []), ("It's asking.", [])])
    monkeypatch.setattr(app.tools, "run_tool", lambda n, a: (ran.append((n, dict(a))), real(n, a))[1])
    app.answer("close notepad")
    assert ran[0][1]["discard_changes"] is False and clicked == [] and desktop.is_window(7)


def test_close_without_saving_in_the_users_words_goes_through(brain, desktop, monkeypatch):
    _trusted()
    clicked = _asks_inside(desktop, monkeypatch)
    real = app.tools.run_tool
    brain([("", [("window_action", {"title_or_app": "notepad", "action": "close", "discard_changes": True})]),
           ("Closed it without saving.", [])])
    monkeypatch.setattr(app.tools, "run_tool", lambda n, a: real(n, a))
    app.answer("close notepad without saving")
    assert clicked == ["Don't save"] and not desktop.is_window(7)


def test_the_read_back_checks_the_window_it_closed_not_any_notepad(desktop, monkeypatch):
    from nova.actions import checks, readback
    monkeypatch.setattr(checks, "evaluate", lambda c, ctx=None, timeout=0, **k: getattr(checks, c["check"])(c.get("arg", ""), ctx or {}))
    # QA 2026-11-25: two Notepad windows; one closed, "is any Notepad open?" stayed yes and she kept retrying.
    desktop.set_windows([(9, "*Research - weather in delhi.txt - Notepad", "notepad.exe")], front=9)
    rb = readback.read_back("window_action", {"title_or_app": "notepad", "action": "close"},
                            "Closed '*step one done - Notepad'.")
    assert rb["state"] == "passed" and "step one done" in rb["why"]
    rb = readback.read_back("window_action", {"title_or_app": "notepad", "action": "close"},
                            windows.ASKS_TO_SAVE.format(title="*Research - weather in delhi.txt - Notepad"))
    assert rb["state"] == "skipped"                                      # waiting for the user's choice, not failed
