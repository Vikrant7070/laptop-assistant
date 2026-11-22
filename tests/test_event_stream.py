"""One event stream (roadmap R9): the bus fans events out to every page, replays after Last-Event-ID, drops a slow
subscriber, and /api/stream sends it all as Server-Sent Events; /api/events no longer takes events from other pages."""
import json
import threading

from nova.actions import tools
from nova.audio import voice
from nova.core import app
from nova.core.app import background, eventbus, session, voice_io


def _client():
    return app.app.test_client()


def _sse_events(resp, n):
    """The first n events of a streamed /api/stream response, as (kind, id, data)."""
    out, buf = [], ""
    it = iter(resp.response)
    while len(out) < n:
        buf += next(it).decode() if isinstance(buf, str) else next(it)
        while "\n\n" in buf and len(out) < n:
            block, buf = buf.split("\n\n", 1)
            fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":"))
            if "event" in fields:
                out.append((fields["event"], int(fields["id"]) if "id" in fields else None, json.loads(fields["data"])))
    resp.close()
    return out


def test_two_subscribers_get_the_same_events():
    a, b = eventbus.bus.subscribe(), eventbus.bus.subscribe()
    eventbus.publish("thought", {"text": "one"})
    eventbus.publish("busy", {"busy": True})
    got_a = [a.get_nowait() for _ in range(2)]
    got_b = [b.get_nowait() for _ in range(2)]
    assert got_a == got_b and [e["kind"] for e in got_a] == ["thought", "busy"]
    assert got_a[1]["id"] == got_a[0]["id"] + 1


def test_replay_after_last_event_id():
    first = eventbus.publish("thought", {"text": "missed 0"})
    eventbus.publish("thought", {"text": "missed 1"})
    eventbus.publish("thought", {"text": "missed 2"})
    q = eventbus.bus.subscribe(first["id"])
    assert [q.get_nowait()["data"]["text"] for _ in range(2)] == ["missed 1", "missed 2"] and q.empty()
    assert eventbus.bus.subscribe().empty()                # no id: from now on only


def test_a_slow_subscriber_is_dropped_not_waited_for():
    bus = eventbus.Bus(max_queue=2)
    slow = bus.subscribe()
    for i in range(3):
        bus.publish("pulse", {"n": i})
    assert slow.dropped and bus.subscribers() == 0
    assert len(bus.since(0)) == 3                          # it reconnects and catches up from the log


def test_the_log_keeps_only_the_newest():
    bus = eventbus.Bus(keep=3)
    for i in range(5):
        bus.publish("pulse", {"n": i})
    assert [e["data"]["n"] for e in bus.since(0)] == [2, 3, 4] and bus.last_id == 5


def test_the_stream_sends_state_then_events_as_sse(monkeypatch):
    monkeypatch.setattr(eventbus, "HEARTBEAT", 0.05)
    start = eventbus.publish("pulse", {"n": 0})["id"]
    voice_io.push_event({"reply": "hello"})
    session.think("checking the weather")
    resp = _client().get("/api/stream", headers={"Last-Event-ID": str(start)}, buffered=False)
    assert resp.status_code == 200 and resp.mimetype == "text/event-stream"
    assert resp.headers["Cache-Control"] == "no-cache"
    got = _sse_events(resp, 3)
    assert got[0][0] == "state" and got[0][1] is None and {"speaking", "ears", "busy", "wake_phrase"} <= set(got[0][2])
    assert got[1][:2] == ("event", start + 1) and got[1][2] == {"reply": "hello"}
    assert got[2][0] == "thought" and got[2][2]["text"] == "checking the weather"


def test_a_closed_stream_lets_go_of_its_queue(monkeypatch):
    monkeypatch.setattr(eventbus, "HEARTBEAT", 0.05)
    resp = _client().get("/api/stream", buffered=False)
    _sse_events(resp, 1)
    assert eventbus.bus.subscribers() == 0


def test_after_a_restart_an_old_last_event_id_starts_fresh(monkeypatch):
    monkeypatch.setattr(eventbus, "HEARTBEAT", 0.05)
    voice_io.push_event({"reply": "since the restart"})
    resp = _client().get("/api/stream?backlog=event", headers={"Last-Event-ID": "9999"}, buffered=False)
    assert _sse_events(resp, 2)[1][2] == {"reply": "since the restart"}


