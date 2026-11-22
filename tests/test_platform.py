"""R12: the desktop behind nova.platform - the fake tests run on, and the guard that keeps Win32 in one place."""
import ast
from pathlib import Path

from nova import platform
from nova.actions import checks, tools

ROOT = Path(__file__).resolve().parent.parent

NOTEPAD = (2, "Untitled - Notepad", "notepad.exe")
CHROME = (5, "News - Google Chrome", "chrome.exe")

# Files that still call Win32 themselves (ctypes.windll / WinDLL). Each R12 PR moves one area into nova/platform/
# and takes files off this list. Shrink it; never grow it.
WIN32_OUTSIDE_PLATFORM = {
    "nova/actions/checks.py",             # dark mode / full screen readings (screen step)
    "nova/actions/coworker.py",           # hiding, parking and claiming research windows
    "nova/actions/presence.py",
    "nova/actions/recorder.py",           # its own hooks thread
    "nova/actions/speedup.py",
    "nova/actions/tools/common.py",
    "nova/actions/tools/system.py",
    "nova/actions/tools/windows.py",      # moving, closing and minimising windows; keys and the clipboard
    "nova/actions/watcher.py",
    "nova/avatar/avatar.py",
    "nova/avatar/win32.py",               # the avatar's drawing calls (last step)
    "nova/brain/cloud.py",                # DPAPI
    "nova/core/app/background.py",
    "nova/core/app/session.py",
    "nova/core/desktop.py",
    "nova/shared.py",
}


def test_tests_run_on_a_fake_desktop():
    assert isinstance(platform.current(), platform.FakeDesktop)
    assert tools.windows._windows() == []


def test_the_fake_keeps_z_order_and_the_front(desktop):
    desktop.set_windows([CHROME, NOTEPAD])
    assert desktop.foreground() == 5 and [w.title for w in desktop.windows()] == [CHROME[1], NOTEPAD[1]]
    tools.windows._focus(2)
    assert desktop.foreground() == 2 and desktop.windows()[0] == NOTEPAD and desktop.log == [("focus", 2)]
    desktop.close(2)
    assert desktop.foreground() == 5
    desktop.add(9, "Calculator", "calc.exe", front=True)
    assert desktop.windows()[0].hwnd == 9 and desktop.foreground() == 9


def test_windows_are_tuples_callers_can_unpack(desktop):
    desktop.set_windows([NOTEPAD])
    hwnd, title, proc = tools.windows._windows()[0]
    assert (hwnd, title, proc) == NOTEPAD and tools.windows._find_window("notepad") == NOTEPAD


def test_checks_read_the_front_window_from_the_platform(desktop, monkeypatch):
    desktop.set_windows([CHROME, NOTEPAD], front=2)
    monkeypatch.setattr(tools.forms, "_title_of", lambda h: dict([CHROME[:2], NOTEPAD[:2]])[h])
    assert checks._window_for({})[:2] == (2, "Untitled - Notepad")
    assert checks.window_open("chrome", {})[0] and not checks.window_open("excel", {})[0]


def test_use_swaps_the_desktop():
    mine = platform.FakeDesktop([NOTEPAD])
    assert platform.use(mine) is mine and platform.current().windows() == [NOTEPAD]


def _win32_users():
    found = set()
    for path in (ROOT / "nova").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Attribute, ast.Name)) and getattr(node, "attr", getattr(node, "id", "")) in ("windll", "WinDLL"):
                found.add(path.relative_to(ROOT).as_posix())
    return found


def test_win32_calls_stay_in_the_platform_package():
    outside = {f for f in _win32_users() if not f.startswith("nova/platform/")}
    assert outside - WIN32_OUTSIDE_PLATFORM == set(), "new Win32 code goes in nova/platform/"
    assert WIN32_OUTSIDE_PLATFORM - outside == set(), "a file no longer calls Win32: take it off the list"
