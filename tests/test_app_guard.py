"""When a request names an app ("type hello judge in notepad"), keys that open or switch windows and typing only go
into that app. 2026-11-20 Notepad was closed and the small brain pressed alt+tab, ctrl+shift+n and ctrl+n in VS Code,
then typed into "Untitled-1 - Visual Studio Code". Windows here are fakes: no real window is read, no key pressed."""
import json

import pytest

from nova.actions import tools
from nova.core import app

VSCODE = (1, "Untitled-1 - Visual Studio Code", "Code.exe")
NOTEPAD = (2, "Untitled - Notepad", "Notepad.exe")
CLAUDE = (3, "Claude", "claude.exe")


def trusted():
    """As on the laptop: typing and keys don't ask first."""
    s = app.settings()
    s["autonomy"] = "trusted"
    app.SETTINGS_FILE.write_text(json.dumps(s), encoding="utf-8")


@pytest.fixture
def desk(monkeypatch):
    """A pretend desktop: `desk["windows"]` front first."""
    d = {"windows": [VSCODE, CLAUDE]}
    monkeypatch.setattr(tools, "_windows", lambda: list(d["windows"]))
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    return d


@pytest.mark.parametrize("request_text, name", [
    ("type hello judge in notepad", "notepad"),
    ("write my shopping list into Notepad", "notepad"),
    ("open notepad and type hi", "notepad"),
    ("search cats in chrome", "chrome"),
    ("make a new sheet in excel", "excel"),
    ("type hello in vs code", "vs code"),
    ("type hello", ""),
    ("what's the weather in london", ""),
])
def test_the_app_a_request_names(request_text, name):
    assert tools.named_app(request_text) == name


def test_shortcuts_are_compared_whatever_the_spelling():
    assert tools._keys_combo("Shift + Ctrl+N") == "ctrl+shift+n"
    assert tools._keys_combo("control+n") == "ctrl+n"
    assert tools._keys_combo("ALT+TAB") == "alt+tab"


def test_which_windows_belong_to_notepad():
    assert tools.window_is_app(NOTEPAD, "notepad")
    assert tools.window_is_app((9, "*notes.txt - Notepad", "?"), "notepad")       # process unknown: title says it
    assert not tools.window_is_app(VSCODE, "notepad")
    assert not tools.window_is_app((9, "notepad.py - Laptop assistant - Visual Studio Code", "Code.exe"), "notepad")
    assert tools.window_is_app(VSCODE, "vs code")
    assert tools.window_is_app((9, "Calculator", "ApplicationFrameHost.exe"), "calculator")


@pytest.mark.parametrize("keys", ["alt+tab", "ctrl+shift+n", "ctrl+n"])
def test_new_window_keys_in_another_app_are_refused_when_the_app_is_closed(desk, keys):
    out = tools.app_guard("press_keys", {"keys": keys}, "notepad")
    assert out.startswith("Not done") and "open_app(name='notepad')" in out and "Visual Studio Code" in out
    assert app._failed(out)


def test_typing_into_another_app_is_refused(desk):
    out = tools.app_guard("type_text", {"text": "hello judge", "into_window": "Untitled-1 - Visual Studio Code"}, "notepad")
    assert out.startswith("Not done") and "open_app(name='notepad')" in out
    out = tools.app_guard("type_text", {"text": "hello judge"}, "notepad")       # no window named: the one in front
    assert out.startswith("Not done") and "open_app" in out


def test_notepad_open_but_behind_says_how_to_reach_it(desk):
    desk["windows"] = [VSCODE, NOTEPAD]
    out = tools.app_guard("type_text", {"text": "hi", "into_window": "Visual Studio Code"}, "notepad")
    assert "into_window='notepad'" in out and "open_app" not in out
    assert tools.app_guard("press_keys", {"keys": "ctrl+n"}, "notepad").startswith("Not done")


def test_the_right_app_is_let_through(desk):
    desk["windows"] = [NOTEPAD, VSCODE]
    assert tools.app_guard("type_text", {"text": "hi", "into_window": "notepad"}, "notepad") is None
    assert tools.app_guard("type_text", {"text": "hi"}, "notepad") is None
    assert tools.app_guard("press_keys", {"keys": "ctrl+n"}, "notepad") is None      # a new tab inside Notepad
    assert tools.app_guard("press_keys", {"keys": "ctrl+s"}, "notepad") is None      # ordinary keys aren't touched
    assert tools.app_guard("press_keys", {"keys": "alt+tab"}, "notepad").startswith("Not done")   # leaves Notepad


def test_no_app_named_changes_nothing(desk):
    assert tools.app_guard("press_keys", {"keys": "alt+tab"}, "") is None
    assert tools.app_guard("type_text", {"text": "hi", "into_window": "Visual Studio Code"}, "") is None


def test_waits_a_moment_for_a_window_that_open_app_just_started(desk, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(tools.time, "time", lambda: clock["t"])

    def tick(s):
        clock["t"] += s
        if clock["t"] >= 1.0:
            desk["windows"] = [NOTEPAD, VSCODE]
    monkeypatch.setattr(tools.time, "sleep", tick)
    assert tools._app_window("notepad", wait=3.0) == NOTEPAD


def test_the_whole_request_opens_notepad_first_and_types_nowhere_else(brain, desk, monkeypatch):
    """Replays the 2026-11-20 run: the brain's wrong steps are refused, so nothing reaches VS Code."""
    trusted()
    fake, ran = brain([
        ("", [("press_keys", {"keys": "alt+tab"})]),
        ("", [("press_keys", {"keys": "ctrl+shift+n"})]),
        ("", [("type_text", {"text": "hello judge", "into_window": "Untitled-1 - Visual Studio Code"})]),
        ("", [("open_app", {"name": "notepad"})]),
        ("", [("type_text", {"text": "hello judge", "into_window": "notepad"})]),
        ("Typed it in Notepad.", []),
    ])

    def run_tool(name, args):
        ran.append((name, args))
        if name == "open_app":
            desk["windows"] = [NOTEPAD, VSCODE, CLAUDE]
        return "ok"
    monkeypatch.setattr(app.tools, "run_tool", run_tool)
    out, status = app.answer("type hello judge in notepad")
    assert status == 200
    assert ran == [("open_app", {"name": "notepad"}), ("type_text", {"text": "hello judge", "into_window": "notepad"})]
    first = fake.seen[0][-1]["content"]
    assert "notepad is not open: call open_app(name='notepad') first" in first     # she is told before she starts
    refusals = [m["content"] for m in fake.seen[-1] if m.get("role") == "tool" and "Not done" in str(m.get("content"))]
    assert len(refusals) == 3


def test_a_request_without_an_app_types_as_before(brain, desk):
    trusted()
    fake, ran = brain([("", [("type_text", {"text": "hello"})]), ("Done.", [])])
    app.answer("type hello")
    assert ran == [("type_text", {"text": "hello"})]
