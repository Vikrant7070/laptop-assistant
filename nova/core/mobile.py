"""Nova on your iPhone: a small web app (installed from Safari with Share -> Add to Home Screen) that talks to Nova on
the laptop. The laptop stays the one brain; the phone is a window.

This is a SECOND little server, not the main one: it listens on 127.0.0.1 only (it refuses to start anywhere else) and
you reach it from the phone through Tailscale (`tailscale serve`), which gives it a private https address that only
devices signed in to your Tailscale account can open. Nothing is opened on your router.

Safety, in this order:
  * every call except pairing needs a device key (Authorization: Bearer ...). Being "on the same network" or "coming
    from 127.0.0.1" (which is where Tailscale's proxy comes from) proves nothing;
  * a device gets its key by sending the 6-digit pairing code shown in Nova's settings (valid 10 minutes); five wrong
    codes lock pairing for 10 minutes; the keys are stored only as hashes (data/devices.json) and each device can be
    removed in the settings;
  * browsers' cross-site requests are refused (JSON only, no CORS headers, Origin must match Host);
  * requests are answered like the Telegram remote: anything that changes the laptop asks Yes / No first, even on Full
    access (app.py passes remote=True).
Her answers come back as events the page long-polls (/m/events), so a slow answer never ties up a connection.
"""
import hashlib
import hmac
import json
import queue
import re
import secrets
import threading
import time
import urllib.parse
from collections import deque
from functools import wraps

from nova import runtime
from flask import Flask, jsonify, request, send_from_directory

from nova import store
from nova.shared import ROOT

PORT = 5057
HOST = "127.0.0.1"
PAIR_SECS = 600            # a pairing code works for 10 minutes
LOCK_SECS = 600            # five wrong codes lock pairing for 10 minutes
MAX_WRONG = 5
MAX_TEXT = 2000
STATIC = ROOT / "static" / "mobile"


def _file():
    return store.current().devices


def _load():
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("devices"), list) else {"devices": []}
    except Exception:
        return {"devices": []}


def _save(d):
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(f)


def _hash(key):
    return hashlib.sha256(str(key).encode()).hexdigest()


