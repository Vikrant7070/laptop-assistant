"""The real desktop: Windows, through ctypes. The only place Win32 calls should live (roadmap R12).

Moved here unchanged from nova/actions/tools/windows.py: window listing and focus (step 1); the clipboard save /
set / restore and the focused box's text (step 2); from checks.py, shared.py, presence.py, background.py and
tools/common.py: window and monitor rectangles, the work area and an auto-hiding taskbar, dark mode, idle time, the
lock screen and DPI awareness (step 3); from brain/cloud.py, app/session.py, speedup.py and tools/system.py: DPAPI,
the account's display name, memory commit, the wallpaper and the dark-mode switch (step 4). Keys go through the `keyboard` package, plain clipboard text
through `pyperclip`, both imported on use.
"""
import ctypes
import time
from ctypes import wintypes

import psutil

from nova.platform import Window

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class _APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uCallbackMessage", wintypes.UINT),
                ("uEdge", wintypes.UINT), ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint), ("flags", ctypes.c_uint), ("showCmd", ctypes.c_uint),
                ("ptMinPosition", ctypes.c_long * 2), ("ptMaxPosition", ctypes.c_long * 2),
                ("rcNormalPosition", ctypes.c_long * 4)]


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Windows couldn't encrypt/decrypt the saved key.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


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

    # -- keys and typing (the `keyboard` package)
    def press(self, keys):
        """A key or shortcut, e.g. "ctrl+v", "enter", "windows+h"."""
        import keyboard
        keyboard.send(keys)

    def write(self, text, delay=0):
        """Type text as key presses, one letter at a time (pasting is type_into_focus's job)."""
        import keyboard
        keyboard.write(text, delay=delay)

    # -- the clipboard
    def clipboard_text(self):
        import pyperclip
        return pyperclip.paste()

    def set_clipboard_text(self, text):
        """Plain copy: it shows in clipboard history, as a user's own copy would."""
        import pyperclip
        pyperclip.copy(text)

    def clipboard_save(self):
        return _clip_save()

    def clipboard_set_text(self, text):
        return _clip_set_text(text)

    def clipboard_restore(self, saved, our_seq):
        _clip_restore(saved, our_seq)

    def focused_text(self):
        return _focused_text()

    # -- the screen (step 3)
    def window_rect(self, hwnd):
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom

    def monitor_rect(self, hwnd):
        """The whole monitor the window is on (MONITOR_DEFAULTTONEAREST), or None."""
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        mon = user32.MonitorFromWindow(hwnd, 2)
        if not mon or not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
            return None
        m = info.rcMonitor
        return m.left, m.top, m.right, m.bottom

    def work_area(self):
        """Usable screen rectangle (left, top, right, bottom) excluding the taskbar."""
        r = wintypes.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
        return r.left, r.top, r.right, r.bottom

    def autohide_taskbar(self):
        """(edge, rect) of the taskbar when it is set to auto-hide (edge 0 left, 1 top, 2 right, 3 bottom), else None."""
        try:
            ab = _APPBARDATA()
            ab.cbSize = ctypes.sizeof(ab)
            if not ctypes.windll.shell32.SHAppBarMessage(4, ctypes.byref(ab)) & 1:     # ABM_GETSTATE: ABS_AUTOHIDE
                return None
            ctypes.windll.shell32.SHAppBarMessage(5, ctypes.byref(ab))                 # ABM_GETTASKBARPOS
            return ab.uEdge, (ab.rc.left, ab.rc.top, ab.rc.right, ab.rc.bottom)
        except Exception:
            return None

    def dark_mode(self):
        """True when apps use the dark theme, False for light, None if it can't be read."""
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        except OSError:
            return None

    def idle_seconds(self):
        """Seconds since the user last touched the keyboard or mouse."""
        info = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO), 0)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return max(0.0, (kernel32.GetTickCount() - info.dwTime) / 1000)

    def screen_locked(self):
        """True while the Windows lock screen is up (the input desktop isn't the user's 'Default' desktop)."""
        try:
            h = user32.OpenInputDesktop(0, False, 0x0001)          # DESKTOP_READOBJECTS
            if not h:
                return True
            try:
                buf = ctypes.create_unicode_buffer(64)
                need = ctypes.c_ulong()
                if not user32.GetUserObjectInformationW(h, 2, buf, ctypes.sizeof(buf), ctypes.byref(need)):   # UOI_NAME
                    return False
                return buf.value.lower() != "default"
            finally:
                user32.CloseDesktop(h)
        except Exception:
            return False

    def dpi_aware(self):
        """Real pixel coordinates for this process, so clicks land where UI Automation says. -> it worked?"""
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return True
        except Exception:
            return False

    # -- one window's state and actions (step 5)
    def is_window(self, hwnd):
        return bool(hwnd) and bool(user32.IsWindow(hwnd))

    def is_visible(self, hwnd):
        return bool(user32.IsWindowVisible(hwnd))

    def is_minimized(self, hwnd):
        return bool(user32.IsIconic(hwnd))

    def is_maximized(self, hwnd):
        return bool(user32.IsZoomed(hwnd))

    def is_hung(self, hwnd):
        return bool(user32.IsHungAppWindow(hwnd))

    def show(self, hwnd, cmd):
        """ShowWindow: SW_MAXIMIZE 3, SW_SHOWNOACTIVATE 4, SW_MINIMIZE 6, SW_SHOWMINNOACTIVE 7, SW_RESTORE 9."""
        user32.ShowWindow(hwnd, cmd)

    def close(self, hwnd):
        """WM_CLOSE, like clicking its X: the app may still ask to save."""
        user32.PostMessageW(hwnd, 0x0010, 0, 0)

    def window_pid(self, hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def class_name(self, hwnd):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def title(self, hwnd):
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def dpi(self, hwnd):
        return user32.GetDpiForWindow(hwnd) or 96

    def frame_rect(self, hwnd):
        """The window's visible rectangle (without the invisible resize borders)."""
        r = wintypes.RECT()
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
            user32.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def move(self, hwnd, x, y, w, h, flags=0):
        """SetWindowPos without a z-order change target (flags: SWP_NOZORDER 0x4, SWP_NOACTIVATE 0x10, SWP_SHOWWINDOW 0x40)."""
        user32.SetWindowPos(hwnd, 0, x, y, w, h, flags)

    def set_placement(self, hwnd, rect, show_cmd):
        """The window's normal (restored) rectangle and how it is shown, in one SetWindowPlacement."""
        wp = _WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(wp)
        user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
        wp.showCmd = show_cmd
        wp.rcNormalPosition[:] = list(rect)
        user32.SetWindowPlacement(hwnd, ctypes.byref(wp))

    def virtual_screen(self):
        """(left, width) of the area all monitors cover together."""
        return user32.GetSystemMetrics(76), user32.GetSystemMetrics(78)    # SM_XVIRTUALSCREEN, SM_CXVIRTUALSCREEN

    def top_level(self):
        """Every visible top-level window, titled or not: [(hwnd, class, title, pid, process name)]."""
        out = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                pid = self.window_pid(hwnd)
                try:
                    proc = psutil.Process(pid).name()
                except psutil.Error:
                    proc = "?"
                out.append((hwnd, self.class_name(hwnd), self.title(hwnd), pid, proc))
            return True
        user32.EnumWindows(cb, 0)
        return out

    def children(self, hwnd):
        """A window's child windows: [(hwnd, class, title, visible)]."""
        out = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(child, _):
            out.append((child, self.class_name(child), self.title(child), bool(user32.IsWindowVisible(child))))
            return True
        user32.EnumChildWindows(hwnd, cb, 0)
        return out

    # -- the mouse
    def cursor_pos(self):
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def set_cursor(self, x, y):
        user32.SetCursorPos(int(x), int(y))

    def mouse(self, flags, data=0):
        """mouse_event: LEFTDOWN 0x2, LEFTUP 0x4, RIGHTDOWN 0x8, RIGHTUP 0x10, WHEEL 0x800 (data = +-120 a notch)."""
        user32.mouse_event(flags, 0, 0, data, 0)

    # -- the system (step 4)
    def protect(self, data):
        """DPAPI: bytes only this Windows account can decrypt. Raises OSError if Windows refuses."""
        return _dpapi(data, True)

    def unprotect(self, data):
        return _dpapi(data, False)

    def user_display_name(self):
        """The Windows account's display name ("Vikrant Kumar"), "" if unknown."""
        try:
            size = ctypes.c_ulong(256)
            buf = ctypes.create_unicode_buffer(256)
            if ctypes.windll.secur32.GetUserNameExW(3, buf, ctypes.byref(size)):
                return buf.value.strip()
        except Exception:
            pass        # intentional: the display name is a nicety; callers fall back to "boss"
        return ""

    KNOWN_FOLDERS = {"Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
                     "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
                     "Downloads": "374DE290-123F-4565-9164-39C4925E467B"}

    def known_folder(self, name):
        """The real path of a Windows known folder (SHGetKnownFolderPath), so a Desktop moved into OneDrive is found.
        None if Windows can't say; callers fall back to <home>\\<name>."""
        guid = self.KNOWN_FOLDERS.get(name)
        if not guid:
            return None
        import uuid
        path = ctypes.c_wchar_p()
        try:
            hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.create_string_buffer(uuid.UUID(guid).bytes_le, 16),
                                                            0, None, ctypes.byref(path))
            return path.value if hr == 0 and path.value else None
        except OSError:
            return None
        finally:
            ctypes.windll.ole32.CoTaskMemFree(path)

    def memory_commit(self):
        """(used, limit) in bytes: RAM + pagefile that programs have claimed, and the most they can claim; None if unknown."""
        m = _MemStatus()
        m.dwLength = ctypes.sizeof(m)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return None
        return m.ullTotalPageFile - m.ullAvailPageFile, m.ullTotalPageFile

    def wallpaper(self):
        """SPI_GETDESKWALLPAPER: the wallpaper's path ("" for none); None if it can't be read."""
        buf = ctypes.create_unicode_buffer(1024)
        return buf.value if user32.SystemParametersInfoW(0x73, len(buf), buf, 0) else None

    def set_wallpaper(self, path):
        """SPI_SETDESKWALLPAPER, saved and broadcast. -> did Windows accept it?"""
        return bool(user32.SystemParametersInfoW(20, 0, str(path), 3))

    def set_dark_mode(self, on):
        """Apps and Windows itself in the dark (or light) theme."""
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                            0, winreg.KEY_SET_VALUE) as k:
            for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 0 if on else 1)


