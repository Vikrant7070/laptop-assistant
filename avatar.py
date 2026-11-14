"""Desktop Nova: the 3D character that lives on your screen.

A see-through, always-on-top window (Win32 layered window with per-pixel alpha) that plays the
sprite frames rendered from static/nova3d.html. She wanders around, talks in speech bubbles, and
walks over to buttons to "press" them when the assistant clicks something.

Control API (used by tools.py / app.py):  POST http://127.0.0.1:5056/cmd  {"do": ..., ...}
  anim {name}            base animation: idle | talk | think | sleepy
  say {text, secs}       speech bubble
  walk_to {x, y}         walk so her feet are at (x, y)          (waits until she arrives)
  go_point {x, y}        walk next to (x, y) and raise her hand to it (waits until the "tap")
  finish_point           finish the tap, then a happy hop
  happy / wave           one-shot animations
  react {name, repeat}   any reaction once (or `repeat` times): laugh, dance, spin, clap, surprised, sad, confused,
                         nod, shake, facepalm, bow, love, yawn, look, happy, wave
  carry_to {x, y}        walk to (x, y) holding something up beside her (used to drag a window; waits until she arrives)
  clickthrough {on}      let the mouse pass through her
  show / hide
Run alone:  pythonw avatar.py
"""
import ctypes
import json
import math
import queue
import random
import textwrap
import threading
import time
import tkinter as tk
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixel coordinates
except Exception:
    pass

ROOT = Path(__file__).parent
FRAMES = ROOT / "static" / "nova3d"
PORT = 5056
SCALE = 0.8          # character size on screen
SPEED = 420          # walking speed, pixels per second
TICK_MS = 33
from shared import REACTIONS, host_allowed, work_area  # noqa: E402

user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", ctypes.c_ushort), ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


