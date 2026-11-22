"""Talk to Nova from your phone, through a private Telegram bot.

How it works:
  - The user makes a bot with Telegram's @BotFather and pastes its token into ⚙ Model -> Phone. The token is saved
    encrypted with Windows DPAPI (like the cloud-brain keys), in data/phone.json.
  - Nova shows a 6-digit pairing code. The first Telegram account that sends that code to the bot (in a private chat,
    within 10 minutes) becomes the ONLY account she listens to. Everyone else is ignored without a reply.
  - Messages are answered by the same brain as the chat window. Anything that would change things on the laptop asks
    first (Yes / No buttons in Telegram), whatever the autonomy setting - the user isn't watching the screen.
  - While the user is away from the laptop, reminders and finished background jobs are also sent to the phone.

No extra package: Telegram's Bot API is plain HTTPS + JSON (long polling, so nothing on the laptop listens on a port).
"""
import base64
import json
import os
import queue
import random
import re
import threading
import time
import urllib.error
import urllib.request

from nova import runtime
from nova import store

API = os.environ.get("NOVA_TELEGRAM_API", "https://api.telegram.org")   # tests point this at a fake server
POLL_SECS = 25           # long poll: Telegram holds the request open until a message arrives (or this many seconds)
PAIR_SECS = 600          # a pairing code works this long
MAX_TEXT = 3900          # Telegram's limit is 4096 characters per message
TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{30,50}$")
HELP = ("I'm Nova, on your laptop 💻 Just message me what you need, like \"what's my battery?\", \"is the research "
        "done?\" or \"remind me at 6 to call mom\".\n/status - laptop health\n/stop - stop what I'm doing\n"
        "/help - this message")


def _file():
    return store.current().phone


def _load():
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d):
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(f)


def _protect(text, on):
    from nova.brain import cloud       # its DPAPI helper: only this Windows account can decrypt
    if on:
        return base64.b64encode(cloud._dpapi(text.encode(), True)).decode()
    return cloud._dpapi(base64.b64decode(text), False).decode()


class PhoneError(Exception):
    pass


