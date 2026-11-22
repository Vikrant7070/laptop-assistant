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
    "nova/actions/recorder.py",           # its own hooks thread
    "nova/avatar/avatar.py",
    "nova/avatar/win32.py",               # the avatar's drawing calls (last step)
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


def test_full_screen_check_compares_the_window_with_its_monitor(desktop):
    desktop.set_windows([CHROME])
    desktop.rects = {5: (0, 0, 1920, 1080)}
    assert checks.fullscreen("", {}) == (True, "fills the screen")
    desktop.rects = {5: (100, 100, 900, 700)}
    assert checks.fullscreen("", {}) == (False, "doesn't fill the screen")
    desktop.set_windows([])
    assert checks.fullscreen("", {}) == (False, "no window in front")


def test_dark_mode_check(desktop):
    desktop.dark = True
    assert checks.dark_mode("on", {}) == (True, "dark") and checks.dark_mode("off", {}) == (False, "dark")
    desktop.dark = None
    assert checks.dark_mode("on", {}) == (False, "couldn't read the colour mode")


def test_screen_readings_come_from_the_platform(desktop):
    from nova import shared
    from nova.actions import presence
    from nova.core import app
    desktop.idle, desktop.locked, desktop.work = 321.0, True, (0, 0, 1366, 728)
    desktop.taskbar = (3, (0, 708, 1366, 768))
    assert app.background.user_idle_secs() == 321.0 and presence.screen_locked() is True
    assert shared.work_area() == (0, 0, 1366, 728) and shared.autohide_taskbar() == (3, (0, 708, 1366, 768))


def test_saved_keys_are_encrypted_through_the_platform(desktop):
    from nova import store
    from nova.brain import cloud
    cloud.set_key("anthropic", "sk-test-123")
    stored = store.current().keys.read_text(encoding="utf-8")
    assert "sk-test-123" not in stored                       # never on disk in the clear
    assert cloud.get_key("anthropic") == "sk-test-123"
    with __import__("pytest").raises(cloud.CloudError):
        cloud._dpapi(b"not ours", False)                     # Windows refusing -> the same CloudError as before


def test_system_settings_go_through_the_platform(desktop, tmp_path):
    from nova.actions import speedup
    from nova.core import app
    assert tools.set_dark_mode(True) == "Dark mode on." and desktop.dark is True
    assert tools.set_dark_mode("off") == "Dark mode off." and desktop.logged("dark_mode") == [True, False]
    pic = tmp_path / "sky.png"
    pic.write_bytes(b"png")
    assert tools.set_wallpaper(str(pic)) == "Wallpaper set to sky.png." and desktop.logged("wallpaper") == [str(pic)]
    desktop.display_name = "Asha Verma"
    assert app.session._windows_display_name() == "Asha"
    desktop.display_name = ""
    assert app.session._windows_display_name() == ""
    assert speedup._commit() == desktop.memory


def test_window_actions_without_her_body(desktop, monkeypatch):
    from nova.avatar import avatar_client
    monkeypatch.setattr(avatar_client, "running", lambda: False)
    desktop.set_windows([NOTEPAD, CHROME])
    assert tools.window_action("notepad", "minimize") == "Minimized 'Untitled - Notepad'." and desktop.is_minimized(2)
    assert tools.window_action("chrome", "maximize") == "Maximized 'News - Google Chrome'." and desktop.is_maximized(5)
    assert tools.window_action("notepad", "close") == "Closed 'Untitled - Notepad'."
    assert desktop.logged("close") == [2] and [w.hwnd for w in desktop.windows()] == [5]


def test_research_windows_are_parked_past_the_screen_edge(desktop, monkeypatch):
    from nova.actions import coworker
    monkeypatch.setattr(coworker, "WATCH", False)
    desktop.alive.add(77)
    desktop.virtual = (0, 3000)                                        # two monitors side by side
    coworker.park(77)
    x = coworker._offscreen_x()
    assert x >= 3000 and desktop.logged("placement") == [(77, (x, 0, x + 1280, 900), 4)]
    assert desktop.logged("move")[-1] == (77, (x, 0, x + 1280, 900))
    coworker.close_window(77)
    assert desktop.logged("close") == [77]


def test_the_watcher_reads_dialogs_and_hangs_from_the_platform(desktop):
    from nova.actions import watcher
    desktop.set_windows([(9, "Word", "WINWORD.EXE")])
    desktop.classes[9] = "#32770"
    desktop.kids[9] = [(91, "Static", "Cannot open file.", True), (92, "Static", "hidden", False), (93, "Button", "OK", True)]
    assert watcher.top_windows() == [(9, "#32770", "Word", 1009, "WINWORD.EXE")]
    assert watcher.dialog_text(9) == "Cannot open file."
    desktop.hung.add(9)
    assert watcher.is_hung(9) and watcher.still_there({"kind": "hung", "hwnd": 9})


def test_scrolling_turns_the_wheel(desktop, monkeypatch):
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    tools.forms.scroll("down", 3)
    assert [m[1:3] for m in desktop.log if m[0] == "mouse"] == [(0x0800, -120)] * 3
