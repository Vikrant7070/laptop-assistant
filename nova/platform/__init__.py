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

    def press(self, keys: str) -> None:
        """A key or shortcut in whatever has the focus: "ctrl+v", "enter", "windows+h"."""

    def write(self, text: str, delay: float = 0) -> None:
        """Type text as key presses, one letter at a time."""

    def clipboard_text(self) -> str:
        """The clipboard's plain text ("" if none)."""

    def set_clipboard_text(self, text: str) -> None:
        """A plain copy, as the user's own would be (it shows in clipboard history)."""

    def clipboard_save(self) -> list | None:
        """Everything on the clipboard as [(format, bytes)]; [] when empty; None when it can't be read."""

    def clipboard_set_text(self, text: str) -> int:
        """Text on the clipboard kept out of clipboard history -> its sequence number (0: clipboard busy)."""

    def clipboard_restore(self, saved: list | None, our_seq: int) -> None:
        """Put `saved` back, unless something else was copied after our_seq."""

    def focused_text(self) -> str | None:
        """What the focused box holds, None if it can't be read."""

    def window_rect(self, hwnd: int) -> tuple:
        """(left, top, right, bottom) of the window."""

    def monitor_rect(self, hwnd: int) -> tuple | None:
        """(left, top, right, bottom) of the monitor the window is on, None if unknown."""

    def work_area(self) -> tuple:
        """The usable screen (left, top, right, bottom), without the taskbar."""

    def autohide_taskbar(self) -> tuple | None:
        """(edge, rect) of an auto-hiding taskbar, None if it doesn't auto-hide."""

    def dark_mode(self) -> bool | None:
        """Apps use the dark theme? None if it can't be read."""

    def idle_seconds(self) -> float:
        """Seconds since the user last touched the keyboard or mouse."""

    def screen_locked(self) -> bool:
        """Is the lock screen up?"""

    def dpi_aware(self) -> bool:
        """Make this process work in real pixels (so clicks land where UI Automation says)."""

    def protect(self, data: bytes) -> bytes:
        """Encrypt for this Windows account only (DPAPI). Raises OSError if it can't."""

    def unprotect(self, data: bytes) -> bytes:
        """Decrypt what protect() made. Raises OSError if it can't."""

    def user_display_name(self) -> str:
        """The account's display name ("" if unknown)."""

    def memory_commit(self) -> tuple | None:
        """(used, limit) bytes of RAM + pagefile programs have claimed; None if unknown."""

    def set_wallpaper(self, path) -> bool:
        """Set the desktop wallpaper. -> accepted?"""

    def set_dark_mode(self, on: bool) -> None:
        """Switch apps and Windows to the dark (or light) theme."""


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
