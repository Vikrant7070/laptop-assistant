"""The real desktop: Windows, through ctypes. The only place Win32 calls should live (roadmap R12).

Moved here unchanged from nova/actions/tools/windows.py: window listing and focus (step 1); the clipboard save /
set / restore and the focused box's text (step 2). Keys go through the `keyboard` package, plain clipboard text
through `pyperclip`, both imported on use.
"""
import ctypes
import time
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