# ------------------------------------------------------------------ the clipboard (moved unchanged from tools/windows.py)

_CF_UNICODETEXT = 13


_GMEM_MOVEABLE = 0x0002


# clipboard formats whose data is a GDI handle, not memory - they can't be copied byte by byte. (A bitmap
# also lives on the clipboard as CF_DIB, which is copied, so a copied picture still comes back.)
_CLIP_HANDLE_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}


_CLIP_SAVE_MAX = 64 * 1024 * 1024      # don't hold more than this much of the user's clipboard in memory


_clip_api = None


def _clip():
    """user32/kernel32 with the argument types set (handles are 64-bit pointers)."""
    global _clip_api
    if _clip_api is None:
        import ctypes
        from ctypes import wintypes as w
        u, k = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
        for f, args, res in (
                (u.OpenClipboard, [w.HWND], w.BOOL), (u.CloseClipboard, [], w.BOOL),
                (u.EmptyClipboard, [], w.BOOL), (u.EnumClipboardFormats, [w.UINT], w.UINT),
                (u.GetClipboardData, [w.UINT], w.HANDLE), (u.SetClipboardData, [w.UINT, w.HANDLE], w.HANDLE),
                (u.RegisterClipboardFormatW, [w.LPCWSTR], w.UINT), (u.GetClipboardSequenceNumber, [], w.DWORD),
                (k.GlobalAlloc, [w.UINT, ctypes.c_size_t], w.HGLOBAL), (k.GlobalFree, [w.HGLOBAL], w.HGLOBAL),
                (k.GlobalLock, [w.HGLOBAL], w.LPVOID), (k.GlobalUnlock, [w.HGLOBAL], w.BOOL),
                (k.GlobalSize, [w.HGLOBAL], ctypes.c_size_t)):
            f.argtypes, f.restype = args, res
        _clip_api = (ctypes, u, k)
    return _clip_api


