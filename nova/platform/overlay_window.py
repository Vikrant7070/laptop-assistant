"""The hands-off glow's window (nova/overlay.py draws the picture; this puts it on screen).

Built like 3D Nova's window (avatar_window.py, which works on this laptop): a Tk window made layered, topmost, a tool
window that is never focused, and click-through; the picture goes on with UpdateLayeredWindow. Plus
SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE): screenshots - her screen looks - don't see it
(NOVA_OVERLAY_CAPTURE=1 turns that off, only to take a picture of the overlay itself).

Found live (2026-11-23), so not done:
- withdraw / deiconify: once shown again, the window paints as a plain grey Tk window over the whole screen;
- a pulse that only changes the window's alpha with no picture (UpdateLayeredWindow, hdcSrc NULL): the glow vanishes
  (so the pulse passes the same picture again, with a new constant alpha);
- a thread blocked reading the stdin pipe froze Tk's main loop and the glow vanished (see _take_stdin).
So the window stays mapped the whole time: "hidden" is a fully see-through picture, "shown" is the glow. One window
spans every screen (the virtual desktop); each screen's edges glow. The window has no title, so it isn't in Nova's
window list. It runs in its own process (python -m nova.overlay).
"""
import ctypes
import json
import os
import queue

from nova.platform import avatar_window as aw

user32 = aw.user32
user32.SetWindowDisplayAffinity.argtypes = [aw.VP, ctypes.c_uint32]
WDA_EXCLUDEFROMCAPTURE = 0x11
TICK_MS = 100


def _take_stdin():
    """The command pipe, moved off the process's standard input (which becomes NUL) before Tk starts.

    Found live (2026-11-23): while a thread sat blocked reading the stdin pipe, Tk's main loop froze too (its ticks
    stopped), and the glow - drawn fine a moment earlier - was gone from the screen. Tcl looks at the standard handles;
    with the pipe on its own descriptor and NUL as stdin, the loop keeps running and the glow stays."""
    fd = os.dup(0)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)                     # for fds 0-2 this also changes the process's STD_INPUT_HANDLE
    os.close(null)
    return os.fdopen(fd, "r", encoding="utf-8")


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("rcMonitor", ctypes.c_long * 4), ("rcWork", ctypes.c_long * 4),
                ("dwFlags", ctypes.c_uint32)]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, aw.VP, aw.VP, ctypes.c_void_p, ctypes.c_void_p)
user32.EnumDisplayMonitors.argtypes = [aw.VP, aw.VP, _MONITORENUMPROC, ctypes.c_void_p]
user32.GetMonitorInfoW.argtypes = [aw.VP, ctypes.POINTER(_MONITORINFO)]


def monitors():
    """Every screen as (x, y, w, h) in desktop pixels, the primary first."""
    found = []

    def one(hmon, _hdc, _rect, _data):
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            left, top, right, bottom = info.rcMonitor
            found.append((not info.dwFlags & 1, (left, top, right - left, bottom - top)))   # 1 = MONITORINFOF_PRIMARY
        return 1

    user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(one), None)
    return [rect for _, rect in sorted(found)]


def layout(screens):
    """The window over all `screens`: (x, y, w, h) of the desktop they span, and each screen inside that window."""
    x0, y0 = min(s[0] for s in screens), min(s[1] for s in screens)
    x1, y1 = max(s[0] + s[2] for s in screens), max(s[1] + s[3] for s in screens)
    return (x0, y0, x1 - x0, y1 - y0), [(x - x0, y - y0, w, h) for x, y, w, h in screens]


def run(render, to_bytes, pulse, log):
    """Read JSON commands from stdin ({"do": "show", "text"} / {"do": "hide"}) until it closes."""
    import time
    pipe = _take_stdin()
    import tkinter as tk
    from nova import runtime
    user32.SetProcessDPIAware()
    root = tk.Tk()
    root.overrideredirect(True)
    w, h = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+0+0")
    root.title("")
    root.attributes("-topmost", True)    # WS_EX_TOPMOST can't be set as a style bit; started in the background it sat
    root.update_idletasks()              # behind every window (found live 2026-11-23)
    hwnd = aw.top_level_of(root.winfo_id())
    aw.set_clickthrough(hwnd, aw.make_layered(hwnd), True)
    if not os.environ.get("NOVA_OVERLAY_CAPTURE"):
        user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    root.update()                        # map it first, then draw (drawing before mapping showed nothing, live)
    dibs = {}
    state = {"rect": (0, 0, w, h), "since": None}          # since: when the glow went up (None: hidden)
    aw.blit(hwnd, dibs, bytes(w * h * 4), w, h, 0, 0)      # mapped, but nothing to see
    commands = queue.Queue()

    def read_stdin():
        for line in pipe:
            try:
                commands.put(json.loads(line))
            except ValueError:
                continue
        commands.put({"do": "quit"})                       # Nova (or the exam) closed the pipe: go away

    def tick():
        try:
            while not commands.empty():
                cmd = commands.get_nowait()
                if cmd.get("do") == "show":
                    rect, inside = layout(monitors() or [(0, 0, w, h)])     # screens may have changed since last time
                    state["rect"], state["since"] = rect, time.monotonic()
                    aw.blit(hwnd, dibs, to_bytes(render(rect[2], rect[3], str(cmd.get("text") or ""), inside)),
                            rect[2], rect[3], rect[0], rect[1], pulse(0))
                    log(f"shown over {len(inside)} screen(s) {rect} (exstyle "
                        f"{hex(user32.GetWindowLongPtrW(hwnd, aw.GWL_EXSTYLE))}): {str(cmd.get('text'))[:60]}")
                elif cmd.get("do") == "hide":
                    x, y, rw, rh = state["rect"]
                    state["since"] = None
                    aw.blit(hwnd, dibs, bytes(rw * rh * 4), rw, rh, x, y)
                elif cmd.get("do") == "quit":
                    root.destroy()
                    return
            if state["since"] is not None:                 # breathe: the same picture, the window's opacity changes
                x, y, rw, rh = state["rect"]
                aw.blit(hwnd, dibs, None, rw, rh, x, y, pulse(time.monotonic() - state["since"]))
        except Exception as e:
            log(f"error: {type(e).__name__}: {e}")
        root.after(TICK_MS, tick)

    runtime.spawn("overlay.stdin", read_stdin)
    root.after(TICK_MS, tick)
    root.mainloop()