VP = ctypes.c_void_p
user32.GetDC.restype = VP
user32.GetDC.argtypes = [VP]
user32.ReleaseDC.argtypes = [VP, VP]
user32.GetParent.restype = VP
user32.GetParent.argtypes = [VP]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [VP, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [VP, ctypes.c_int, ctypes.c_ssize_t]
user32.UpdateLayeredWindow.argtypes = [VP, VP, ctypes.POINTER(POINT), ctypes.POINTER(SIZE), VP,
                                       ctypes.POINTER(POINT), ctypes.c_uint32, ctypes.POINTER(BLENDFUNCTION), ctypes.c_uint32]
gdi32.CreateCompatibleDC.restype = VP
gdi32.CreateCompatibleDC.argtypes = [VP]
gdi32.CreateDIBSection.restype = VP
gdi32.CreateDIBSection.argtypes = [VP, ctypes.POINTER(BITMAPINFO), ctypes.c_uint, ctypes.POINTER(VP), VP, ctypes.c_uint32]
gdi32.SelectObject.restype = VP
gdi32.SelectObject.argtypes = [VP, VP]
gdi32.DeleteDC.argtypes = [VP]
gdi32.DeleteObject.argtypes = [VP]

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0x80000, 0x20, 0x80, 0x8, 0x08000000
BLEND = BLENDFUNCTION(0, 0, 255, 1)


def premultiplied_bgra(img):
    r, g, b, a = img.split()
    return Image.merge("RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a), ImageChops.multiply(r, a), a)).tobytes()


class Sprites:
    def __init__(self):
        self.load()

    def load(self):
        meta = json.loads((FRAMES / "meta.json").read_text(encoding="utf-8"))
        self.fps = meta.get("fps", 12)
        w, h = meta["size"]
        self.w, self.h = round(w * SCALE), round(h * SCALE)
        self.feet = (meta["feet"][0] * SCALE, meta["feet"][1] * SCALE)
        self.anims = {k: v for k, v in meta["anims"].items()}
        self.images = {}
        for name, info in self.anims.items():
            self.images[name] = [Image.open(FRAMES / f"{name}_{i:02d}.png").convert("RGBA").resize((self.w, self.h), Image.LANCZOS)
                                 for i in range(info["frames"])]
        self.cache = {}

    def count(self, name):
        return len(self.images[name])

    def image(self, name, i, flip):
        img = self.images[name][i]
        return img.transpose(Image.FLIP_LEFT_RIGHT) if flip else img

    def bytes(self, name, i, flip):
        key = (name, i, flip)
        if key not in self.cache:
            self.cache[key] = premultiplied_bgra(self.image(name, i, flip))
        return self.cache[key]

    def hand(self, name, i, flip):
        hx, hy = self.anims[name]["hand"][i]
        hx, hy = hx * SCALE, hy * SCALE
        return (self.w - hx if flip else hx), hy


class Nova:
    def __init__(self):
        self.sp = Sprites()
        self.root = tk.Tk()
        self.root.title("Nova 3D")
        self.root.overrideredirect(True)
        self.root.geometry(f"{self.sp.w}x{self.sp.h}+0+0")
        self.root.update_idletasks()
        self.hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        self.base_style = user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE
        user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, self.base_style)
        self.root.attributes("-topmost", True)

        left, top, right, bottom = work_area()
        self.pos = [right - 220.0, float(bottom)]   # feet position on screen
        self.target = None
        self.facing_left = False
        self.base = "idle"
        self.oneshot = None          # dict(name, start, hold_at, done_event, after)
        self.bubble = None           # (text, until)
        self.visible = True
        self.next_wander = time.time() + random.uniform(8, 16)
        self.arrive_event = None
        self.carrying = False        # walking while holding a window: uses the "carry" animation, never turns around
        self.next_fidget = time.time() + random.uniform(20, 40)
        self.dibs = {}
        self.q = queue.Queue()
        self.drag = None
        try:
            self.font = ImageFont.truetype("segoeui.ttf", max(12, round(17 * SCALE)))
        except OSError:
            self.font = ImageFont.load_default()

        self.root.bind("<ButtonPress-1>", self.on_press)
        self.root.bind("<B1-Motion>", self.on_drag)
        self.root.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Button-3>", lambda e: self.react(random.choice(("laugh", "dance", "spin", "love", "clap", "wave"))))
        threading.Thread(target=self.serve, daemon=True).start()
        threading.Thread(target=self.watch_battery, daemon=True).start()
        self.start_oneshot("wave")
        self.say("Hi! I'm Nova. I'll be right here on your screen ✨", 5)
        self.tick()

    # ------------------------------------------------------------ behaviour
    def start_oneshot(self, name, hold_at=None, after=None, repeat=1):
        ev = threading.Event()
        self.oneshot = {"name": name, "start": time.time(), "hold_at": hold_at, "event": ev, "after": after, "held": False,
                        "repeat": max(1, int(repeat))}
        return ev

    def react(self, name, repeat=None):
        """Play a reaction. Dancing and laughing look best a few times over."""
        if name not in self.sp.images or name not in REACTIONS:
            return None
        if repeat is None:
            repeat = {"dance": 3, "laugh": 2, "clap": 2}.get(name, 1)
        self.target = None if not self.carrying else self.target      # stop strolling to do it
        return self.start_oneshot(name, repeat=min(int(repeat), 8))

    def fidget(self):
        """Small things she does by herself when nothing is happening."""
        hour = time.localtime().tm_hour
        sleepy_time = hour >= 23 or hour < 6
        r = random.random()
        if sleepy_time and r < .45:
            self.react("yawn")
        elif r < .75:
            self.react("look")
        elif r < .85:
            self.react("spin")
        elif r < .93:
            self.react("nod")
        else:
            self.react("yawn")

    def say(self, text, secs=6):
        text = " ".join(str(text).split())
        if len(text) > 170:
            text = text[:167] + "…"
        self.bubble = (text, time.time() + secs) if text else None

    def walk_to(self, x, y):
        left, top, right, bottom = work_area()
        x = min(max(x, left + self.sp.w * .25), right - self.sp.w * .25)
        y = min(max(y, top + self.sp.h * .9), bottom)
        self.target = (x, y)
        self.arrive_event = threading.Event()
        return self.arrive_event

    def point_spot(self, tx, ty):
        """Where her feet should be so her pointing hand ends up right next to (tx, ty)."""
        tap = self.sp.anims["point"]["tap"]
        left, _, right, _ = work_area()
        for flip in (False, True):
            hx, hy = self.sp.hand("point", tap, flip)
            gap = 14 if not flip else -14
            fx = tx + gap + (self.sp.feet[0] - hx)
            fy = ty + 6 + (self.sp.feet[1] - hy)
            if left + self.sp.w * .3 < fx < right - self.sp.w * .3:
                return fx, fy, flip
        return fx, fy, flip

    def wander(self):
        left, top, right, bottom = work_area()
        if random.random() < .7:   # stroll along the bottom of the screen
            return random.uniform(left + 150, right - 150), bottom
        return random.uniform(left + 150, right - 150), random.uniform(top + self.sp.h, bottom)

    def watch_battery(self):
        import psutil
        while True:
            try:
                b = psutil.sensors_battery()
                low = bool(b and b.percent <= 15 and not b.power_plugged)
                if low and self.base == "idle":
                    self.q.put(({"do": "anim", "name": "sleepy"}, None, None))
                elif not low and self.base == "sleepy":
                    self.q.put(({"do": "anim", "name": "idle"}, None, None))
            except Exception:
                pass
            time.sleep(60)

    # ------------------------------------------------------------ commands from the assistant
    def handle(self, cmd, done, result):
        do = cmd.get("do")
        if do == "anim" and cmd.get("name") in self.sp.images:
            self.base = cmd["name"]
        elif do == "say":
            self.say(cmd.get("text", ""), float(cmd.get("secs", 6)))
        elif do == "walk_to":
            ev = self.walk_to(float(cmd["x"]), float(cmd["y"]))
            threading.Thread(target=lambda: (ev.wait(15), done.set()), daemon=True).start()
            return
        elif do == "go_point":
            tx, ty = float(cmd["x"]), float(cmd["y"])
            fx, fy, flip = self.point_spot(tx, ty)
            ev = self.walk_to(fx, fy)

            def then():
                ev.wait(15)
                self.q.put(({"do": "_point", "flip": flip}, done, result))
            threading.Thread(target=then, daemon=True).start()
            return
        elif do == "_point":
            self.facing_left = cmd["flip"]
            ev = self.start_oneshot("point", hold_at=self.sp.anims["point"]["tap"])
            threading.Thread(target=lambda: (ev.wait(5), done.set()), daemon=True).start()
            return
        elif do == "finish_point":
            if self.oneshot and self.oneshot["name"] == "point":
                self.oneshot["hold_at"] = None
                self.oneshot["after"] = "happy"
            else:
                self.start_oneshot("happy")
        elif do in ("happy", "wave"):
            self.start_oneshot(do)
        elif do == "react":
            ev = self.react(cmd.get("name", ""), cmd.get("repeat"))
            result["played"] = bool(ev)
            if ev and cmd.get("wait"):
                threading.Thread(target=lambda: (ev.wait(20), done.set()), daemon=True).start()
                return
        elif do == "carry_to":
            self.oneshot = None
            self.carrying = True
            ev = self.walk_to(float(cmd["x"]), float(cmd["y"]))

            def arrived():
                ev.wait(20)
                self.carrying = False
                done.set()
            threading.Thread(target=arrived, daemon=True).start()
            return
        elif do == "clickthrough":
            style = self.base_style | (WS_EX_TRANSPARENT if cmd.get("on") else 0)
            user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, style)
        elif do == "show":
            self.visible = True
            self.root.deiconify()
        elif do == "hide":
            self.visible = False
            self.root.withdraw()
        elif do == "duck":      # step out of a screenshot for a moment
            self.root.withdraw()
        elif do == "unduck":
            if self.visible:
                self.root.deiconify()
        elif do == "reload":
            self.sp.load()
        result["pos"] = self.pos
        if done:
            done.set()

    def serve(self):
        nova = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _ours(self):
                """Only Nova (same laptop, our own Host name) may drive her - not a web page via DNS rebinding."""
                if host_allowed(self.headers.get("Host"), PORT) and not self.headers.get("Origin"):
                    return True
                self.send_response(403)
                self.end_headers()
                return False

            def do_POST(self):
                if not self._ours():
                    return
                cmd = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 2) or b"{}")
                done, result = threading.Event(), {}
                nova.q.put((cmd, done, result))
                ok = done.wait(float(cmd.get("timeout", 20)))
                body = json.dumps({"ok": ok, **result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._ours():
                    return
                b = nova.bubble
                body = json.dumps({"ok": True, "pos": nova.pos, "visible": nova.visible, "talking": nova.base == "talk",
                                   "bubble": b[0] if b and time.time() < b[1] else "",
                                   "reactions": [r for r in REACTIONS if r in nova.sp.images]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

        ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

    # ------------------------------------------------------------ mouse: drag her around, click to open chat
    def on_press(self, e):
        self.drag = (e.x_root, e.y_root, self.pos[0], self.pos[1], False)

    def on_drag(self, e):
        if not self.drag:
            return
        x0, y0, px, py, _ = self.drag
        if abs(e.x_root - x0) + abs(e.y_root - y0) > 4:
            self.drag = (x0, y0, px, py, True)
            self.target = None
            self.pos = [px + e.x_root - x0, py + e.y_root - y0]

    def on_release(self, e):
        if self.drag and not self.drag[4]:
            self.start_oneshot("wave")
            try:
                urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:5055/api/show_window", method="POST"), timeout=1)
            except Exception:
                pass
        self.drag = None
        self.next_wander = time.time() + random.uniform(20, 40)

    # ------------------------------------------------------------ main loop
    def tick(self):
        now = time.time()
        try:
            while True:
                self.handle(*self.q.get_nowait())
        except queue.Empty:
            pass

        moving = False
        if self.target and not self.drag:
            dx, dy = self.target[0] - self.pos[0], self.target[1] - self.pos[1]
            dist = math.hypot(dx, dy)
            step = SPEED * (1.6 if dist > 900 else 1) * TICK_MS / 1000
            if dist <= step:
                self.pos = list(self.target)
                self.target = None
                if self.arrive_event:
                    self.arrive_event.set()
            else:
                self.pos[0] += dx / dist * step
                self.pos[1] += dy / dist * step
                if abs(dx) > 2 and not self.carrying:
                    self.facing_left = dx < 0
                moving = True
        elif (not self.oneshot and self.base == "idle" and now > self.next_wander and not self.drag):
            self.walk_to(*self.wander())
            self.next_wander = now + random.uniform(15, 35)
        elif (not self.oneshot and self.base == "idle" and now > self.next_fidget and not self.drag and not self.target
              and not (self.bubble and now < self.bubble[1])):
            self.fidget()
            self.next_fidget = now + random.uniform(25, 60)

        if self.oneshot:
            o = self.oneshot
            n = self.sp.count(o["name"])
            idx = int((now - o["start"]) * self.sp.fps)
            if o["hold_at"] is not None and idx >= o["hold_at"]:
                idx = o["hold_at"]
                if not o["held"]:
                    o["held"] = True
                    o["event"].set()
                o["start"] = now - idx / self.sp.fps
            if idx >= n and o["repeat"] > 1:
                o["repeat"] -= 1
                o["start"] = now
                idx = 0
            if idx >= n:
                o["event"].set()
                after = o["after"]
                self.oneshot = None
                if after:
                    self.start_oneshot(after)
                name, idx = self.base, int(now * self.sp.fps) % self.sp.count(self.base)
            else:
                name = o["name"]
        elif moving:
            name = "carry" if self.carrying and "carry" in self.sp.images else "walk"
            idx = int(now * self.sp.fps * 1.3) % self.sp.count(name)
        else:
            name, idx = self.base, int(now * self.sp.fps) % self.sp.count(self.base)

        flip = self.facing_left if name in ("walk", "point", "carry") else False
        if self.visible:
            self.render(name, idx, flip)
        self.root.after(TICK_MS, self.tick)

    def render(self, name, idx, flip):
        sw, sh = self.sp.w, self.sp.h
        fx, fy = self.sp.feet
        if self.bubble and time.time() < self.bubble[1]:
            canvas, xoff, yoff = self.compose_bubble(self.sp.image(name, idx, flip))
            data, w, h = premultiplied_bgra(canvas), canvas.width, canvas.height
        else:
            self.bubble = None
            data, w, h, xoff, yoff = self.sp.bytes(name, idx, flip), sw, sh, 0, 0
        x = round(self.pos[0] - xoff - fx)
        y = round(self.pos[1] - yoff - fy)
        self.blit(data, w, h, x, y)

    def compose_bubble(self, frame):
        text = textwrap.fill(self.bubble[0], 30)
        d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        l, t, r, b = d.multiline_textbbox((0, 0), text, font=self.font, spacing=4)
        pad = 12
        bw, bh = r - l + pad * 2, b - t + pad * 2
        tail = 14
        cw = max(frame.width, bw + 4)
        ch = frame.height + bh + tail
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        xoff = (cw - frame.width) // 2
        canvas.alpha_composite(frame, (xoff, bh + tail))
        g = ImageDraw.Draw(canvas)
        bx = (cw - bw) // 2
        g.rounded_rectangle((bx, 0, bx + bw - 1, bh - 1), radius=14, fill=(250, 248, 255, 245), outline=(124, 92, 255, 255), width=2)
        mx = cw // 2
        g.polygon([(mx - 9, bh - 2), (mx + 9, bh - 2), (mx + 2, bh + tail)], fill=(250, 248, 255, 245))
        g.line([(mx - 9, bh - 1), (mx + 2, bh + tail), (mx + 9, bh - 1)], fill=(124, 92, 255, 255), width=2)
        g.multiline_text((bx + pad - l, pad - t), text, font=self.font, fill=(30, 24, 60, 255), spacing=4)
        return canvas, xoff, bh + tail

    def blit(self, data, w, h, x, y):
        if (w, h) not in self.dibs:
            bmi = BITMAPINFO()
            bmi.bmiHeader = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            bits = VP()
            hbmp = gdi32.CreateDIBSection(None, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            if len(self.dibs) > 20:  # bubble sizes vary; don't hoard bitmaps
                for old_bmp, _ in self.dibs.values():
                    gdi32.DeleteObject(old_bmp)
                self.dibs.clear()
            self.dibs[(w, h)] = (hbmp, bits)
        hbmp, bits = self.dibs[(w, h)]
        ctypes.memmove(bits, data, len(data))
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        old = gdi32.SelectObject(mem, hbmp)
        user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(POINT(x, y)), ctypes.byref(SIZE(w, h)), mem,
                                   ctypes.byref(POINT(0, 0)), 0, ctypes.byref(BLEND), 2)
        gdi32.SelectObject(mem, old)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)


if __name__ == "__main__":
    Nova().root.mainloop()
