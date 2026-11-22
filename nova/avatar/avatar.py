"""Desktop Nova: the 3D character that lives on your screen.

A see-through, always-on-top window (Win32 layered window with per-pixel alpha) that shows her 3D model. She
wanders around, talks in speech bubbles, and walks over to buttons to "press" them when the assistant clicks something.

Live 3D: a hidden browser window (desktop.py) runs static/nova3d.html?live=1, which poses the model each frame and
POSTs the pixels to /frame here; the reply tells it what to draw next. That lets her play moves the brain invents and
wear a new look (nova/avatar/body.py). Until live frames arrive she isn't drawn (there is no other picture of her).

Control API (used by nova/actions/tools/ and app.py):  POST http://127.0.0.1:5056/cmd  {"do": ..., ...}
  anim {name}            base animation: idle | talk | think | sleepy
  work {on}              busy on a task (not listening): after a moment she sits at her desk, typing on a laptop
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
  listen {on, hearing, secs}  she is listening (glowing waves at her ears; bigger while a voice comes in),
                         for secs unless renewed or turned off
  speech {words, secs, n, env, fps, start}  a sentence she is saying (voice.Mouth): its loudness moves her mouth
                         and body (live 3D) and its words pick a gesture (body.speech_gesture)
  show / hide
Run alone (from the project folder):  pythonw -m nova.avatar.avatar
"""
import ctypes
import json
import math
import os
import queue
import random
import textwrap
import threading
import time
import tkinter as tk
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw, ImageFont

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixel coordinates
except Exception:
    pass  # intentional: the 3D window must keep running without it

from nova import runtime
from nova.avatar import body, commands, hearing, win32  # noqa: E402
from nova import store  # noqa: E402
from nova.shared import REACTIONS, host_allowed, stand_area  # noqa: E402

PORT = 5056
SPEED = 420          # walking speed, pixels per second
TICK_MS = 33
LIVE_STALE = 0.5     # seconds without a new live frame before she counts as not drawn
RENDERER_ORIGINS = {"http://127.0.0.1:5055", "http://localhost:5055"}    # the hidden live renderer's page
from nova.audio import sfx  # noqa: E402


def sound_setting():
    """The 'react_to_sound' setting: dance to music and jump at sudden sounds playing on the laptop."""
    try:
        return bool(json.loads(store.current().settings.read_text(encoding="utf-8")).get("react_to_sound", True))
    except (OSError, ValueError):
        return True


class Rig:
    """The live renderer's frame (static/nova3d.html: W, H, ANIMS) - its size, where her feet are, how many frames each
    built-in animation has at 12 fps, and where her pointing hand is at the "tap" of the point animation."""
    w, h = 260, 340
    feet = (130, 325)
    fps = 12
    anims = {"idle": 24, "walk": 16, "talk": 12, "think": 12, "point": 14, "happy": 12, "wave": 16, "sleepy": 12,
             "laugh": 12, "dance": 24, "spin": 16, "clap": 12, "surprised": 12, "sad": 16, "confused": 16, "nod": 12,
             "shake": 12, "facepalm": 18, "bow": 16, "love": 16, "carry": 16, "yawn": 18, "look": 20, "work": 24,
             "wakeup": 40, "nap": 24, "liedown": 14}
    tap = 8                       # the frame of "point" where her finger touches the button
    tap_hand = (83, 114)          # her hand there, in frame pixels (facing right)

    def count(self, name):
        return self.anims[name]

    def hand(self, flip):
        hx, hy = self.tap_hand
        return (self.w - hx if flip else hx), hy