class Mobile:
    """on_message(text) -> Nova's answer dict (reply / pending / activity / error); on_confirm(approved) -> answer dict;
    on_stop() -> bool; status_text() -> str. All set by app.py, like phone.Phone."""

    def __init__(self, on_message=None, on_confirm=None, on_stop=None, status_text=None, state=None, tasks=None,
                 on_answer=None):
        self.on_message, self.on_confirm, self.on_stop, self.status_text = on_message, on_confirm, on_stop, status_text
        self.state = state or (lambda: {})
        self.tasks = tasks or (lambda: [])      # the Tasks page's rows, read-only
        self.on_answer = on_answer               # on_answer(card id, answers) -> reply dict: a tap on a question card
        self.pair_code, self.pair_until = "", 0.0
        self.wrong, self.locked_until = 0, 0.0
        self.events, self.next_id = deque(maxlen=50), 1
        self.cond = threading.Condition()
        self.work = queue.Queue()
        self.server, self.thread, self.worker = None, None, None
        self.lock = threading.Lock()
        self.app = self._build()

    # ---------------------------------------------------------------- devices and pairing
    def devices(self):
        return [{"id": d["id"], "name": d.get("name", ""), "created": d.get("created", 0), "seen": d.get("seen", 0)}
                for d in _load()["devices"]]

    def remove(self, device_id):
        d = _load()
        before = len(d["devices"])
        d["devices"] = [x for x in d["devices"] if x["id"] != device_id]
        _save(d)
        return len(d["devices"]) < before

    def new_pair_code(self):
        self.pair_code = f"{secrets.randbelow(1000000):06d}"
        self.pair_until = time.time() + PAIR_SECS
        self.wrong = 0
        return self.pair_code

    def pairing(self):
        """The code to show in settings (or "" when none is open)."""
        return self.pair_code if self.pair_code and time.time() < self.pair_until else ""

    def pair(self, code, name):
        """-> (key, "") or ("", why). One call = one guess; a right code is used up at once."""
        now = time.time()
        if now < self.locked_until:
            return "", f"Too many wrong codes. Try again in {int(self.locked_until - now) // 60 + 1} minutes."
        want = self.pairing()
        ok = bool(want) and hmac.compare_digest(str(code or "").strip(), want)
        if not ok:
            self.wrong += 1
            if self.wrong >= MAX_WRONG:
                self.locked_until, self.wrong, self.pair_code = now + LOCK_SECS, 0, ""
            return "", "That code isn't right (or it expired). Ask Nova's settings for a new one."
        self.pair_code, self.wrong = "", 0
        key = secrets.token_urlsafe(32)
        d = _load()
        d["devices"].append({"id": secrets.token_hex(4), "name": re.sub(r"[^\w .'-]", "", str(name or "iPhone"))[:40] or "iPhone",
                             "hash": _hash(key), "created": now, "seen": now})
        _save(d)
        return key, ""

    def device_for(self, key):
        if not key:
            return None
        h = _hash(key)
        d = _load()
        for dev in d["devices"]:
            if hmac.compare_digest(dev["hash"], h):
                if time.time() - dev.get("seen", 0) > 60:          # (don't rewrite the file on every poll)
                    dev["seen"] = time.time()
                    _save(d)
                return dev
        return None

    # ---------------------------------------------------------------- what Nova tells the phone
    def add(self, kind, **data):
        with self.cond:
            self.events.append({"id": self.next_id, "kind": kind, "at": time.time(), **data})
            self.next_id += 1
            self.cond.notify_all()

    def since(self, after, wait=0.0):
        end = time.time() + wait
        with self.cond:
            while True:
                items = [e for e in self.events if e["id"] > after]
                left = end - time.time()
                if items or left <= 0:
                    return items
                self.cond.wait(min(left, 5))

    def say(self, out):
        """An answer dict from app.py as one event for the page."""
        if out.get("error"):
            self.add("error", text=out["error"])
            return
        reply = (out.get("reply") or "").strip()
        pend = out.get("pending")
        if pend:
            what = [p.get("label", "").lower() for p in pend]
            detail = "; ".join(json.dumps(p.get("input") or {}, ensure_ascii=False)[:150] for p in pend)
            self.add("ask", text=reply, what=what, detail="" if detail == "{}" else detail)
            return
        if out.get("question"):
            self.add("question", text=reply, card=out["question"])
            return
        if out.get("activity") and not reply:
            reply = "Done: " + ", ".join(out["activity"][:5])
        self.add("reply", text=reply or "👍")

    def _run(self):
        while True:
            job = self.work.get()
            if job is None:
                return
            try:
                self.add("working")
                job()
            except Exception as e:
                self.add("error", text=f"Something went wrong on the laptop: {e}")

    def ask(self, text):
        self.work.put(lambda: self.say(self.on_message(text) if self.on_message else {"reply": "?"}))

    def confirm(self, approved):
        self.work.put(lambda: self.say(self.on_confirm(approved) if self.on_confirm else {}))

    # ---------------------------------------------------------------- the web server
    def _build(self):
        app = Flask(__name__, static_folder=None)
        me = self

        def cross_site():
            origin = request.headers.get("Origin")
            if not origin:
                return False
            return urllib.parse.urlparse(origin).netloc.lower() != (request.headers.get("Host") or "").lower()

        def guarded(fn):
            @wraps(fn)
            def inner(*a, **k):
                if cross_site():
                    return jsonify(error="cross-site request refused"), 403
                if request.method == "POST" and not request.is_json:
                    return jsonify(error="JSON only"), 415
                return fn(*a, **k)
            return inner

        def authed(fn):
            @wraps(fn)
            @guarded
            def inner(*a, **k):
                auth = request.headers.get("Authorization", "")
                dev = me.device_for(auth[7:].strip() if auth.lower().startswith("bearer ") else "")
                if not dev:
                    return jsonify(error="not paired"), 401
                return fn(*a, **k)
            return inner

        def body():
            return request.get_json(silent=True) or {}

        @app.get("/")
        def index():
            return _page("index.html")

        @app.get("/<path:name>")
        def asset(name):
            if name.startswith("m/") or not re.fullmatch(r"[\w.-]+", name):
                return jsonify(error="not found"), 404
            return _page(name)

        def _page(name):
            r = send_from_directory(STATIC, name)
            r.headers["Cache-Control"] = "no-cache"
            r.headers["X-Content-Type-Options"] = "nosniff"
            return r

        @app.post("/m/pair")
        @guarded
        def pair():
            b = body()
            key, why = me.pair(b.get("code"), b.get("name"))
            if not key:
                return jsonify(error=why), 403
            return jsonify(key=key, last=me.next_id - 1)      # a new phone starts from now, not from other phones' talk

        @app.post("/m/chat")
        @authed
        def chat():
            text = str(body().get("text") or "").strip()[:MAX_TEXT]
            if not text:
                return jsonify(error="empty message"), 400
            me.ask(text)
            return jsonify(ok=True), 202

        @app.post("/m/confirm")
        @authed
        def confirm():
            me.confirm(body().get("approved") is True)
            return jsonify(ok=True), 202

        @app.post("/m/stop")
        @authed
        def stop():
            busy = me.on_stop() if me.on_stop else False
            me.add("reply", text="Okay, I stopped." if busy else "I wasn't doing anything 🙂")
            return jsonify(ok=True)

        @app.get("/m/events")
        @authed
        def events():
            try:
                after = int(request.args.get("after", 0))
                wait = max(0.0, min(float(request.args.get("wait", 0)), 25.0))
            except ValueError:
                return jsonify(error="bad number"), 400
            return jsonify(events=me.since(after, wait), last=me.next_id - 1)

        @app.get("/m/status")
        @authed
        def status():
            return jsonify(text=me.status_text() if me.status_text else "", **me.state())

        @app.post("/m/answer")
        @authed
        def answer():
            b = body()
            if not me.on_answer:
                return jsonify(error="not available"), 404
            qid, answers = str(b.get("id") or ""), b.get("answers") if isinstance(b.get("answers"), dict) else {}
            me.work.put(lambda: me.say(me.on_answer(qid, answers)))
            return jsonify(ok=True), 202

        @app.get("/m/tasks")
        @authed
        def tasks():
            return jsonify(rows=me.tasks())

        @app.post("/m/unpair")
        @authed
        def unpair():
            auth = request.headers.get("Authorization", "")
            dev = me.device_for(auth[7:].strip())
            me.remove(dev["id"])
            return jsonify(ok=True)

        return app

    def start(self):
        """Listen on 127.0.0.1 only. Safe to call twice."""
        from werkzeug.serving import make_server
        with self.lock:
            if self.server:
                return
            self.server = make_server(HOST, PORT, self.app, threaded=True)
            self.thread = runtime.spawn("mobile.serve_forever", self.server.serve_forever, start=False)
            self.thread.start()
            if not self.worker or not self.worker.is_alive():
                self.worker = runtime.spawn("mobile.run", self._run, start=False)
                self.worker.start()

    def stop(self):
        with self.lock:
            srv, self.server = self.server, None
        if srv:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass  # intentional: shutdown is best effort

    def running(self):
        return bool(self.server)