class Phone:
    """The Telegram link. on_message(text) -> Nova's answer dict; on_confirm(approved) -> answer dict;
    on_stop() -> bool; status_text() -> str. All set by app.py."""

    def __init__(self, on_message=None, on_confirm=None, on_stop=None, status_text=None, on_answer=None):
        self.on_message, self.on_confirm = on_message, on_confirm
        self.on_answer = on_answer           # on_answer(card id, {key: value}): a tap on a question card's button
        self.asked_card = None               # (message id, card id, key, values) of the last question card sent
        self.on_stop, self.status_text = on_stop, status_text
        self.note = ""                       # last problem, shown in ⚙ settings
        self.pair_code, self.pair_until = "", 0.0
        self._stop = threading.Event()
        self._thread = None
        self._work = queue.Queue()
        self._worker = None
        self.asked = 0                       # message id of the last Yes/No question, so old buttons can't answer a new one

    # ---------------------------------------------------------------- settings
    def token(self):
        enc = _load().get("token")
        if enc:
            try:
                return _protect(enc, False)
            except Exception:
                self.note = "The saved bot token can't be read on this Windows account. Paste it again."
        return os.environ.get("NOVA_TELEGRAM_TOKEN", "").strip()

    def info(self):
        d = _load()
        return {"configured": bool(self.token()), "bot": d.get("bot", ""), "paired": bool(d.get("chat_id")),
                "paired_name": d.get("paired_name", ""), "forward": d.get("forward", True),
                "pair_code": self.pair_code if (self.pair_code and time.time() < self.pair_until) else "",
                "running": self.running(), "note": self.note}

    def set_token(self, token):
        """Check the token with Telegram (getMe), then save it encrypted and start listening. Returns info()."""
        token = str(token or "").strip()
        if not token:
            self.stop()
            _save({})
            self.pair_code = ""
            return self.info()
        if not TOKEN_RE.match(token):
            raise PhoneError("That doesn't look like a bot token. It looks like 123456789:ABC... - copy it from "
                             "@BotFather's message.")
        me = self._call("getMe", token=token, http_timeout=15)
        d = {"token": _protect(token, True), "bot": me.get("username", ""), "forward": True}
        old = _load()
        if old.get("bot") == d["bot"]:          # same bot again: keep the paired account
            d.update({k: old[k] for k in ("chat_id", "user_id", "paired_name", "forward", "offset") if k in old})
        _save(d)
        self.note = ""
        if not d.get("chat_id"):
            self.new_pair_code()
        self.start()
        return self.info()

    def set_forward(self, on):
        d = _load()
        d["forward"] = bool(on)
        _save(d)

    def unpair(self):
        d = _load()
        for k in ("chat_id", "user_id", "paired_name"):
            d.pop(k, None)
        _save(d)
        self.new_pair_code()
        return self.info()

    def new_pair_code(self):
        self.pair_code = f"{random.SystemRandom().randint(0, 999999):06d}"
        self.pair_until = time.time() + PAIR_SECS
        return self.pair_code

    def paired(self):
        return bool(_load().get("chat_id"))

    # ---------------------------------------------------------------- Telegram HTTP
    def _call(self, method, token=None, http_timeout=20, **params):
        token = token or self.token()
        if not token:
            raise PhoneError("No bot token saved.")
        req = urllib.request.Request(f"{API}/bot{token}/{method}", data=json.dumps(params).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as r:
                body = json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                desc = json.loads(e.read()).get("description", "")
            except Exception:
                desc = ""
            if e.code in (401, 404):
                raise PhoneError("Telegram says this bot token is wrong (or the bot was deleted).") from e
            if e.code == 409:
                raise PhoneError("Another program is already reading this bot's messages. Use a bot only for "
                                 "Nova, or close the other program.") from e
            raise PhoneError(f"Telegram error {e.code}: {desc[:150]}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise PhoneError("I can't reach Telegram (no internet?).") from e
        if not body.get("ok"):
            raise PhoneError(f"Telegram error: {body.get('description', '?')[:150]}")
        return body.get("result")

    def send(self, text, buttons=None, rows=None):
        """Send text to the paired account. Returns the message id (or 0). buttons: one row; rows: several."""
        chat = _load().get("chat_id")
        if not chat or not text:
            return 0
        text = str(text)
        mid = 0
        while text:
            part, text = text[:MAX_TEXT], text[MAX_TEXT:]
            params = {"chat_id": chat, "text": part, "disable_web_page_preview": True}
            if (buttons or rows) and not text:
                params["reply_markup"] = {"inline_keyboard": rows or [buttons]}
            try:
                mid = (self._call("sendMessage", **params) or {}).get("message_id", 0)
            except PhoneError as e:
                self.note = str(e)
                return 0
        return mid

    def notify(self, text):
        """Something she'd say out loud, for a user who isn't at the laptop. Only when forwarding is on."""
        d = _load()
        if d.get("chat_id") and d.get("forward", True) and self.token():
            return self.send(text)
        return 0

    # ---------------------------------------------------------------- listening
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        if self.running() or not self.token():
            return
        self._stop.clear()
        self._thread = runtime.spawn("nova-phone", self._poll, start=False)
        self._thread.start()
        if not (self._worker and self._worker.is_alive()):
            self._worker = runtime.spawn("nova-phone-work", self._work_loop, start=False)
            self._worker.start()

    def stop(self):
        self._stop.set()

    def _poll(self):
        wait = 2
        while not self._stop.is_set():
            d = _load()
            try:
                updates = self._call("getUpdates", http_timeout=POLL_SECS + 10, offset=d.get("offset", 0),
                                     timeout=POLL_SECS, allowed_updates=["message", "callback_query"]) or []
                wait = 2
                self.note = ""
            except PhoneError as e:
                self.note = str(e)
                if "wrong" in str(e):                # a bad token won't fix itself
                    return
                self._stop.wait(wait)
                wait = min(60, wait * 2)
                continue
            for u in updates:
                d = _load()
                d["offset"] = u.get("update_id", 0) + 1
                _save(d)
                try:
                    self.handle(u)
                except Exception as e:                # one odd message must never stop her listening
                    self.note = f"Couldn't handle a message: {type(e).__name__}: {e}"

    def _work_loop(self):
        while True:
            job = self._work.get()
            try:
                job()
            except Exception as e:
                self.note = f"{type(e).__name__}: {e}"
                self.send(f"Sorry, something went wrong on the laptop: {e}")

    # ---------------------------------------------------------------- one update
    def handle(self, u):
        """One Telegram update. Returns what it did (for tests and the log)."""
        if "callback_query" in u:
            return self._button(u["callback_query"])
        m = u.get("message") or {}
        chat, sender = m.get("chat") or {}, m.get("from") or {}
        text = (m.get("text") or "").strip()
        if chat.get("type") != "private" or sender.get("is_bot"):
            return "ignored: not a private chat"
        d = _load()
        if not d.get("chat_id"):
            code = re.sub(r"^/start\s*", "", text).strip()
            if self.pair_code and time.time() < self.pair_until and code == self.pair_code:
                d.update(chat_id=chat["id"], user_id=sender.get("id"),
                         paired_name=(sender.get("first_name") or sender.get("username") or "")[:40])
                _save(d)
                self.pair_code = ""
                self.send(f"Paired! 🎉 I'm Nova, on your laptop. Only this Telegram account can talk to me.\n\n{HELP}")
                return "paired"
            return "ignored: not paired"
        if chat.get("id") != d["chat_id"] or sender.get("id") != d.get("user_id"):
            return "ignored: stranger"
        if not text:
            self.send("I can only read text messages for now 🙂")
            return "not text"
        low = text.lower()
        if low in ("/start", "/help"):
            self.send(HELP)
            return "help"
        if low == "/stop":
            busy = self.on_stop() if self.on_stop else False
            self.send("Okay, I stopped." if busy else "I wasn't doing anything 🙂")
            return "stop"
        if low == "/status":
            self.send(self.status_text() if self.status_text else "?")
            return "status"
        self._work.put(lambda: self._ask(text))
        return "queued"

    def _typing(self):
        try:
            self._call("sendChatAction", chat_id=_load().get("chat_id"), action="typing", http_timeout=5)
        except PhoneError:
            pass

    def _ask(self, text):
        self._typing()
        out = self.on_message(text) if self.on_message else {"reply": "?"}
        self.reply(out)

    def reply(self, out):
        """Send Nova's answer dict (reply / pending / error) back to the phone."""
        if out.get("error"):
            self.send("😵 " + out["error"])
            return
        reply = (out.get("reply") or "").strip()
        pend = out.get("pending")
        if pend:
            what = ", ".join(p["label"].lower() for p in pend)
            q = (reply + "\n\n" if reply else "") + f"🙋 May I {what}?"
            detail = "; ".join(json.dumps(p.get("input") or {}, ensure_ascii=False)[:150] for p in pend)
            if detail and detail != "{}":
                q += f"\n({detail})"
            self.asked = self.send(q, buttons=[{"text": "✅ Yes", "callback_data": "yes"},
                                               {"text": "❌ No", "callback_data": "no"}]) or -1
            return
        card = out.get("question")
        qs = (card or {}).get("questions") or []
        if len(qs) == 1 and qs[0]["kind"] in ("yesno", "one"):
            # A question card: a button per choice (one per row, labels can be long). Several / open answers are typed.
            q = qs[0]
            mid = self.send(reply or q["text"], rows=[[{"text": c["label"][:60], "callback_data": f"q:{i}"}]
                                                      for i, c in enumerate(q["choices"])])
            self.asked_card = (mid or -1, card["id"], q["key"], [c["value"] for c in q["choices"]])
            return
        if out.get("activity") and not reply:
            reply = "Done: " + ", ".join(out["activity"][:5])
        self.send(reply or "👍")

    def _button(self, q):
        d = _load()
        sender = q.get("from") or {}
        msg = q.get("message") or {}
        try:
            self._call("answerCallbackQuery", callback_query_id=q.get("id"), http_timeout=5)
        except PhoneError:
            pass
        if not d.get("chat_id") or sender.get("id") != d.get("user_id"):
            return "ignored: stranger"
        data = str(q.get("data") or "")
        if data.startswith("q:"):
            return self._card_button(msg, data)
        if msg.get("message_id") != self.asked:
            self.send("That question has expired - ask me again if you still want it.")
            return "expired"
        self.asked = 0
        approved = q.get("data") == "yes"
        self._work.put(lambda: (self._typing(), self.reply(self.on_confirm(approved) if self.on_confirm else {})))
        return "yes" if approved else "no"

    def _card_button(self, msg, data):
        """A tap on a question card's button: only the newest card's buttons count."""
        asked = self.asked_card
        try:
            i = int(data[2:])
        except ValueError:
            return "ignored: bad button"
        if not asked or msg.get("message_id") != asked[0] or not 0 <= i < len(asked[3]):
            self.send("That question has expired - ask me again if you still want it.")
            return "expired"
        self.asked_card = None
        mid, card_id, key, values = asked
        self._work.put(lambda: (self._typing(), self.reply(self.on_answer(card_id, {key: values[i]})
                                                          if self.on_answer else {})))
        return f"answered {values[i]}"
