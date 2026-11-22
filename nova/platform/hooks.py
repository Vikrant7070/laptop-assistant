"""The "watch me" recorder's view of the real mouse and keyboard: Windows low-level hooks (roadmap R12 step 5b).

Moved unchanged from nova/actions/recorder.py. Two hooks (WH_MOUSE_LL, WH_KEYBOARD_LL) on a thread of their own,
with a message loop; nothing is injected into other programs. Events that software made (Nova's own clicks and
keys, flagged as injected) are left out, so only what the user did comes through.

    hooks = InputHooks(on_mouse=lambda wp, x, y: ..., on_key=lambda wp, vk: ...)
    runtime.spawn("nova-recorder-hooks", hooks.run, args=(ready,))   # ready = {"ev": Event(), "ok": False}
    ...
    hooks.stop()

The callbacks run inside Windows' hook call: keep them tiny (put the event on a queue) and never raise.
This module uses its own copies of user32/kernel32, so the argtypes set here never change how other calls behave.
"""
import ctypes
from ctypes import wintypes

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_QUIT = 0x12
LLKHF_INJECTED, LLKHF_LOWER_IL_INJECTED, LLMHF_INJECTED, LLMHF_LOWER_IL_INJECTED = 0x10, 0x02, 0x01, 0x02
GA_ROOT = 2


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_u = _k = None


def _dlls():
    """Our own copies of user32/kernel32, so the argtypes set here never change how the tools package's calls behave."""
    global _u, _k
    if _u is None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        u.SetWindowsHookExW.restype = wintypes.HHOOK
        u.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        u.CallNextHookEx.restype = LRESULT
        u.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        u.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.WindowFromPoint.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        u.GetForegroundWindow.restype = wintypes.HWND
        k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        k.GetModuleHandleW.restype = wintypes.HMODULE
        _u, _k = u, k
    return _u, _k


def window_at(x, y):
    """The top-level window under a screen point (what a click there lands in)."""
    u, _ = _dlls()
    return u.GetAncestor(u.WindowFromPoint(wintypes.POINT(x, y)), GA_ROOT)


def foreground():
    """The front window, with the full 64-bit handle type."""
    u, _ = _dlls()
    return u.GetForegroundWindow()


class InputHooks:
    def __init__(self, on_mouse, on_key):
        self.on_mouse, self.on_key = on_mouse, on_key      # on_mouse(wp, x, y), on_key(wp, vk)
        self.thread_id = 0

    # ---- the hooks: as little as possible, never raise, always pass the event on
    def _mouse(self, n, wp, lp):
        try:
            if n >= 0:
                m = ctypes.cast(lp, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not (m.flags & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED)):
                    self.on_mouse(wp, m.pt.x, m.pt.y)
        except Exception:
            pass  # intentional: hook callbacks must never raise into Windows
        return _u.CallNextHookEx(None, n, wp, lp)

    def _key(self, n, wp, lp):
        try:
            if n >= 0:
                k = ctypes.cast(lp, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not (k.flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED)):
                    self.on_key(wp, k.vkCode)
        except Exception:
            pass  # intentional: hook callbacks must never raise into Windows
        return _u.CallNextHookEx(None, n, wp, lp)

    def run(self, ready):
        """Install both hooks and pump messages until stop(). ready["ok"] says whether Windows allowed them."""
        u, k = _dlls()
        self.thread_id = k.GetCurrentThreadId()
        mod = k.GetModuleHandleW(None)
        self._mproc, self._kproc = HOOKPROC(self._mouse), HOOKPROC(self._key)   # kept alive while hooked
        hm = u.SetWindowsHookExW(WH_MOUSE_LL, self._mproc, mod, 0)
        hk = u.SetWindowsHookExW(WH_KEYBOARD_LL, self._kproc, mod, 0)
        ready["ok"] = bool(hm and hk)
        ready["ev"].set()
        msg = wintypes.MSG()
        try:
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            if hm:
                u.UnhookWindowsHookEx(hm)
            if hk:
                u.UnhookWindowsHookEx(hk)

    def stop(self):
        """End the message loop (the hooks come off as run() returns)."""
        if self.thread_id:
            u, _ = _dlls()
            u.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        self.thread_id = 0
