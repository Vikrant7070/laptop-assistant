"""Nova on the iPhone (nova/core/mobile.py): pairing, device keys, cross-site refusal, answers as events, approvals."""
import json
import threading
import time

import pytest

from nova.core import app, mobile as mobile_mod


@pytest.fixture
def m():
    f = mobile_mod._file()
    if f.exists():
        f.unlink()
    got = {"text": [], "confirm": [], "stop": 0}

    def on_message(text):
        got["text"].append(text)
        return {"reply": "Hello from the laptop"}

    mob = mobile_mod.Mobile(on_message=on_message, on_confirm=lambda ok: got["confirm"].append(ok) or {"reply": "ok"},
                            on_stop=lambda: got.__setitem__("stop", got["stop"] + 1) or True,
                            status_text=lambda: "💻 Laptop: CPU 5%\n🔒 Screen is locked.", state=lambda: {"asleep": False, "busy": False})
    mob.got = got
    mob.worker = threading.Thread(target=mob._run, daemon=True)
    mob.worker.start()
    yield mob
    mob.work.put(None)
    if f.exists():
        f.unlink()


def paired(m):
    m.new_pair_code()
    key, why = m.pair(m.pair_code, "Vikrant's iPhone")
    assert key and not why
    return key, {"Authorization": "Bearer " + key}


def wait_for(m, kind, after=0, secs=3):
    end = time.time() + secs
    while time.time() < end:
        hit = [e for e in m.since(after) if e["kind"] == kind]
        if hit:
            return hit[-1]
        time.sleep(0.02)
    raise AssertionError(f"no {kind} event")


# ---------------------------------------------------------------- nothing works without a key

@pytest.mark.parametrize("method,path", [("post", "/m/chat"), ("post", "/m/confirm"), ("post", "/m/stop"),
                                         ("get", "/m/events"), ("get", "/m/status"), ("post", "/m/unpair")])
def test_every_call_needs_a_device_key(m, method, path):
    c = m.app.test_client()
    r = getattr(c, method)(path, json={"text": "hi"}) if method == "post" else c.get(path)
    assert r.status_code == 401
    bad = {"Authorization": "Bearer not-a-real-key"}
    r = getattr(c, method)(path, json={"text": "hi"}, headers=bad) if method == "post" else c.get(path, headers=bad)
    assert r.status_code == 401
    assert m.got["text"] == [] and m.got["confirm"] == []


