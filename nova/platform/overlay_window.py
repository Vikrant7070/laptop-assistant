"""The hands-off glow's window (nova/overlay.py draws the picture; this puts it on screen).

Built like 3D Nova's window (avatar_window.py, which works on this laptop): a Tk window made layered, topmost, a tool
window that is never focused, and click-through; the picture goes on with UpdateLayeredWindow. Plus
SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE): screenshots - her screen looks - don't see it
(NOVA_OVERLAY_CAPTURE=1 turns that off, only to take a picture of the overlay itself).

Found live (2026-11-23), so not done:
- withdraw / deiconify: once shown again, the window paints as a plain grey Tk window over the whole screen;
- a pulse that only changes the window's alpha (UpdateLayeredWindow with no new picture): the glow vanishes;
- a thread blocked reading the stdin pipe froze Tk's main loop and the glow vanished (see _take_stdin).
So the window stays mapped the whole time: "hidden" is a fully see-through picture, "shown" is the glow. Steady, no
pulse. The window has no title, so it isn't in Nova's window list. It runs in its own process (python -m nova.overlay).
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


def run(render, to_bytes, log):
    """Read JSON commands from stdin ({"do": "show", "text"} / {"do": "hide"}) until it closes."""
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
    clear, dibs = bytes(w * h * 4), {}
    aw.blit(hwnd, dibs, clear, w, h, 0, 0)                 # mapped, but nothing to see
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
                    aw.blit(hwnd, dibs, to_bytes(render(w, h, str(cmd.get("text") or ""))), w, h, 0, 0)
                    log(f"shown on a {w}x{h} screen (exstyle {hex(user32.GetWindowLongPtrW(hwnd, aw.GWL_EXSTYLE))}): "
                        f"{str(cmd.get('text'))[:60]}")
                elif cmd.get("do") == "hide":
                    aw.blit(hwnd, dibs, clear, w, h, 0, 0)
                elif cmd.get("do") == "quit":
                    root.destroy()
                    return
        except Exception as e:
            log(f"error: {type(e).__name__}: {e}")
        root.after(TICK_MS, tick)

    runtime.spawn("overlay.stdin", read_stdin)
    root.after(TICK_MS, tick)
    root.mainloop()