def test_chat_events_wait_for_the_first_page_but_are_not_taken_from_others(monkeypatch):
    monkeypatch.setattr(eventbus, "HEARTBEAT", 0.05)
    voice_io.push_event({"reply": "while the chat was closed"})
    one = eventbus.bus.subscribe()                          # a page already open gets new events only
    resp = _client().get("/api/stream?backlog=event", buffered=False)
    assert _sse_events(resp, 2)[1][2] == {"reply": "while the chat was closed"}
    resp = _client().get("/api/stream?backlog=event", buffered=False)
    voice_io.push_event({"reply": "live"})
    assert _sse_events(resp, 2)[1][2] == {"reply": "live"}  # the backlog was shown once, not twice
    assert one.get_nowait()["data"] == {"reply": "live"}    # and the other page got it too


def test_the_polling_fallback_no_longer_clears_for_every_reader():
    c = _client()
    first = c.get("/api/events").get_json()
    voice_io.push_event({"reply": "for both pages"})
    a = c.get(f"/api/events?after={first['last']}").get_json()
    b = c.get(f"/api/events?after={first['last']}").get_json()
    assert a["events"] == b["events"] == [{"reply": "for both pages"}]
    assert c.get(f"/api/events?after={a['last']}").get_json()["events"] == []
    assert {"speaking", "ears", "heard", "wake_phrase"} <= set(a)


def test_thoughts_arrive_in_order_from_many_threads():
    q = eventbus.bus.subscribe()
    threads = [threading.Thread(target=lambda i=i: [session.think(f"t{i}-{k}") for k in range(20)]) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    got = [q.get_nowait() for _ in range(80)]
    assert [e["data"]["id"] for e in got] == sorted(e["data"]["id"] for e in got)
    assert [e["id"] for e in got] == sorted(e["id"] for e in got)


def test_the_session_lock_publishes_busy():
    q = eventbus.bus.subscribe()
    with session.lock:
        assert session.lock.locked()
    assert not session.lock.acquire(timeout=0) or session.lock.release() is None
    got = [q.get_nowait()["data"]["busy"] for _ in range(q.qsize())]
    assert got[:2] == [True, False]


def test_ear_commands_and_thinking_are_published():
    q = eventbus.bus.subscribe()
    voice_io.ear_listen()
    voice_io.ear_close()
    kinds = [(e["kind"], e["data"]) for e in (q.get_nowait() for _ in range(q.qsize()))]
    assert ("ear", {"do": "listen"}) in kinds and ("ear", {"do": "stop"}) in kinds
    assert len(voice_io.ear_cmds) <= 10


def test_her_lips_and_ears_are_published():
    q = eventbus.bus.subscribe()
    real = voice.RealMouth                                 # conftest swaps the real Mouth for a fake one
    m = real.__new__(real)
    m.lips, m.on_lips, m._tell = False, voice_io.mouth.on_lips, lambda *a, **k: None
    real._lips(m, True)
    real._lips(m, True)                                    # no change: nothing new
    real._lips(m, False)
    ears = voice.wake._EarsBase()
    ears.heard = []
    ears.status = "listening"
    ears._note("heard 'Hey Nova'")
    got = [(e["kind"], e["data"]) for e in (q.get_nowait() for _ in range(q.qsize()))]
    assert [k for k, _ in got if k.startswith("speak")] == ["speak_start", "speak_end"]
    ears_ev = [d for k, d in got if k == "ears"]
    assert ears_ev[-1]["status"] == "listening" and ears_ev[-1]["heard"][0].endswith("heard 'Hey Nova'")


def test_the_pulse_is_still_served_and_now_published(monkeypatch):
    monkeypatch.setattr(tools.home_of("stats"), "stats", lambda: {"cpu": 5, "ram": 40, "disk": 50, "battery": None,
                                                     "plugged": True, "uptime_h": 1})
    monkeypatch.setattr(tools.home_of("due_reminders"), "due_reminders", lambda: ["stretch"])
    j = _client().get("/api/pulse").get_json()
    assert j["stats"]["cpu"] == 5 and j["say"] == ["Hey! Reminder: stretch"]
    assert background.pulse_now()["say"] == ["Hey! Reminder: stretch"]
