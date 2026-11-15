"""Desktop Nova: the 3D character that lives on your screen.

A see-through, always-on-top window (Win32 layered window with per-pixel alpha) that shows her 3D model. She
wanders around, talks in speech bubbles, and walks over to buttons to "press" them when the assistant clicks something.

Live 3D: a hidden browser window (desktop.py) runs static/nova3d.html?live=1, which poses the model each frame and
POSTs the pixels to /frame here; the reply tells it what to draw next. That lets her play moves the brain invents and
wear a new look (nova/avatar/body.py). Whenever live frames stop arriving, the saved sprite frames take over.

Control API (used by tools.py / app.py):  POST http://127.0.0.1:5056/cmd  {"do": ..., ...}
  anim {name}            base animation: idle | talk | think | sleepy
  sleep {on}             on: walk to the bottom-right corner and doze there (no wandering); off: wake up and wave
  say {text, secs}       speech bubble
  thought {text, secs}   thought cloud: what she's working on right now (empty text clears it)
  walk_to {x, y}         walk so her feet are at (x, y)          (waits until she arrives)
  go_point {x, y}        walk next to (x, y) and raise her hand to it (waits until the "tap")
  finish_point           finish the tap, then a happy hop
  happy / wave           one-shot animations
  react {name, repeat}   any reaction once (or `repeat` times): laugh, dance, spin, clap, surprised, sad, confused,
                         nod, shake, facepalm, bow, love, yawn, look, happy, wave
  carry_to {x, y}        walk to (x, y) holding something up beside her (used to drag a window; waits until she arrives)
  clickthrough {on}      let the mouse pass through her
  move {move}            play (and remember) a move the brain invented - body.clean_move format; needs live 3D
  look {look}            wear this look (body.load_look format); needs live 3D
  live {on}              use live 3D frames (on) or only the saved sprites (off)
  show / hide
Run alone (from the project folder):  pythonw -m nova.avatar.avatar
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

from PIL import Image, ImageChops, ImageDraw, ImageFont

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixel coordinates
except Exception:
    pass

from nova.avatar import body  # noqa: E402
from nova.shared import DATA, REACTIONS, ROOT, host_allowed, work_area  # noqa: E402

FRAMES = ROOT / "static" / "nova3d"
PORT = 5056
SCALE = 0.8          # character size on screen
SPEED = 420          # walking speed, pixels per second
TICK_MS = 33
LIVE_STALE = 0.5     # seconds without a new live frame before the saved sprites take over again
RENDERER_ORIGINS = {"http://127.0.0.1:5055", "http://localhost:5055"}    # the hidden live renderer's page
from nova.audio import sfx  # noqa: E402

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


def live_setting():
    """The 'live_3d' setting (data/settings.json, written by the app): live 3D frames, or only the saved sprites."""
    try:
        return bool(json.loads((DATA / "settings.json").read_text(encoding="utf-8")).get("live_3d", True))
    except (OSError, ValueError):
        return True


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
        self.thought = None          # (text, until): the thought cloud - what she's working on. Speech wins over it.
        self.visible = True
        self.next_wander = time.time() + random.uniform(8, 16)
        self.arrive_event = None
        self.carrying = False        # walking while holding a window: uses the "carry" animation, never turns around
        self.asleep = False          # napping in the bottom-right corner (5 minutes without a task, or "go to sleep")
        self.next_fidget = time.time() + random.uniform(20, 40)
        self.dibs = {}
        self.moves = body.load_moves()   # moves the brain invented, by name
        self.look = body.load_look()
        self.look_v = 1                  # bumped on every look change, so the renderer knows to re-dress her
        self.live_on = live_setting()
        self.live = None                 # (latest live frame as a PIL image, when it arrived)
        self.want = {"name": "idle", "t": 0}
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
        threading.Thread(target=sfx.build, daemon=True).start()     # makes data/sfx/*.wav the first time
        self.start_oneshot("wave")
        self.say("Hi! I'm Nova. I'll be right here on your screen ✨", 5)
        self.tick()

    # ------------------------------------------------------------ behaviour
    def start_oneshot(self, name, hold_at=None, after=None, repeat=1):
        ev = threading.Event()
        self.oneshot = {"name": name, "start": time.time(), "hold_at": hold_at, "event": ev, "after": after, "held": False,
                        "repeat": max(1, int(repeat))}
        return ev

    def live_now(self):
        """Is the live 3D renderer sending frames right now?"""
        return bool(self.live_on and self.live and time.time() - self.live[1] < LIVE_STALE)

    def count(self, name):
        """Frames in an animation at sprite speed. An invented move ("move:<name>") lasts its duration."""
        if name.startswith("move:"):
            m = self.moves.get(name[5:])
            return max(1, round(m["duration"] * self.sp.fps)) if m else 1
        return self.sp.count(name)

    def react(self, name, repeat=None, sound=True):
        """Play a reaction. Dancing and laughing look best a few times over. `sound`: with its sound effect.
        An invented move plays only while live 3D is on (the sprites can't show it)."""
        if name in self.moves:
            if not self.live_now():
                return None
            self.target = None if not self.carrying else self.target
            return self.start_oneshot("move:" + name, repeat=min(int(repeat or 1), 8))
        if name not in self.sp.images or name not in REACTIONS:
            return None
        if sound:
            self.sound(name)
        if repeat is None:
            repeat = {"dance": 3, "laugh": 2, "clap": 2}.get(name, 1)
        self.target = None if not self.carrying else self.target      # stop strolling to do it
        return self.start_oneshot(name, repeat=min(int(repeat), 8))

    def sound(self, name):
        """A reaction's sound effect (sfx.py). Idle fidgets pass sound=False, so this only runs for real reactions."""
        self.last_sound = sfx.play(name)

    def fidget(self):
        """Small things she does by herself when nothing is happening."""
        hour = time.localtime().tm_hour
        sleepy_time = hour >= 23 or hour < 6
        r = random.random()
        if self.moves and self.live_now() and random.random() < .2:
            self.react(random.choice(list(self.moves)), sound=False)
            return
        if sleepy_time and r < .45:
            self.react("yawn", sound=False)
        elif r < .75:
            self.react("look", sound=False)
        elif r < .85:
            self.react("spin", sound=False)
        elif r < .93:
            self.react("nod", sound=False)
        else:
            self.react("yawn", sound=False)

    def say(self, text, secs=6):
        text = " ".join(str(text).split())
        if len(text) > 170:
            text = text[:167] + "…"
        self.bubble = (text, time.time() + secs) if text else None

    def think_aloud(self, text, secs=12):
        # Segoe UI has no emoji: they'd show as empty boxes, and the cloud's shape already says "thinking".
        text = "".join(ch for ch in str(text) if ord(ch) < 0x2190 or ch in "→…")
        text = " ".join(text.split())
        if len(text) > 140:
            text = text[:137] + "…"
        self.thought = (text, time.time() + secs) if text else None

    def corner(self):
        """Where her feet go when she naps: the bottom-right corner of the screen."""
        left, top, right, bottom = work_area()
        return right - self.sp.w * .42, bottom      # far enough in that her hair and "z" aren't cut off

    def nap(self):
        if self.asleep:
            return
        self.asleep = True
        self.oneshot = None
        self.thought = None
        self.base = "idle"               # walk over first, then lie down
        self.next_wander = self.next_fidget = time.time() + 10 ** 9
        ev = self.walk_to(*self.corner())
        threading.Thread(target=lambda: (ev.wait(20), self.q.put(({"do": "_doze"}, None, None))), daemon=True).start()

    def wake_up(self):
        if not self.asleep:
            return
        self.asleep = False
        self.base = "idle"
        self.start_oneshot("wave")      # no "Hey!" here: she's usually woken to listen, and the mic would hear it
        self.next_wander = time.time() + random.uniform(20, 40)
        self.next_fidget = time.time() + random.uniform(25, 60)

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
                elif not low and self.base == "sleepy" and not self.asleep:
                    self.q.put(({"do": "anim", "name": "idle"}, None, None))
            except Exception:
                pass
            time.sleep(60)

    # ------------------------------------------------------------ commands from the assistant
    def handle(self, cmd, done, result):
        do = cmd.get("do")
        if do == "anim" and cmd.get("name") in self.sp.images:
            name = cmd["name"]
            if self.asleep and name != "talk":
                name = "sleepy"          # napping: only talking (in her sleep) changes the pose
            self.base = name
        elif do == "sleep":
            self.nap() if cmd.get("on") else self.wake_up()
        elif do == "_doze":
            if self.asleep:
                self.base = "sleepy"
                self.start_oneshot("yawn")
        elif do == "say":
            self.say(cmd.get("text", ""), float(cmd.get("secs", 6)))
        elif do == "thought":
            self.think_aloud(cmd.get("text", ""), float(cmd.get("secs", 12)))
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
            self.sound(do)
        elif do == "react":
            ev = self.react(cmd.get("name", ""), cmd.get("repeat"))
            result["played"] = bool(ev)
            result["sound"] = bool(ev) and getattr(self, "last_sound", False)
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
        elif do == "move":
            move, err = body.clean_move(**{k: cmd.get("move", {}).get(k) for k in ("name", "duration", "keys", "loop")})
            if err:
                result["error"] = err
            else:
                self.moves[move["name"]] = move
                result["live"] = self.live_now()
                ev = self.react(move["name"], cmd.get("repeat"))
                result["played"] = bool(ev)
                if ev and cmd.get("wait"):
                    threading.Thread(target=lambda: (ev.wait(30), done.set()), daemon=True).start()
                    return
        elif do == "forget_move":
            self.moves.pop(cmd.get("name"), None)
        elif do == "look":
            self.look = {**body.DEFAULT_LOOK, **(cmd.get("look") or {})}
            self.look_v += 1
            result["live"] = self.live_now()
        elif do == "live":
            self.live_on = bool(cmd.get("on"))
        if result is not None:       # commands she queues for herself (battery, dozing off) have no caller waiting
            result["pos"] = self.pos
        if done:
            done.set()

    def serve(self):
        nova = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _ours(self):
                """Only Nova (same laptop, our own Host name) may drive her - not a web page via DNS rebinding.
                The one page allowed in is her own live renderer, and only to /frame."""
                origin = self.headers.get("Origin")
                if host_allowed(self.headers.get("Host"), PORT) and (
                        not origin or (origin in RENDERER_ORIGINS and self.path.startswith("/frame"))):
                    return True
                self.send_response(403)
                self.end_headers()
                return False

            def do_OPTIONS(self):
                if not self._ours():
                    return
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", ""))
                self.send_header("Access-Control-Allow-Methods", "POST")
                self.end_headers()

            def frame(self):
                """A live frame from the renderer in, what to draw next out."""
                from urllib.parse import parse_qs, urlparse
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                data = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
                try:
                    w, h, lv = int(q.get("w", 0)), int(q.get("h", 0)), int(q.get("lv", -1))
                except ValueError:
                    w = h = lv = 0
                if w == nova.sp.w and h == nova.sp.h and len(data) == w * h * 4:
                    # WebGL reads rows bottom-up: the -1 stride turns the picture the right way up
                    nova.live = (Image.frombytes("RGBA", (w, h), data, "raw", "RGBA", 0, -1), time.time())
                want = dict(nova.want, w=nova.sp.w, h=nova.sp.h,
                            fps=12 if nova.asleep else 30, pause=not (nova.visible and nova.live_on))
                if lv != nova.look_v:
                    want.update(look=nova.look, look_v=nova.look_v)
                out = json.dumps(want).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
                self.end_headers()
                self.wfile.write(out)

            def do_POST(self):
                if not self._ours():
                    return
                if self.path.startswith("/frame"):
                    return self.frame()
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
                                   "asleep": nova.asleep, "base": nova.base,
                                   "bubble": b[0] if b and time.time() < b[1] else "",
                                   "reactions": [r for r in REACTIONS if r in nova.sp.images],
                                   "moves": sorted(nova.moves), "live": nova.live_now()}).encode()
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
        if not self.asleep:
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
              and not (self.bubble and now < self.bubble[1]) and not (self.thought and now < self.thought[1])):
            self.fidget()
            self.next_fidget = now + random.uniform(25, 60)

        # `at` is the position in the animation counted in sprite frames, as a float: the sprites show frame
        # int(at); the live renderer gets the exact phase at/n, so it moves smoothly at 30 fps.
        if self.oneshot:
            o = self.oneshot
            n = self.count(o["name"])
            at = (now - o["start"]) * self.sp.fps
            if o["hold_at"] is not None and at >= o["hold_at"]:
                at = o["hold_at"]
                if not o["held"]:
                    o["held"] = True
                    o["event"].set()
                o["start"] = now - at / self.sp.fps
            if at >= n and o["repeat"] > 1:
                o["repeat"] -= 1
                o["start"] = now
                at = 0
            if at >= n:
                o["event"].set()
                after = o["after"]
                self.oneshot = None
                if after:
                    self.start_oneshot(after)
                name, at = self.base, now * self.sp.fps
            else:
                name = o["name"]
        elif moving:
            name = "carry" if self.carrying and "carry" in self.sp.images else "walk"
            at = now * self.sp.fps * 1.3
        else:
            name, at = self.base, now * self.sp.fps
        n = self.count(name)
        idx = int(at) % n
        move = self.moves.get(name[5:]) if name.startswith("move:") else None
        self.want = {"name": name, "t": (at % n) / n if n > 1 else 0, "move": move}

        flip = self.facing_left if name in ("walk", "point", "carry") else False
        if self.visible:
            self.render(name, idx, flip)
        self.root.after(TICK_MS, self.tick)

    def frame_image(self, name, idx, flip):
        """This moment's picture of her: the live 3D frame when the renderer is running, else the saved sprite.
        (Live frames lag one tick behind `want` - 33 ms, too little to see.)"""
        if self.live_now() and self.live[0].size == (self.sp.w, self.sp.h):
            img = self.live[0]
            return img.transpose(Image.FLIP_LEFT_RIGHT) if flip else img, True
        if name.startswith("move:"):          # the sprites have no picture of an invented move
            name, idx = self.base, int(time.time() * self.sp.fps) % self.sp.count(self.base)
        return self.sp.image(name, idx, flip), False

    def render(self, name, idx, flip):
        sw, sh = self.sp.w, self.sp.h
        fx, fy = self.sp.feet
        now = time.time()
        if self.bubble and now >= self.bubble[1]:
            self.bubble = None
        if self.thought and now >= self.thought[1]:
            self.thought = None
        img, live = self.frame_image(name, idx, flip)
        if self.bubble or self.thought:       # speaking beats thinking: the cloud waits under the speech bubble
            text, thought = (self.bubble[0], False) if self.bubble else (self.thought[0], True)
            canvas, xoff, yoff = self.compose_bubble(img, text, thought)
            data, w, h = premultiplied_bgra(canvas), canvas.width, canvas.height
        elif live or name.startswith("move:"):     # not one of the cached sprite pictures
            data, w, h, xoff, yoff = premultiplied_bgra(img), sw, sh, 0, 0
        else:
            data, w, h, xoff, yoff = self.sp.bytes(name, idx, flip), sw, sh, 0, 0
        x = round(self.pos[0] - xoff - fx)
        y = round(self.pos[1] - yoff - fy)
        self.blit(data, w, h, x, y)

    def compose_bubble(self, frame, text, thought=False):
        """Her frame with a bubble above it: a speech bubble with a pointed tail, or (thought=True) a thought cloud -
        rounder, tinted, with little circles rising from her head instead of a tail."""
        text = textwrap.fill(text, 30)
        d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        l, t, r, b = d.multiline_textbbox((0, 0), text, font=self.font, spacing=4)
        pad = 14 if thought else 12
        bw, bh = r - l + pad * 2, b - t + pad * 2
        tail = 22 if thought else 14
        cw = max(frame.width, bw + 4)
        ch = frame.height + bh + tail
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        xoff = (cw - frame.width) // 2
        canvas.alpha_composite(frame, (xoff, bh + tail))
        g = ImageDraw.Draw(canvas)
        bx = (cw - bw) // 2
        mx = cw // 2
        line = (124, 92, 255, 255)
        if thought:
            fill = (236, 250, 254, 240)
            g.rounded_rectangle((bx, 0, bx + bw - 1, bh - 1), radius=min(bh // 2, 24), fill=fill, outline=(34, 190, 220, 255), width=2)
            for cx, cy, rad in ((mx + 4, bh + 6, 6), (mx + 12, bh + 17, 4)):     # the trail of little thought circles
                g.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=fill, outline=(34, 190, 220, 255), width=2)
            g.multiline_text((bx + pad - l, pad - t), text, font=self.font, fill=(20, 60, 80, 255), spacing=4)
        else:
            g.rounded_rectangle((bx, 0, bx + bw - 1, bh - 1), radius=14, fill=(250, 248, 255, 245), outline=line, width=2)
            g.polygon([(mx - 9, bh - 2), (mx + 9, bh - 2), (mx + 2, bh + tail)], fill=(250, 248, 255, 245))
            g.line([(mx - 9, bh - 1), (mx + 2, bh + tail), (mx + 9, bh - 1)], fill=line, width=2)
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
