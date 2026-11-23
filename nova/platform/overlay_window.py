"""The hands-off glow's window (nova/overlay.py draws the picture; this puts it on screen).

Built like 3D Nova's window (avatar_window.py, which works on this laptop): a Tk window made layered, topmost, a tool
window that is never focused, and click-through; the picture goes on with UpdateLayeredWindow. Two additions:
- SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE): screenshots - her screen looks - don't see it
  (NOVA_OVERLAY_CAPTURE=1 turns that off, only to take a picture of the overlay itself);
- a slow pulse that only changes the window's alpha (UpdateLayeredWindow with no new picture), so it costs nothing.
The window has no title, so it isn't in Nova's window list. It runs in its own process (python -m nova.overlay).
"""
import ctypes
import json
import math
import os
import queue
import sys
import time

from nova.platform import avatar_window as aw

user32 = aw.user32
user32.SetWindowDisplayAffinity.argtypes = [aw.VP, ctypes.c_uint32]
user32.ShowWindow.argtypes = [aw.VP, ctypes.c_int]
WDA_EXCLUDEFROMCAPTURE = 0x11
PULSE_MS = 80


def _alpha(hwnd, a):
    user32.UpdateLayeredWindow(hwnd, None, None, None, None, None, 0, ctypes.byref(aw.BLENDFUNCTION(0, 0, a, 1)), 2)


def run(render, to_bytes, log):
    """Read JSON commands from stdin ({"do": "show", "text"} / {"do": "hide"}) until it closes."""
    import tkinter as tk
    from nova import runtime
    user32.SetProcessDPIAware()
    root = tk.Tk()
    root.overrideredirect(True)
    w, h = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+0+0")
    root.title("")
    root.update_idletasks()
    hwnd = aw.top_level_of(root.winfo_id())
    style = aw.make_layered(hwnd)
    aw.set_clickthrough(hwnd, style, True)
    if not os.environ.get("NOVA_OVERLAY_CAPTURE"):
        user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    root.withdraw()
    commands, dibs, state = queue.Queue(), {}, {"on": False, "t0": time.time()}

    def read_stdin():
        for line in sys.stdin:
            try:
                commands.put(json.loads(line))
            except ValueError:
                continue
        commands.put({"do": "quit"})                 # Nova (or the exam) closed the pipe: go away

    def tick():
        try:
            while not commands.empty():
                cmd = commands.get_nowait()
                if cmd.get("do") == "show":
                    root.deiconify()
                    aw.blit(hwnd, dibs, to_bytes(render(w, h, str(cmd.get("text") or ""))), w, h, 0, 0)
                    state.update(on=True, t0=time.time())
                    log(f"shown on a {w}x{h} screen: {str(cmd.get('text'))[:60]}")
                elif cmd.get("do") == "hide":
                    state["on"] = False
                    root.withdraw()
                elif cmd.get("do") == "quit":
                    root.destroy()
                    return
            if state["on"]:
                _alpha(hwnd, int(185 + 70 * (0.5 + 0.5 * math.sin((time.time() - state["t0"]) * 2.2))))
        except Exception as e:
            log(f"error: {type(e).__name__}: {e}")
        root.after(PULSE_MS, tick)

    runtime.spawn("overlay.stdin", read_stdin)
    root.after(PULSE_MS, tick)
    root.mainloop()