def test_coming_from_localhost_proves_nothing(m):
    # Tailscale's proxy connects from 127.0.0.1: that must not count as being the user
    r = m.app.test_client().post("/m/chat", json={"text": "hi"}, environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 401


# ---------------------------------------------------------------- pairing

def test_pairing_with_the_right_code_gives_a_key_stored_only_as_a_hash(m):
    c = m.app.test_client()
    code = m.new_pair_code()
    r = c.post("/m/pair", json={"code": code, "name": "Vikrant's iPhone"})
    assert r.status_code == 200
    key = r.get_json()["key"]
    raw = mobile_mod._file().read_text(encoding="utf-8")
    assert key not in raw and "Vikrant's iPhone" in raw
    assert c.get("/m/status", headers={"Authorization": "Bearer " + key}).status_code == 200
    assert c.post("/m/pair", json={"code": code}).status_code == 403       # a code works once


def test_no_open_code_or_an_expired_one_pairs_nothing(m, monkeypatch):
    c = m.app.test_client()
    assert c.post("/m/pair", json={"code": "123456"}).status_code == 403
    code = m.new_pair_code()
    monkeypatch.setattr(mobile_mod.time, "time", lambda: m.pair_until + 1)
    assert c.post("/m/pair", json={"code": code}).status_code == 403
    assert m.devices() == []


def test_five_wrong_codes_lock_pairing_even_for_the_right_one(m):
    c = m.app.test_client()
    code = m.new_pair_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert c.post("/m/pair", json={"code": wrong}).status_code == 403
    r = c.post("/m/pair", json={"code": code})
    assert r.status_code == 403 and "Too many wrong codes" in r.get_json()["error"]
    assert m.devices() == []
    m.locked_until = 0
    code = m.new_pair_code()
    assert c.post("/m/pair", json={"code": code}).status_code == 200


def test_devices_are_listed_and_can_be_removed(m):
    key, hdr = paired(m)
    dev = m.devices()
    assert len(dev) == 1 and dev[0]["name"] == "Vikrant's iPhone" and "hash" not in dev[0]
    c = m.app.test_client()
    assert c.get("/m/status", headers=hdr).status_code == 200
    assert m.remove(dev[0]["id"]) and not m.remove("nope")
    assert c.get("/m/status", headers=hdr).status_code == 401


def test_a_device_can_unpair_itself(m):
    key, hdr = paired(m)
    c = m.app.test_client()
    assert c.post("/m/unpair", json={}, headers=hdr).status_code == 200
    assert m.devices() == [] and c.get("/m/status", headers=hdr).status_code == 401


# ---------------------------------------------------------------- web safety

def test_other_websites_cannot_use_it(m):
    key, hdr = paired(m)
    c = m.app.test_client()
    evil = {**hdr, "Origin": "https://evil.example"}
    assert c.post("/m/chat", json={"text": "x"}, headers=evil).status_code == 403
    assert c.post("/m/pair", json={"code": m.new_pair_code()}, headers={"Origin": "https://evil.example"}).status_code == 403
    same = {**hdr, "Origin": "http://laptop.tail1234.ts.net", "Host": "laptop.tail1234.ts.net"}
    assert c.post("/m/chat", json={"text": "x"}, headers=same).status_code == 202
    plain = c.post("/m/chat", data="text=hi", headers={**hdr, "Content-Type": "text/plain"})
    assert plain.status_code == 415                                        # no "simple" cross-site form posts
    assert "Access-Control-Allow-Origin" not in c.get("/m/status", headers=hdr).headers


def test_the_page_is_served_and_nothing_else(m):
    c = m.app.test_client()
    r = c.get("/")
    assert r.status_code == 200 and b"Nova" in r.data and r.headers["X-Content-Type-Options"] == "nosniff"
    for name in ("manifest.webmanifest", "sw.js", "icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        assert c.get("/" + name).status_code == 200, name
    assert c.get("/../app.py").status_code in (400, 404)
    assert c.get("/m/nothing").status_code == 404
    assert c.get("/devices.json").status_code == 404 and c.get("/gmail.json").status_code == 404


def test_only_loopback_is_ever_listened_on(m, monkeypatch):
    seen = []
    import werkzeug.serving as ws

    class Fake:
        def serve_forever(self):
            pass

        def shutdown(self):
            pass

        def server_close(self):
            pass
    monkeypatch.setattr(ws, "make_server", lambda host, port, app_, threaded=True: seen.append((host, port)) or Fake())
    m.start()
    m.start()                                                            # twice is safe
    assert seen == [("127.0.0.1", 5057)] and mobile_mod.HOST == "127.0.0.1"
    m.stop()
    assert not m.running()


# ---------------------------------------------------------------- the conversation

def test_a_message_is_answered_through_events(m):
    key, hdr = paired(m)
    c = m.app.test_client()
    assert c.post("/m/chat", json={"text": "  what's my battery  "}, headers=hdr).status_code == 202
    e = wait_for(m, "reply")
    assert e["text"] == "Hello from the laptop" and m.got["text"] == ["what's my battery"]
    r = c.get("/m/events?after=0", headers=hdr).get_json()
    assert [x["kind"] for x in r["events"]][:2] == ["working", "reply"] and r["last"] == e["id"]
    assert c.get(f"/m/events?after={e['id']}", headers=hdr).get_json()["events"] == []
    assert c.post("/m/chat", json={"text": "   "}, headers=hdr).status_code == 400


def test_events_wait_for_news(m):
    key, hdr = paired(m)
    c = m.app.test_client()
    last = c.get("/m/events", headers=hdr).get_json()["last"]
    threading.Timer(0.3, lambda: m.add("reply", text="late news")).start()
    t0 = time.time()
    r = c.get(f"/m/events?after={last}&wait=5", headers=hdr).get_json()
    assert r["events"][0]["text"] == "late news" and 0.2 < time.time() - t0 < 3
    t0 = time.time()
    assert c.get(f"/m/events?after={r['last']}&wait=0.4", headers=hdr).get_json()["events"] == []
    assert time.time() - t0 >= 0.35
    assert c.get("/m/events?after=abc", headers=hdr).status_code == 400


def test_a_question_comes_as_an_ask_event_and_the_answer_goes_back(m):
    key, hdr = paired(m)
    c = m.app.test_client()
    m.on_message = lambda text: {"reply": "Typing it.", "pending": [{"label": "Type text", "input": {"text": "hello"}}]}
    c.post("/m/chat", json={"text": "type hello"}, headers=hdr)
    e = wait_for(m, "ask")
    assert e["what"] == ["type text"] and "hello" in e["detail"] and e["text"] == "Typing it."
    assert c.post("/m/confirm", json={"approved": True}, headers=hdr).status_code == 202
    assert wait_for(m, "reply", after=e["id"])["text"] == "ok"
    assert m.got["confirm"] == [True]
    c.post("/m/confirm", json={"approved": "yes please"}, headers=hdr)      # only a real true counts
    end = time.time() + 2
    while len(m.got["confirm"]) < 2 and time.time() < end:
        time.sleep(0.02)
    assert m.got["confirm"] == [True, False]


def test_errors_and_activity_only_answers(m):
    key, hdr = paired(m)
    c = m.app.test_client()
    m.on_message = lambda text: {"error": "The brain isn't running"}
    c.post("/m/chat", json={"text": "x"}, headers=hdr)
    assert wait_for(m, "error")["text"] == "The brain isn't running"
    m.on_message = lambda text: {"reply": "", "activity": ["Opened Notepad"]}
    c.post("/m/chat", json={"text": "y"}, headers=hdr)
    assert wait_for(m, "reply")["text"] == "Done: Opened Notepad"
    m.on_message = lambda text: (_ for _ in ()).throw(RuntimeError("boom"))
    c.post("/m/chat", json={"text": "z"}, headers=hdr)
    assert "boom" in wait_for(m, "error", after=m.next_id - 3)["text"]


def test_stop_and_status(m):
    key, hdr = paired(m)
    c = m.app.test_client()
    assert c.post("/m/stop", json={}, headers=hdr).status_code == 200
    assert m.got["stop"] == 1 and wait_for(m, "reply")["text"] == "Okay, I stopped."
    s = c.get("/m/status", headers=hdr).get_json()
    assert "CPU 5%" in s["text"] and s["asleep"] is False and s["busy"] is False


def test_only_the_last_50_events_are_kept(m):
    for i in range(80):
        m.add("reply", text=str(i))
    assert len(m.since(0)) == 50 and m.since(0)[0]["text"] == "30"


# ---------------------------------------------------------------- through Nova's brain

def test_phone_app_requests_ask_first_even_in_full_access(brain, monkeypatch):
    app.save_settings({**app.settings(), "autonomy": "full"})
    monkeypatch.setattr(app.tools.windows, "_windows", lambda: [(1, "Untitled - Notepad", "Notepad.exe")])
    fake, ran = brain([("Typing it.", [("type_text", {"text": "hello"})]), ("Typed hello!", [])])
    out = app.mobile_message("type hello in notepad")
    assert out.get("pending") and ran == []                                # asked, didn't type
    assert app.mouth.said == []                                            # nothing said out loud on the empty laptop
    assert "[from the user's phone app" in fake.seen[0][-1]["content"]
    out2 = app.mobile_confirm(True)
    assert ran == [("type_text", {"text": "hello"})] and out2["reply"] == "Typed hello!"


def test_the_phone_tag_is_not_taken_for_a_screen_request(brain):
    assert app._unspoken(f"{app.MOBILE_TAG} how are you") == "how are you"


# ---------------------------------------------------------------- settings

def test_settings_switch_pairing_and_removal(client, monkeypatch):
    started = []
    monkeypatch.setattr(app.mobile, "start", lambda: started.append("start"))
    monkeypatch.setattr(app.mobile, "stop", lambda: started.append("stop"))
    monkeypatch.setattr(app.integrations, "tailscale_url", lambda: "https://laptop.tail1234.ts.net")
    r = client.post("/api/mobile", json={"new_code": True})
    assert r.status_code == 400                                            # not switched on yet
    j = client.post("/api/mobile", json={"on": True}).get_json()
    assert app.settings()["mobile"] is True and started == ["start"] and j["url"].endswith("ts.net")
    monkeypatch.setattr(app.mobile, "running", lambda: True)
    code = client.post("/api/mobile", json={"new_code": True}).get_json()["code"]
    assert len(code) == 6 and code.isdigit()
    key, why = app.mobile.pair(code, "Test phone")
    assert key
    dev = client.get("/api/mobile").get_json()["devices"]
    assert len(dev) == 1 and "hash" not in json.dumps(dev)
    assert client.post("/api/mobile", json={"remove": dev[0]["id"]}).get_json()["devices"] == []
    client.post("/api/mobile", json={"on": False})
    assert started == ["start", "stop"] and app.settings()["mobile"] is False
    f = mobile_mod._file()
    if f.exists():
        f.unlink()


def test_a_new_phone_does_not_see_what_other_phones_said(m):
    m.add("reply", text="private answer to another phone")
    c = m.app.test_client()
    j = c.post("/m/pair", json={"code": m.new_pair_code()}).get_json()
    assert j["last"] == m.next_id - 1 and j["last"] >= 1
    assert c.get(f"/m/events?after={j[chr(108) + chr(97) + chr(115) + chr(116)]}", headers={"Authorization": "Bearer " + j["key"]}).get_json()["events"] == []
