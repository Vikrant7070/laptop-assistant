"""The desktop Nova works on, behind one small interface (roadmap R12).

Win32 calls (ctypes.windll.user32 and friends) were spread over 19 files, and tests reached in and patched their
private helpers. Now code asks the current Desktop, and tests install a FakeDesktop scene instead:

    from nova import platform
    platform.current().windows()        # [Window(hwnd, title, proc), ...], topmost first
    platform.current().foreground()     # the front window's handle (0 if none)
    platform.current().focus(hwnd)      # bring it to the front

    platform.use(platform.FakeDesktop([(1, "Untitled - Notepad", "notepad.exe")]))     # tests

Like nova.store, there is one current object; `use()` swaps it. The real one is `win32.Win32Desktop`, created on
first use so importing this package never touches Win32.

The interface grows one area per PR (roadmap R12 step 4): windows and focus first; then keys, typing and the
clipboard; the screen; DPAPI and the registry; the avatar's drawing calls.
"""
from typing import NamedTuple, Protocol


class Window(NamedTuple):
    """One top-level window. A tuple, so `for hwnd, title, proc in windows()` keeps working."""
    hwnd: int
    title: str
    proc: str


class Desktop(Protocol):
    def windows(self) -> list[Window]:
        """Visible top-level windows with a title, topmost first (Program Manager and the Settings host left out)."""

    def foreground(self) -> int:
        """The front window's handle, 0 if there is none."""

    def focus(self, hwnd: int) -> None:
        """Bring the window to the front (restoring it if minimised), without pressing keys."""


from nova.platform.fake import FakeDesktop  # noqa: E402  (fake.py imports Window from here)

_current = None


def current():
    """The Desktop everything works through."""
    global _current
    if _current is None:
        from nova.platform.win32 import Win32Desktop
        _current = Win32Desktop()
    return _current


def use(desktop):
    """Install `desktop` as the current one and return it (tests: a FakeDesktop)."""
    global _current
    _current = desktop
    return desktop


__all__ = ["Desktop", "FakeDesktop", "Window", "current", "use"]
