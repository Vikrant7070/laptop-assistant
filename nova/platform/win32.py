"""The real desktop: Windows, through ctypes. The only place Win32 calls should live (roadmap R12).

Moved here unchanged from nova/actions/tools/windows.py (_windows, _focus).
"""
import ctypes
from ctypes import wintypes

import psutil

from nova.platform import Window

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class Win32Desktop:
    def windows(self):
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    try:
                        proc = psutil.Process(pid.value).name()
                    except psutil.Error:
                        proc = "?"
                    if buf.value not in ("Program Manager", "Settings") or proc != "explorer.exe":
                        found.append(Window(hwnd, buf.value, proc))
            return True

        user32.EnumWindows(cb, 0)
        return found

    def foreground(self):
        return user32.GetForegroundWindow() or 0

    def focus(self, hwnd):
        """Bring a window to the front without pressing keys (borrow the foreground thread's input)."""
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        fg = user32.GetForegroundWindow()
        me = kernel32.GetCurrentThreadId()
        other = user32.GetWindowThreadProcessId(fg, None)
        user32.AttachThreadInput(me, other, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(me, other, False)