def _clip_open():
    _, u, _ = _clip()
    for _ in range(20):                 # another app may be holding the clipboard for a moment
        if u.OpenClipboard(None):
            return True
        time.sleep(0.025)
    return False


def _clip_put(items):
    """Replace the clipboard with [(format, bytes)]; a format may be a registered name. Call with it open."""
    ctypes, u, k = _clip()
    u.EmptyClipboard()
    for fmt, data in items:
        if isinstance(fmt, str):
            fmt = u.RegisterClipboardFormatW(fmt)
        h = k.GlobalAlloc(_GMEM_MOVEABLE, max(len(data), 1))
        if not h:
            continue
        p = k.GlobalLock(h)
        if not p:
            k.GlobalFree(h)
            continue
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        if not u.SetClipboardData(fmt, h):
            k.GlobalFree(h)            # (on success the clipboard owns it)


_KEEP_OUT_OF_HISTORY = [("ExcludeClipboardContentFromMonitorProcessing", b"\0\0\0\0"),
                        ("CanIncludeInClipboardHistory", b"\0\0\0\0"), ("CanUploadToCloudClipboard", b"\0\0\0\0")]


def _clip_save():
    """A copy of everything on the clipboard: [(format, bytes)], [] when empty, None when it couldn't be read."""
    ctypes, u, k = _clip()
    if not _clip_open():
        return None
    try:
        items, total, fmt = [], 0, u.EnumClipboardFormats(0)
        while fmt:
            if fmt not in _CLIP_HANDLE_FORMATS and not 0x200 <= fmt <= 0x3FF:   # (private / GDI-object ranges)
                h = u.GetClipboardData(fmt)
                size = k.GlobalSize(h) if h else 0
                if size:
                    total += size
                    if total > _CLIP_SAVE_MAX:
                        return None
                    p = k.GlobalLock(h)
                    if p:
                        items.append((fmt, ctypes.string_at(p, size)))
                        k.GlobalUnlock(h)
            fmt = u.EnumClipboardFormats(fmt)
        return items
    finally:
        u.CloseClipboard()


def _clip_set_text(text):
    """Put text on the clipboard (kept out of clipboard history). Returns the clipboard's sequence number, or
    0 when the clipboard couldn't be opened."""
    _, u, _ = _clip()
    if not _clip_open():
        return 0
    try:
        _clip_put([(_CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))] + _KEEP_OUT_OF_HISTORY)
    finally:
        u.CloseClipboard()
    return u.GetClipboardSequenceNumber() or 1


def _clip_restore(saved, our_seq):
    """Put the user's clipboard back - unless something else was copied since we pasted (then that stays)."""
    _, u, _ = _clip()
    if saved is None or u.GetClipboardSequenceNumber() != our_seq or not _clip_open():
        return
    try:
        if saved:
            _clip_put(list(saved) + _KEEP_OUT_OF_HISTORY)   # (it is in Win+V history already - no second copy)
        else:
            u.EmptyClipboard()
    finally:
        u.CloseClipboard()


def _focused_text():
    """What the focused box holds (to see a paste land), or None when it can't be read."""
    try:
        import uiautomation as auto
        c = auto.GetFocusedControl()
        try:
            return c.GetValuePattern().Value
        except Exception:
            return c.GetTextPattern().DocumentRange.GetText(200000)
    except Exception:
        return None
