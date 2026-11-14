"""The Stop switch: ends a task that's stuck or looping, at once, and leaves the history usable."""
import threading
import time

import app


def run_in_background(text):
    box = {}
    t = threading.Thread(target=lambda: box.update(res=app.answer(text)), daemon=True)
    t.start()
    return t, box


def wait_for(cond, secs=3):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_stop_while_brain_is_stuck_thinking(brain, monkeypatch):
    brain([])
    release = threading.Event()

    def stuck_brain(*a, **k):        # a brain call that never comes back
        release.wait(10)
        return {"role": "assistant", "content": "late"}, "late", []
    monkeypatch.setattr(app.llm, "chat", stuck_brain)
    t, box = run_in_background("do something")
    assert wait_for(app.lock.locked)
    time.sleep(0.2)
    assert app.stop_now() is True
    t.join(2)
    assert not t.is_alive(), "Stop should not wait for the brain"
    out, status = box["res"]
    assert status == 200 and out["stopped"] and out["reply"] == app.STOPPED_REPLY
    release.set()


def test_stop_ends_a_tool_loop(brain, monkeypatch):
    # A brain that keeps asking for the same step forever.
    fake, ran = brain([("", [("get_datetime", {})])] * 200)
    calls = []

    def slow_tool(n, a):
        calls.append(n)
        if len(calls) == 3:
            app.stop_now()
        return "ok"
    monkeypatch.setattr(app.tools, "run_tool", slow_tool)
    out, status = app.answer("loop please")
    assert status == 200 and out["stopped"]
    assert len(calls) == 3
    # every tool call in the history has its result, and the next request works normally
    brain([("Hello again!", [])])
    out, _ = app.answer("hi")
    assert out["reply"] == "Hello again!" and not out.get("stopped")


def test_stop_skips_remaining_calls_in_a_batch(brain, monkeypatch):
    fake, ran = brain([("", [("get_datetime", {}), ("network_info", {}), ("get_system_status", {})])])

    def tool(n, a):
        ran.append(n)
        app.stop_now()
        return "ok"
    monkeypatch.setattr(app.tools, "run_tool", tool)
    out, _ = app.answer("check everything")
    assert ran == ["get_datetime"] and out["stopped"]
    results = [m for m in app.state["messages"] if m["role"] == "tool" or "pressed Stop" in str(m.get("content", ""))]
    assert sum("pressed Stop" in str(m.get("content", "")) for m in results) == 2


def test_stop_when_idle_does_not_cancel_the_next_request(brain):
    assert app.stop_now() is False
    brain([("Sure!", [])])
    out, _ = app.answer("hi")
    assert out["reply"] == "Sure!"


def test_stop_endpoint(client):
    j = client.post("/api/stop", json={}).get_json()
    assert j["ok"] is True and j["was_busy"] is False