class Nova:
    def __init__(self):
        self.sp = Rig()
        self.root = tk.Tk()
        self.root.title("Nova 3D")
        self.root.overrideredirect(True)
        self.root.geometry(f"{self.sp.w}x{self.sp.h}+0+0")
        self.root.update_idletasks()
        self.hwnd = win32.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        self.base_style = win32.make_layered(self.hwnd)
        self.root.attributes("-topmost", True)

        left, top, right, bottom = stand_area()
        self.pos = [right - 220.0, float(bottom)]   # feet position on screen
        self.target = None
        self.facing_left = False
        self.base = "idle"
        self.oneshot = None          # dict(name, start, hold_at, done_event, after)
        self.bubble = None           # (text, until)
        self.thought = None          # (text, until): the thought cloud - what she's working on. Speech wins over it.
        self.visible = True
        self.next_wander = time.time() + random.uniform(8, 16)
        self.roam = body.roam_mode()
        self.roam_checked = time.time()
        self.last_errand = 0.0       # when she last walked somewhere for a command (point, carry, walk_to)
        self.arrive_event = None
        self.carrying = False        # walking while holding a window: uses the "carry" animation, never turns around
        self.working = False         # the assistant is busy on a task: she sits at her desk ("work" animation)
        self.working_since = 0.0
        self.working_end = 0.0       # when the assistant last stopped being busy (the desk stays a little longer)
        self.asleep = False          # napping in the bottom-right corner (5 minutes without a task, or "go to sleep")
        self.deep = False            # ...and asleep long enough that the app turned her mic off
        self.next_fidget = time.time() + random.uniform(20, 40)
        self.dibs = {}
        self.moves = body.load_moves()   # moves the brain invented, by name
        self.style = body.character_style()   # how the chosen character moves (characters/<id>/character.json)
        self.pokes = []                  # when she was clicked lately: quick clicks again and again are a poke
        self.look = body.load_look()
        self.look_v = 1                  # bumped on every look change, so the renderer knows to re-dress her
        self.live = None                 # (latest live frame as a PIL image, when it arrived)
        self.live_head = None            # the top of her head in that frame (x, y), sent by the renderer
        self.head_s = None               # the same, smoothed: where the speech bubble's tail points
        self.want = {"name": "idle", "t": 0}
        self.gestures = {}               # speech gestures in play (body.gesture_move), by name
        self.speech = None               # the sentence she is saying: {"start", "secs", "env", "fps"}
        self.listen_until = 0.0          # her ears glow until then (the microphone is taking in a request)
        self.hearing = False             # ...and a voice is coming in right now
        self.next_dance = time.time() + 20
        self.q = queue.Queue()
        self.drag = None
        try:
            self.font = ImageFont.truetype("segoeui.ttf", 17)
        except OSError:
            self.font = ImageFont.load_default()

        self.root.bind("<ButtonPress-1>", self.on_press)
        self.root.bind("<B1-Motion>", self.on_drag)
        self.root.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Button-3>", lambda e: self.react(random.choice(self.style["touch"]["right_click"])))
        runtime.spawn("avatar.serve", self.serve)
        runtime.spawn("avatar.watch_battery", self.watch_battery)
        runtime.spawn("avatar.watch_parent", watch_parent)
        self.ears = hearing.LaptopEars(on_event=lambda e: self.q.put(({"do": "_heard", "what": e}, None, None)),
                                       enabled=sound_setting)
        self.ears.start()
        runtime.spawn("avatar.build", sfx.build)     # makes data/sfx/*.wav the first time
        self.react("wave", sound=False)       # her own hello (Lucy's big wave, Makima's small one)
        self.say(self.style["touch"]["greeting"], 5)
        self.tick()

    # ------------------------------------------------------------ behaviour
    def start_oneshot(self, name, hold_at=None, after=None, repeat=1, speed=1.0, start=None):
        """speed: play faster/slower (dancing at the music's tempo); start: when frame 0 is (on a beat)."""
        ev = threading.Event()
        self.oneshot = {"name": name, "start": start or time.time(), "hold_at": hold_at, "event": ev, "after": after,
                        "held": False, "repeat": max(1, int(repeat)), "speed": speed}
        return ev

    def live_now(self):
        """Is the live 3D renderer sending frames right now?"""
        return bool(self.live and time.time() - self.live[1] < LIVE_STALE)

    def move_def(self, name):
        """An invented move or a speech gesture, by name (without the "move:" prefix)."""
        return self.moves.get(name) or self.style["moves"].get(name) or self.gestures.get(name)

    def count(self, name):
        """Frames in an animation at 12 fps. An invented move ("move:<name>") lasts its duration."""
        if name.startswith("move:"):
            m = self.move_def(name[5:])
            return max(1, round(m["duration"] * self.sp.fps)) if m else 1
        return self.sp.count(name)

    def talk_energy(self, now):
        """How loud she is right now (0..1) while saying a sentence, else None."""
        sp = self.speech
        if not sp or not (sp["start"] <= now < sp["start"] + sp["secs"]):
            return None
        i = int((now - sp["start"]) * sp["fps"])
        return sp["env"][i] if i < len(sp["env"]) else 0.0

    def on_speech(self, cmd):
        """A sentence is starting: keep its loudness for her mouth, and gesture to fit the words."""
        try:
            self.speech = {"start": float(cmd.get("start") or time.time()), "secs": float(cmd.get("secs") or 0),
                           "env": [float(v) for v in cmd.get("env") or []], "fps": float(cmd.get("fps") or 30)}
        except (TypeError, ValueError):
            self.speech = None
        talking = tuple(body.SPEECH_REACTIONS) + tuple("move:" + body.styled(self.style, r) for r in body.SPEECH_REACTIONS)
        busy = self.oneshot and not self.oneshot["name"].startswith(("move:gesture_",) + talking)
        if not self.live_now() or self.asleep or self.carrying or self.target or busy:
            return
        if self.at_desk(time.time()):      # at her laptop she keeps typing while she talks (no standing up to gesture)
            return
        g = body.speech_gesture(cmd.get("words", ""), self.speech["secs"] if self.speech else 0, int(cmd.get("n") or 0),
                                self.style["talk_hands"], self.style["gestures"])
        if isinstance(g, str):
            self.react(g, repeat=1, sound=False)      # her voice is the sound
        elif g:
            self.gestures = {g["name"]: g}
            self.start_oneshot("move:" + g["name"])

    def heard(self, what):
        """Something playing on the laptop (hearing.py) - her reactions to it are silent."""
        if what == "surprised" and not self.asleep and not self.oneshot and self.base == "idle":
            self.react("surprised", sound=False)

    def music_dance(self, now):
        """Music has been playing a while and she has nothing to do: dance at its tempo, starting on a beat."""
        m = self.ears.beat.music
        if not m or now < self.next_dance or self.asleep or self.oneshot or self.target or self.base != "idle":
            return
        if not self.ears.beat.loud_since or now - self.ears.beat.loud_since < 10:
            return
        self.next_dance = now + random.uniform(35, 70)
        speed = max(0.6, min(1.6, m["bpm"] / 120))     # her dance loop has 4 beats in 2 s = 120 BPM
        beat0 = m["last_beat"] + math.floor((now - m["last_beat"]) / m["period"]) * m["period"]
        own = body.styled(self.style, "dance")      # her own dance (style swap), on the beat like the built-in one
        if own in self.style["moves"] and self.live_now():
            self.start_oneshot("move:" + own, repeat=3, speed=speed * self.style["moves"][own]["duration"] / 2.0,   # 4 beats a loop
                               start=beat0)
        else:
            self.start_oneshot("dance", repeat=3, speed=speed, start=beat0)

    def react(self, name, repeat=None, sound=True):
        """Play a reaction - in her character's own way (style: her version of it, at her speed). Dancing and
        laughing look best a few times over. `sound`: with its sound effect.
        An invented move or a character's own move plays only while the live renderer is drawing her."""
        own = body.styled(self.style, name)
        if own in self.style["moves"] and own not in self.moves:
            if self.live_now():
                if sound:
                    self.sound(name)          # the sound of the reaction she is doing her way
                self.target = None if not self.carrying else self.target
                if repeat is None:
                    repeat = {"dance": 3, "laugh": 2, "clap": 2}.get(name, 1)
                return self.start_oneshot("move:" + own, repeat=min(int(repeat), 8), speed=self.style["speed"])
        elif own in self.moves or own in self.sp.anims:
            name = own
        if name in self.moves:
            if not self.live_now():
                return None
            if sound:
                self.sound(name)          # its own sound, if it has one (sfx: data/sfx/move_<name>.wav)
            self.target = None if not self.carrying else self.target
            return self.start_oneshot("move:" + name, repeat=min(int(repeat or 1), 8))
        if name not in self.sp.anims or name not in REACTIONS:
            return None
        if sound:
            self.sound(name)
        if repeat is None:
            repeat = {"dance": 3, "laugh": 2, "clap": 2}.get(name, 1)
        self.target = None if not self.carrying else self.target      # stop strolling to do it
        return self.start_oneshot(name, repeat=min(int(repeat), 8), speed=self.style["speed"])

    def sound(self, name):
        """A reaction's sound effect (sfx.py). Idle fidgets pass sound=False, so this only runs for real reactions."""
        self.last_sound = sfx.play(name)

    def fidget(self):
        """Small things she does by herself when nothing is happening."""
        hour = time.localtime().tm_hour
        sleepy_time = hour >= 23 or hour < 6
        if self.moves and self.live_now() and random.random() < .2:
            self.react(random.choice(list(self.moves)), sound=False)
            return
        # her own idle habits, by weight (style "fidgets"); her own moves need the renderer, else she just looks around
        name = body.pick_fidget(self.style, random.random, sleepy_time)
        if self.react(name, sound=False) is None:
            self.react("look", sound=False)

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
        left, top, right, bottom = stand_area()
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
        runtime.spawn("avatar.nap", lambda: (ev.wait(20), self.q.put(({"do": "_doze"}, None, None))))

    def wake_up(self, clicked=False):
        if not self.asleep:
            return
        self.asleep = False
        self.deep = False
        self.base = "idle"
        # Up off the floor, eyes flutter open, a rub of the eyes, a big stretch, a happy hop with sparkles, a wave.
        self.start_oneshot("wakeup", after="wave")
        if clicked:
            self.say("I'm up! ✨", 3)    # a bubble, not her voice: the mic is just coming back on
        self.next_wander = time.time() + random.uniform(20, 40)
        self.next_fidget = time.time() + random.uniform(25, 60)

    def walk_to(self, x, y):
        left, top, right, bottom = stand_area()
        x = min(max(x, left + self.sp.w * .25), right - self.sp.w * .25)
        y = min(max(y, top + self.sp.h * .9), bottom)
        self.target = (x, y)
        self.arrive_event = threading.Event()
        return self.arrive_event

    def point_spot(self, tx, ty):
        """Where her feet should be so her pointing hand ends up right next to (tx, ty)."""
        left, _, right, _ = stand_area()
        for flip in (False, True):
            hx, hy = self.sp.hand(flip)
            gap = 14 if not flip else -14
            fx = tx + gap + (self.sp.feet[0] - hx)
            fy = ty + 6 + (self.sp.feet[1] - hy)
            if left + self.sp.w * .3 < fx < right - self.sp.w * .3:
                return fx, fy, flip
        return fx, fy, flip

    def wander(self):
        """Where she goes when idle: by default she stays in the bottom-right corner so she never covers the user's
        work (setting "avatar_roam": corner / edge / free - see body.roam_mode). Working, she goes to her desk there."""
        mode = "corner" if self.working or time.time() - self.working_end < self.WORK_HOLD else self.roam
        return body.roam_spot(mode, stand_area(), self.sp.w, self.sp.h, random.uniform)

    WORK_AFTER = 1.5     # seconds busy before the desk comes out (a quick answer shouldn't flash it)
    WORK_HOLD = 6.0      # seconds she stays at the desk after the last step: a long task has gaps between its steps

    def at_desk(self, now):
        """Show her working at her laptop? Busy for a moment, standing at home - not listening, napping, walking or
        just back from pointing at something (the desk would appear in the middle of the screen)."""
        busy = self.working or now - self.working_end < self.WORK_HOLD
        return (busy and now - self.working_since >= self.WORK_AFTER and not self.asleep
                and now >= self.listen_until and not self.target and not self.drag
                and now - self.last_errand > body.ROAM_RETURN_AFTER and self.live_now())

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
                pass  # intentional: the 3D window must keep running without it
            time.sleep(60)

    # ------------------------------------------------------------ commands from the assistant
    def handle(self, cmd, done, result):
        """Do one command (nova/avatar/commands.py). Unless it finishes `done` itself, say where she is and end it."""
        if commands.run(self, cmd, done, result):
            return
        if result is not None:       # commands she queues for herself (battery, dozing off) have no caller waiting
            result["pos"] = self.pos
        if done:
            done.set()

    def serve(self):
        nova = self

        class H(BaseHTTPRequestHandler):
            # HTTP/1.1 keeps a connection open: the live renderer posts ~24 frames a second over one socket instead of
            # a new one per frame. One per frame left thousands of closed sockets waiting out TIME_WAIT on this port,
            # and every so often a new connection (the app's commands, a status check) drew a client port that
            # matched one of them: Windows dropped its SYN and it hung for 21 s (2026-11-20). Every reply therefore
            # carries a Content-Length (send()).
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def send(self, code, body=b"", headers=()):
                self.send_response(code)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _ours(self):
                """Only Nova (same laptop, our own Host name) may drive her - not a web page via DNS rebinding.
                The one page allowed in is her own live renderer, and only to /frame."""
                origin = self.headers.get("Origin")
                if host_allowed(self.headers.get("Host"), PORT) and (
                        not origin or (origin in RENDERER_ORIGINS and self.path.startswith("/frame"))):
                    return True
                self.close_connection = True         # its body (if any) is never read: don't reuse this socket
                self.send(403, headers=[("Connection", "close")])
                return False

            def do_OPTIONS(self):
                if not self._ours():
                    return
                self.send(204, headers=[("Access-Control-Allow-Origin", self.headers.get("Origin", "")),
                                        ("Access-Control-Allow-Methods", "POST")])

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
                    try:
                        hx, hy = float(q.get("hx", -1)), float(q.get("hy", -1))
                        nova.live_head = (hx, hy) if 0 <= hx <= w and -h <= hy <= h else None
                    except ValueError:
                        nova.live_head = None
                # 24 fps looks smooth and costs ~20% less than 30 (each frame is ~230 KB through the browser);
                # napping she barely moves; paused (hidden) the renderer only checks in now and then.
                pause = not nova.visible
                want = dict(nova.want, w=nova.sp.w, h=nova.sp.h, fps=2 if pause else 12 if nova.asleep else 24,
                            pause=pause)
                if lv != nova.look_v:
                    want.update(look=nova.look, look_v=nova.look_v)
                self.send(200, json.dumps(want).encode(), [("Content-Type", "application/json"),
                                                           ("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")])

            def do_POST(self):
                if not self._ours():
                    return
                if self.path.startswith("/frame"):
                    return self.frame()
                # exactly its body: on a kept-alive connection reading more would wait for the next request
                cmd = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
                done, result = threading.Event(), {}
                nova.q.put((cmd, done, result))
                ok = done.wait(float(cmd.get("timeout", 20)))
                self.send(200, json.dumps({"ok": ok, **result}).encode(), [("Content-Type", "application/json")])

            def do_GET(self):
                if not self._ours():
                    return
                b = nova.bubble
                body = json.dumps({"ok": True, "pos": nova.pos, "visible": nova.visible, "talking": nova.base == "talk",
                                   "asleep": nova.asleep, "deep": nova.deep, "base": nova.base,
                                   "bubble": b[0] if b and time.time() < b[1] else "",
                                   "reactions": [r for r in REACTIONS if r in nova.sp.anims],
                                   "moves": sorted(nova.moves), "live": nova.live_now(),
                                   "doing": nova.oneshot["name"] if nova.oneshot else None,
                                   "music": nova.ears.beat.music, "hearing_error": nova.ears.error,
                                   "sound_for": round(time.time() - nova.ears.beat.loud_since, 1)
                                   if nova.ears.beat.loud_since else 0}).encode()
                self.send(200, body, [("Content-Type", "application/json")])

        ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

    # ------------------------------------------------------------ mouse: drag her around, click to open chat
    def on_press(self, e):
        self.drag = (e.x_root, e.y_root, self.pos[0], self.pos[1], False)

    def on_drag(self, e):
        if not self.drag:
            return
        x0, y0, px, py, moved = self.drag
        if abs(e.x_root - x0) + abs(e.y_root - y0) > 4:
            if not moved and not self.asleep:      # picked up: her own reaction to it (style touch "picked_up")
                self.react(self.style["touch"]["picked_up"], sound=False)
            self.drag = (x0, y0, px, py, True)
            self.target = None
            self.pos = [px + e.x_root - x0, py + e.y_root - y0]

    def on_release(self, e):
        if self.drag and not self.drag[4] and self.asleep:
            # Clicked while asleep: wake up (with the wake-up move) and tell the app, which turns her mic back on.
            self.wake_up(clicked=True)
            runtime.spawn("avatar.tell_app_awake", self.tell_app_awake)
        elif self.drag and not self.drag[4]:
            self.touched(time.time())
            try:
                urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:5055/api/show_window", method="POST"), timeout=1)
            except Exception:
                pass  # intentional: the 3D window must keep running without it
        self.drag = None
        if not self.asleep:
            self.next_wander = time.time() + random.uniform(20, 40)
            self.last_errand = time.time()

    def touched(self, now):
        """Clicked: her own greeting move (style touch "click"); clicked again and again (touch "pokes" times in
        a few seconds) is a poke, and she reacts to that her way (touch "poke", with her "poke_say" bubble)."""
        t = self.style["touch"]
        self.pokes = [p for p in self.pokes if now - p < 5] + [now]
        if len(self.pokes) >= t["pokes"]:
            self.pokes = []
            self.react(t["poke"])
            if t["poke_say"]:
                self.say(t["poke_say"], 3)
        else:
            self.react(t["click"], sound=False)

    @staticmethod
    def tell_app_awake():
        try:
            urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:5055/api/wake", method="POST"), timeout=3)
        except Exception:
            pass  # intentional: the 3D window must keep running without it

    # ------------------------------------------------------------ main loop
    def tick(self):
        now = time.time()
        if now - self.roam_checked > 5:                  # a change in the Model dialog applies within seconds
            self.roam, self.roam_checked = body.roam_mode(), now
            try:                                         # ... and so does a switched character or an edited style
                self.style = body.character_style()
            except Exception:
                pass  # intentional: the 3D window must keep running without it
        try:
            while True:
                self.handle(*self.q.get_nowait())
        except queue.Empty:
            pass

        moving = False
        if self.target and not self.drag:
            dx, dy = self.target[0] - self.pos[0], self.target[1] - self.pos[1]
            dist = math.hypot(dx, dy)
            # her own pace (style walk "pace") sets how fast she covers ground too, not only her step rate:
            # otherwise a slow walker (Makima, 0.8) slides along at everyone's speed
            pace = 1.0 if self.carrying else self.style["walk"]["pace"]
            step = SPEED * pace * (1.6 if dist > 900 else 1) * TICK_MS / 1000
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
        elif (not self.oneshot and self.base == "idle" and now > self.next_wander and not self.drag
              and ((self.roam == "free" and not self.working) or now - self.last_errand > body.ROAM_RETURN_AFTER)):
            self.walk_to(*self.wander())
            self.next_wander = now + (random.uniform(3, 6) if self.roam == "corner" or self.working else random.uniform(15, 35))
        elif (not self.oneshot and self.base == "idle" and now > self.next_fidget and not self.drag and not self.target
              and not (self.bubble and now < self.bubble[1]) and not (self.thought and now < self.thought[1])):
            self.fidget()
            self.next_fidget = now + random.uniform(25, 60)

        # `at` is the position in the animation counted in 12 fps frames, as a float: the live renderer gets the
        # exact phase at/n, so it moves smoothly at its own frame rate.
        if self.oneshot:
            o = self.oneshot
            n = self.count(o["name"])
            at = max(0.0, (now - o["start"]) * self.sp.fps * o.get("speed", 1.0))
            if o["hold_at"] is not None and at >= o["hold_at"]:
                at = o["hold_at"]
                if not o["held"]:
                    o["held"] = True
                    o["event"].set()
                o["start"] = now - at / (self.sp.fps * o.get("speed", 1.0))
            if at >= n and o["repeat"] > 1:
                o["repeat"] -= 1
                o["start"] = now
                at = 0
            if at >= n:
                o["event"].set()
                after = o["after"]
                self.oneshot = None
                if after:
                    if after in REACTIONS:     # a reaction in her own way (the wave after waking up); else as is
                        self.react(after, repeat=1, sound=False)
                    else:
                        self.start_oneshot(after)    # e.g. "liedown" after the yawn
                name, at = self.base, now * self.sp.fps
            else:
                name = o["name"]
        elif moving:
            name = "carry" if self.carrying else "walk"
            at = now * self.sp.fps * 1.3 * (self.style["walk"]["pace"] if name == "walk" else 1)   # her own step rate
        else:
            name, at = self.base, now * self.sp.fps
            if name in ("idle", "think", "talk") and self.at_desk(now):
                name = "work"
        n = self.count(name)
        move = self.move_def(name[5:]) if name.startswith("move:") else None
        talk = self.talk_energy(now)
        # Grooving to the laptop's music: only while standing about (not talking, walking, napping or reacting).
        phase = self.ears.beat.phase(now) if not (talk is not None or moving or self.asleep or self.oneshot
                                                  or self.base != "idle" or name == "work") else None
        groove = {"phase": round(phase, 3), "amp": round(self.ears.beat.music["amp"], 2)} if phase is not None else None
        listen = {"hearing": self.hearing} if now < self.listen_until else None
        self.want = {"name": name, "t": (at % n) / n if n > 1 else 0, "move": move, "talk": talk, "groove": groove,
                     "listen": listen}
        self.music_dance(now)

        flip = self.facing_left if name in ("walk", "point", "carry") else False
        if self.visible:
            self.render(flip)
        self.root.after(TICK_MS, self.tick)

    def frame_image(self, flip):
        """This moment's picture of her: the latest live 3D frame, or None while the renderer isn't drawing her.
        (Live frames lag one tick behind `want` - 33 ms, too little to see.)"""
        if not (self.live_now() and self.live[0].size == (self.sp.w, self.sp.h)):
            return None
        img = self.live[0]
        return img.transpose(Image.FLIP_LEFT_RIGHT) if flip else img

    def render(self, flip):
        sw, sh = self.sp.w, self.sp.h
        fx, fy = self.sp.feet
        now = time.time()
        if self.bubble and now >= self.bubble[1]:
            self.bubble = None
        if self.thought and now >= self.thought[1]:
            self.thought = None
        img = self.frame_image(flip)
        if img is None:                       # no live frame yet: draw nothing (a see-through window)
            data, w, h, xoff, yoff = bytes(sw * sh * 4), sw, sh, 0, 0
        elif self.bubble or self.thought:     # speaking beats thinking: the cloud waits under the speech bubble
            text, thought = (self.bubble[0], False) if self.bubble else (self.thought[0], True)
            canvas, xoff, yoff = self.compose_bubble(img, text, thought, self.pos[0] - fx, self.bubble_anchor(img))
            data, w, h = win32.premultiplied_bgra(canvas), canvas.width, canvas.height
        else:
            data, w, h, xoff, yoff = win32.premultiplied_bgra(img), sw, sh, 0, 0
        x = round(self.pos[0] - xoff - fx)
        y = round(self.pos[1] - yoff - fy)
        self.blit(data, w, h, x, y)

    def bubble_anchor(self, img):
        """Where the bubble's tail points (x, y in her frame): just above her head, which the live renderer reports
        (else the top of the picture). Smoothed, so the bubble glides with her instead of jumping with every bob."""
        target = self.live_head
        if target is None:
            box = img.getchannel("A").getbbox()
            target = (img.width / 2, box[1]) if box else (img.width / 2, 0)
        if self.head_s is None or abs(target[0] - self.head_s[0]) + abs(target[1] - self.head_s[1]) > 120:
            self.head_s = target                         # a big jump (a new pose, lying down): go straight there
        else:
            k = 0.25
            self.head_s = (self.head_s[0] + (target[0] - self.head_s[0]) * k, self.head_s[1] + (target[1] - self.head_s[1]) * k)
        return self.head_s

    def compose_bubble(self, frame, text, thought=False, sx=None, head=None):
        """Her frame with a bubble above it: a speech bubble with a pointed tail, or (thought=True) a thought cloud -
        rounder, tinted, with little circles rising from her head instead of a tail.
        sx = where her frame's left edge is on the screen: the bubble slides sideways to stay fully on the screen
        (she lives at the right edge), while its tail keeps pointing at her.
        head = (x, y) in her frame where the tail's tip goes (above her head); None = the middle of the frame's top."""
        text = textwrap.fill(text, 30)
        d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        l, t, r, b = d.multiline_textbbox((0, 0), text, font=self.font, spacing=4)
        pad = 14 if thought else 12
        bw, bh = r - l + pad * 2, b - t + pad * 2
        tail = 22 if thought else 14
        fw, fh = frame.width, frame.height
        hx, hy = head if head else (fw / 2, 0)
        hy = max(0, hy - 4)                                              # a little gap above her hair
        fy0 = max(0, int(bh + tail - hy))                                # her frame's top in the canvas
        by0 = int(fy0 + hy - tail - bh)                                  # the bubble's top in the canvas
        ch = max(fy0 + fh, by0 + bh + tail)
        if sx is None:
            sx = 0
            left, right = -10 ** 6, 10 ** 6
        else:
            left, _, right, _ = stand_area()
        bub_left = sx + hx - bw / 2                                      # centred over her head ...
        bub_left = max(min(bub_left, right - 6 - bw), left + 6)          # ... unless that runs off the screen
        canvas_left = min(sx, bub_left)
        cw = int(max(sx + fw, bub_left + bw) - canvas_left) + 1
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        xoff = int(sx - canvas_left)
        canvas.alpha_composite(frame, (xoff, fy0))
        g = ImageDraw.Draw(canvas)
        bx = int(bub_left - canvas_left)
        mx = int(min(max(xoff + hx, bx + 24), bx + bw - 24))             # the tail stays over her, inside the bubble
        line = (124, 92, 255, 255)
        if thought:
            fill = (236, 250, 254, 240)
            g.rounded_rectangle((bx, by0, bx + bw - 1, by0 + bh - 1), radius=min(bh // 2, 24), fill=fill, outline=(34, 190, 220, 255), width=2)
            for cx, cy, rad in ((mx + 4, by0 + bh + 6, 6), (mx + 12, by0 + bh + 17, 4)):     # the trail of little thought circles
                g.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=fill, outline=(34, 190, 220, 255), width=2)
            g.multiline_text((bx + pad - l, by0 + pad - t), text, font=self.font, fill=(20, 60, 80, 255), spacing=4)
        else:
            y1 = by0 + bh
            g.rounded_rectangle((bx, by0, bx + bw - 1, y1 - 1), radius=14, fill=(250, 248, 255, 245), outline=line, width=2)
            g.polygon([(mx - 9, y1 - 2), (mx + 9, y1 - 2), (mx + 2, y1 + tail)], fill=(250, 248, 255, 245))
            g.line([(mx - 9, y1 - 1), (mx + 2, y1 + tail), (mx + 9, y1 - 1)], fill=line, width=2)
            g.multiline_text((bx + pad - l, by0 + pad - t), text, font=self.font, fill=(30, 24, 60, 255), spacing=4)
        return canvas, xoff, fy0

    def blit(self, data, w, h, x, y):
        win32.blit(self.hwnd, self.dibs, data, w, h, x, y)


def watch_parent():
    """Close when the Nova app that started her is gone (it passes its pid in NOVA_PARENT), so a crashed or killed
    Nova never leaves an old copy of her walking about on the screen, running yesterday's code."""
    try:
        parent = int(os.environ.get("NOVA_PARENT", "0"))
    except ValueError:
        return
    if not parent:
        return
    import psutil
    while True:
        time.sleep(3)
        if not psutil.pid_exists(parent):
            os._exit(0)


if __name__ == "__main__":
    Nova().root.mainloop()
