"""The hands-off glow: while Nova (or her task exam) uses the keyboard and mouse, the screen's edges glow and a pill at
the top says so - like Claude's computer use. The user's request, 2026-11-23: "make the screen corner glow and say
Nova is using the laptop", after the exam typed into their own Notepad tab while they were using the laptop.

    from nova import overlay
    overlay.show("Nova is using your laptop - say “stop” or press ■ Stop to take over")
    overlay.hide()

It is its own small process (`python -m nova.overlay`; the window itself is nova/platform/overlay_window.py, where
every Win32 call lives), so it works for the exam too (which runs while Nova is quit) and a stuck overlay can't hang
Nova. The controller talks to it over stdin, one JSON line per command.

The window never gets in the way:
- click-through (WS_EX_TRANSPARENT) and never activated (WS_EX_NOACTIVATE): it never becomes "the window in front",
  so it can't take her typing or confuse the checks that read the front window;
- no title, so it isn't in the window list (platform.windows skips untitled windows);
- left out of screenshots (SetWindowDisplayAffinity WDA_EXCLUDEFROMCAPTURE), so her screen looks don't see it;
- always on top, drawn with per-pixel alpha (UpdateLayeredWindow), breathing gently (pulse());
- one window over every screen: each screen's edges glow, the pill sits on the primary one.
"""
import json
import os
import subprocess
import sys
import threading

GLOW = 30                                    # px of glow along each edge
COLORS = ((124, 92, 255), (34, 211, 238))    # Nova's purple -> cyan, left to right
PULSE_SECS, PULSE_LOW = 2.4, 150             # one breath every 2.4 s, down to ~60 % opacity
TASK_TEXT = "Nova is using your laptop - say “stop” or press ■ Stop to take over"


# ------------------------------------------------------------------ drawing (pure: tested)

def render(width, height, text, screens=None):
    """The overlay image: a glowing border fading inward around each screen, and a pill with `text` at the top of the
    first one (the primary). `screens`: (x, y, w, h) rectangles inside the image; default, the whole image.
    -> PIL RGBA image."""
    from PIL import Image, ImageDraw, ImageFont
    screens = list(screens or [(0, 0, width, height)])
    grad = Image.new("RGB", (2, 1))
    grad.putpixel((0, 0), COLORS[0])
    grad.putpixel((1, 0), COLORS[1])
    grad = grad.resize((width, height), Image.BILINEAR)
    mask = Image.new("L", (width, height), 0)
    d = ImageDraw.Draw(mask)
    for sx, sy, sw, sh in screens:
        for i in range(GLOW):                # strongest at the edge, gone by GLOW px in
            a = int(200 * (1 - i / GLOW) ** 2)
            d.rectangle([sx + i, sy + i, sx + sw - 1 - i, sy + sh - 1 - i], outline=a)
    img = grad.convert("RGBA")
    img.putalpha(mask)
    if text:
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeui.ttf"), 20)
        except OSError:
            font = ImageFont.load_default()
        box = draw.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        pw, ph = tw + 66, th + 24
        px, py, pscreen_w, _ = screens[0]
        x0, y0 = px + (pscreen_w - pw) // 2, py + GLOW + 6
        draw.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], radius=ph // 2, fill=(21, 24, 41, 235),
                               outline=COLORS[0] + (255,), width=2)
        draw.ellipse([x0 + 16, y0 + ph // 2 - 6, x0 + 28, y0 + ph // 2 + 6], fill=COLORS[1] + (255,))
        draw.text((x0 + 38, y0 + 12 - box[1]), text, font=font, fill=(233, 235, 255, 255))
    return img


def pulse(t):
    """The glow's breathing: the whole window's opacity (0-255) at `t` seconds - from PULSE_LOW up to full and back
    every PULSE_SECS. Gentle on purpose: the pill dims with it, and must stay readable."""
    import math
    return int(PULSE_LOW + (255 - PULSE_LOW) * (0.5 + 0.5 * math.cos(2 * math.pi * t / PULSE_SECS)))


def premultiplied_bgra(img):
    """RGBA image -> the bytes UpdateLayeredWindow wants (BGRA, colours multiplied by alpha)."""
    import numpy as np
    a = np.asarray(img, dtype=np.uint16)
    rgb = a[..., :3] * a[..., 3:4] // 255
    out = np.empty(a.shape, dtype=np.uint8)
    out[..., 0], out[..., 1], out[..., 2], out[..., 3] = rgb[..., 2], rgb[..., 1], rgb[..., 0], a[..., 3]
    return out.tobytes()


# ------------------------------------------------------------------ the controller (Nova and the exam use this)

class Overlay:
    """Starts the overlay process when first shown; show(text) / hide() / close(). Never raises."""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.shown = ""

    def _send(self, cmd):
        if not can_show():
            return False
        with self.lock:
            try:
                if self.proc is None or self.proc.poll() is not None:
                    if cmd.get("do") != "show":
                        return False
                    exe = sys.executable
                    if exe.lower().endswith("python.exe") and os.path.exists(exe[:-10] + "pythonw.exe"):
                        exe = exe[:-10] + "pythonw.exe"      # no console window
                    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    self.proc = subprocess.Popen([exe, "-m", "nova.overlay"], stdin=subprocess.PIPE, cwd=root,
                                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.proc.stdin.write((json.dumps(cmd) + "\n").encode("utf-8"))
                self.proc.stdin.flush()
                return True
            except Exception:
                self.proc = None
                return False

    def show(self, text=TASK_TEXT):
        if text != self.shown and self._send({"do": "show", "text": text}):
            self.shown = text

    def hide(self):
        if self.shown and self._send({"do": "hide"}):
            self.shown = ""

    def close(self):
        with self.lock:
            p, self.proc, self.shown = self.proc, None, ""
        if p is not None and p.poll() is None:
            try:
                p.stdin.close()
                p.wait(timeout=3)
            except Exception:
                p.kill()


def can_show():
    """Only on a real Windows desktop, never in the tests (they run on a FakeDesktop)."""
    return sys.platform == "win32" and "PYTEST_CURRENT_TEST" not in os.environ and not os.environ.get("NOVA_NO_OVERLAY")


_overlay = Overlay()
show, hide, close = _overlay.show, _overlay.hide, _overlay.close


# ------------------------------------------------------------------ the overlay process (python -m nova.overlay)

def _log(text):
    """Only for finding a broken overlay: nova_overlay.log in %TEMP%."""
    import time
    try:
        with open(os.path.join(os.environ.get("TEMP", "."), "nova_overlay.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + text + "\n")
    except OSError:
        pass  # intentional: the log is only a debugging aid


def main():                                  # pragma: no cover - a real window on a real screen
    from nova.platform import overlay_window
    overlay_window.run(render, premultiplied_bgra, pulse, _log)


if __name__ == "__main__":
    main()
