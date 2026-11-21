"""📱 Phone remote (Telegram): pairing, strangers ignored, answers sent back, Yes/No buttons, forwarding while away.

A fake Telegram Bot API runs on 127.0.0.1, so nothing reaches the internet."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nova.actions import phone as phone_mod
from nova.core import app

TOKEN = "123456789:AAH" + "x" * 32
ME, STRANGER = 1111, 2222


class FakeTelegram:
    def __init__(self):
        self.calls = []                 # (method, params)
        self.updates = []
        self.next_mid = 100
        self.status = 200
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                params = json.loads(self.rfile.read(n) or b"{}")
                token, method = self.path.split("/")[1][3:], self.path.split("/")[2]
                fake.calls.append((method, params))
                if fake.status != 200 or token != TOKEN:
                    code = fake.status if fake.status != 200 else 401
                    return self._send(code, {"ok": False, "error_code": code, "description": "Unauthorized"})
                if method == "getMe":
                    return self._send(200, {"ok": True, "result": {"id": 9, "is_bot": True, "username": "nova_test_bot"}})
                if method == "getUpdates":
                    off = params.get("offset", 0)
                    end = time.time() + min(1.0, params.get("timeout", 0))
                    while time.time() < end and not [u for u in fake.updates if u["update_id"] >= off]:
                        time.sleep(0.02)
                    return self._send(200, {"ok": True, "result": [u for u in fake.updates if u["update_id"] >= off]})
                if method == "sendMessage":
                    fake.next_mid += 1
                    return self._send(200, {"ok": True, "result": {"message_id": fake.next_mid}})
                return self._send(200, {"ok": True, "result": True})

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def sent(self):
        return [p for m, p in self.calls if m == "sendMessage"]


@pytest.fixture
def tg(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(phone_mod, "API", fake.url)
    yield fake
    fake.server.shutdown()


def msg(text, uid=ME, chat_type="private", upd=1):
    return {"update_id": upd, "message": {"message_id": upd, "text": text, "chat": {"id": uid, "type": chat_type},
                                          "from": {"id": uid, "is_bot": False, "first_name": "Vik"}}}


def paired_phone(tg, **hooks):
    p = phone_mod.Phone(**hooks)
    p.set_token(TOKEN)
    p.stop()
    assert p.handle(msg(p.pair_code)) == "paired"
    tg.calls.clear()
    return p


def test_token_is_checked_and_saved_encrypted(tg, isolated):
    p = phone_mod.Phone()
    with pytest.raises(phone_mod.PhoneError, match="doesn't look like"):
        p.set_token("hello")
    with pytest.raises(phone_mod.PhoneError, match="wrong"):
        p.set_token("123456789:AAH" + "y" * 32)          # the fake server only knows TOKEN
    info = p.set_token(TOKEN)
    p.stop()
    assert info["configured"] and info["bot"] == "nova_test_bot" and not info["paired"]
    assert len(info["pair_code"]) == 6 and info["pair_code"].isdigit()
    saved = (isolated / "phone.json").read_text()
    assert TOKEN not in saved and TOKEN.split(":")[1] not in saved     # DPAPI-encrypted, not plain text
    assert p.token() == TOKEN


def test_pairing_needs_the_code_and_ignores_everyone_else(tg):
    p = phone_mod.Phone()
    p.set_token(TOKEN)
    p.stop()
    code = p.pair_code
    assert p.handle(msg("hi there")) == "ignored: not paired"
    assert p.handle(msg(code, chat_type="group")) == "ignored: not a private chat"
    assert tg.sent() == []                                         # strangers get no reply at all
    assert p.handle(msg(f"/start {code}")) == "paired"
    assert p.info()["paired"] and p.info()["paired_name"] == "Vik" and p.pair_code == ""
    assert "Paired" in tg.sent()[-1]["text"]
    n = len(tg.sent())
    assert p.handle(msg("what's my battery?", uid=STRANGER)) == "ignored: stranger"
    assert p.handle(msg(code, uid=STRANGER)) == "ignored: stranger"   # the old code can't pair a second account
    assert len(tg.sent()) == n


def test_expired_code_doesnt_pair(tg):
    p = phone_mod.Phone()
    p.set_token(TOKEN)
    p.stop()
    p.pair_until = time.time() - 1
    assert p.handle(msg(p.pair_code)) == "ignored: not paired"


def test_message_is_answered_and_commands_work(tg):
    asked = []
    p = paired_phone(tg, on_message=lambda t: (asked.append(t), {"reply": "Battery is 80%.", "activity": []})[1],
                     on_stop=lambda: True, status_text=lambda: "💻 Laptop: CPU 5%")
    assert p.handle(msg("battery?", upd=2)) == "queued"
    end = time.time() + 5                               # the worker thread answers it
    while time.time() < end and not any(s["text"] == "Battery is 80%." for s in tg.sent()):
        time.sleep(0.02)
    assert asked == ["battery?"]
    assert tg.sent()[-1]["text"] == "Battery is 80%." and tg.sent()[-1]["chat_id"] == ME
    assert any(m == "sendChatAction" for m, _ in tg.calls)
    p.handle(msg("/status", upd=3))
    assert tg.sent()[-1]["text"].startswith("💻")
    p.handle(msg("/stop", upd=4))
    assert tg.sent()[-1]["text"] == "Okay, I stopped."
    p.handle(msg("/help", upd=5))
    assert "/status" in tg.sent()[-1]["text"]


def test_long_answers_are_split(tg):
    p = paired_phone(tg)
    p.send("x" * 9000)
    parts = [s["text"] for s in tg.sent()]
    assert len(parts) == 3 and "".join(parts) == "x" * 9000


def test_whole_loop_polls_answers_and_moves_offset(tg):
    got = threading.Event()
    p = paired_phone(tg, on_message=lambda t: (got.set(), {"reply": f"echo {t}"})[1])
    tg.updates = [msg("ping", upd=7)]
    p.start()
    try:
        assert got.wait(5)
        end = time.time() + 5
        while time.time() < end and not any(s["text"] == "echo ping" for s in tg.sent()):
            time.sleep(0.05)
        assert any(s["text"] == "echo ping" for s in tg.sent())
        assert phone_mod._load()["offset"] == 8           # the same message is never answered twice
    finally:
        p.stop()


# ------------------------------------------------------------------ through Nova's brain

def wire(p):
    p.on_message, p.on_confirm = app.phone_message, app.phone_confirm


def test_phone_request_asks_first_even_in_full_access(tg, brain, monkeypatch):
    app.save_settings({**app.settings(), "autonomy": "full"})
    p = paired_phone(tg)
    wire(p)
    monkeypatch.setattr(app, "phone", p)
    monkeypatch.setattr(app.tools, "_windows", lambda: [(1, "Untitled - Notepad", "Notepad.exe")])
    fake, ran = brain([("Typing it.", [("type_text", {"text": "hello"})]), ("Typed hello!", [])])
    out = app.phone_message("type hello in notepad")
    assert out.get("pending") and ran == []                    # asked, didn't type
    p.reply(out)
    q = tg.sent()[-1]
    assert "May I" in q["text"] and q["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "yes"
    assert app.mouth.said == []                                # nothing said out loud on the empty laptop
    assert "[from the user's phone" in fake.seen[0][-1]["content"]

    # a stranger pressing the button does nothing
    assert p._button({"id": "a", "from": {"id": STRANGER}, "data": "yes",
                      "message": {"message_id": p.asked}}) == "ignored: stranger"
    assert ran == []
    out2 = app.phone_confirm(True)                             # what the Yes button runs
    assert ran == [("type_text", {"text": "hello"})] and out2["reply"] == "Typed hello!"
    assert app.mouth.said == []


def test_old_button_is_refused(tg):
    p = paired_phone(tg, on_confirm=lambda ok: {"reply": "done"})
    p.asked = 555
    assert p._button({"id": "a", "from": {"id": ME}, "data": "yes", "message": {"message_id": 554}}) == "expired"


def test_laptop_requests_still_follow_autonomy(brain):
    app.save_settings({**app.settings(), "autonomy": "full"})
    fake, ran = brain([("ok", [("type_text", {"text": "hi"})]), ("Typed.", [])])
    out, _ = app.answer("type hi")
    assert not out.get("pending") and ran == [("type_text", {"text": "hi"})]
    assert app.state["remote"] is False


def test_phone_yes_no_text_answers_the_question(tg, brain):
    paired_phone(tg)
    fake, ran = brain([("ok", [("press_keys", {"keys": "ctrl+s"})]), ("Saved.", [])])
    out = app.phone_message("save my file")
    assert out.get("pending")
    out = app.phone_message("yes")
    assert ran == [("press_keys", {"keys": "ctrl+s"})] and out["reply"] == "Saved."


def test_reminders_go_to_phone_only_when_away(tg, monkeypatch):
    p = paired_phone(tg)
    monkeypatch.setattr(app, "phone", p)
    monkeypatch.setattr(app, "user_idle_secs", lambda: 5)
    app.tell_away("Reminder: call mom")
    assert tg.sent() == []
    monkeypatch.setattr(app, "user_idle_secs", lambda: 900)
    app.tell_away("Reminder: call mom")
    assert tg.sent()[-1]["text"] == "🔔 Reminder: call mom"
    p.set_forward(False)
    app.tell_away("Reminder: again")
    assert len(tg.sent()) == 1


def test_phone_api_routes(tg, client):
    r = client.get("/api/phone")
    assert r.status_code == 200 and r.get_json()["configured"] is False
    r = client.post("/api/phone", json={"token": "nope"})
    assert r.status_code == 400 and "doesn't look like" in r.get_json()["error"]
    r = client.post("/api/phone", json={"token": TOKEN})
    j = r.get_json()
    app.phone.stop()
    assert r.status_code == 200 and j["bot"] == "nova_test_bot" and j["pair_code"]
    r = client.post("/api/phone", json={"token": ""})
    assert r.get_json()["configured"] is False
