"""She stays in the conversation until told to sleep; 5 minutes without a task -> she naps in the bottom-right corner."""
import time

import pytest

import app


@pytest.fixture
def body(monkeypatch):
    sent = []
    monkeypatch.setattr(app.avatar_client, "send", lambda do, timeout=20, **a: sent.append((do, a)))
    return sent


@pytest.fixture
def listening(monkeypatch):
    started = []
    monkeypatch.setattr(app, "silent_ok", lambda: True)
    monkeypatch.setattr(app, "silent_loop", lambda: started.append(1))
    return started


def sleeps(sent):
    return [a["on"] for do, a in sent if do == "sleep"]


def test_hey_nova_starts_a_conversation(listening, body):
    app.on_wake()
    assert app.convo["on"] and listening


def test_one_breath_request_starts_a_conversation(listening, body, brain, monkeypatch):
    brain([("Done!", [])])
    app.on_request("open notepad")
    assert app.convo["on"]


def test_conversation_ends_only_when_told(listening, body, brain):
    app.on_wake()
    brain([("Sure!", [])] * 3)
    for t in ("what time is it", "tell me a joke", "thanks"):
        app.heard(t, via="silent")
        assert app.convo["on"]
    app.heard("Nova, go to sleep", via="silent")
    assert not app.convo["on"] and sleeps(body) == [True] and app.nap["asleep"]
    assert any("nap" in s for s in app.mouth.said)


def test_five_idle_minutes_means_nap(body):
    assert not app.nap_due()
    app.nap["last_task"] = time.time() - app.IDLE_NAP + 20
    assert not app.nap_due()
    app.nap["last_task"] = time.time() - app.IDLE_NAP - 1
    assert app.nap_due()
    app.go_to_sleep("idle")
    assert app.nap["asleep"] and sleeps(body) == [True]
    assert not app.nap_due()                 # already asleep


def test_no_nap_while_she_is_busy(body, monkeypatch):
    app.nap["last_task"] = time.time() - app.IDLE_NAP - 1
    with app.lock:
        assert not app.nap_due()
    monkeypatch.setattr(app.mouth, "busy", lambda: True)
    assert not app.nap_due()


def test_idle_nap_in_conversation_says_so(listening, body):
    app.on_wake()
    app.go_to_sleep("idle")
    assert not app.convo["on"]
    assert any("quiet for a while" in s for s in app.mouth.said)


def test_idle_nap_without_conversation_is_silent(body):
    app.go_to_sleep("idle")
    assert app.mouth.said == [] and app.nap["asleep"]


def test_a_typed_request_wakes_her(body, brain):
    app.go_to_sleep("idle")
    brain([("Hi!", [])])
    app.answer("hello")
    assert sleeps(body) == [True, False] and not app.nap["asleep"]
    assert time.time() - app.nap["last_task"] < 5


def test_wake_word_wakes_her(listening, body):
    app.go_to_sleep("asked")
    app.on_wake()
    assert sleeps(body)[-1] is False and app.convo["on"]


def test_clicking_her_wakes_her(body, client):
    app.go_to_sleep("idle")
    client.post("/api/show_window", json={})
    assert sleeps(body)[-1] is False


def test_typed_go_to_sleep(body, client):
    j = client.post("/api/chat", json={"text": "go to sleep"}).get_json()
    assert "nap" in j["reply"] and app.nap["asleep"]


def test_long_message_mentioning_sleep_is_a_normal_request(body, client, brain):
    brain([("Try a warm drink!", [])])
    j = client.post("/api/chat", json={"text": "I can't go to sleep at night, any tips for falling asleep faster?"}).get_json()
    assert j["reply"] == "Try a warm drink!" and not app.nap["asleep"]


def test_silent_loop_keeps_listening_through_silence(monkeypatch, body):
    """Silence in a conversation doesn't end it before 5 minutes; after that she naps."""
    app.convo.update(on=True, last_heard=time.time())
    calls = {"n": 0}

    class Ears:
        failures = 0

        def listen(self):
            calls["n"] += 1
            if calls["n"] == 3:      # pretend 5 minutes went by
                app.nap["last_task"] = app.convo["last_heard"] = time.time() - app.IDLE_NAP - 1
            return "silence", ""
    monkeypatch.setattr(app, "transcriber", Ears())
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    app.silent_loop()
    assert calls["n"] == 3 and not app.convo["on"] and app.nap["asleep"]


def test_status_reports_sleep(client):
    app.go_to_sleep("idle")
    assert client.get("/api/status").get_json()["asleep"] is True


# ---------------------------------------------------------------- "go to sleep", even when the ears get it wrong

@pytest.mark.parametrize("said", [
    "Nova, go to sleep.", "Noah got to sleep.", "go too sleep", "Nova go 2 sleep", "You can sleep now.",
    "Nova, sleep.", "Take a nap.", "Go to bed, Nova.", "Stop listening.", "Good night!", "Bye Nova.", "That's all.",
    "[spoken; speech recognition may have misheard words] Noah got to sleep.",
])
def test_sleep_phrases_and_mishearings(said):
    assert app.wants_sleep(said), said


@pytest.mark.parametrize("said", [
    "I can't go to sleep at night, any tips for falling asleep faster?",
    "what time should I go to sleep if I wake up at six tomorrow",
    "open notepad", "how much battery do I have", "play some sleep music on youtube",
])
def test_normal_requests_are_not_sleep(said):
    assert not app.wants_sleep(said), said


def test_misheard_sleep_by_voice_ends_the_conversation(listening, body):
    app.on_wake()
    app.heard("Noah got to sleep.", via="silent")
    assert not app.convo["on"] and app.nap["asleep"]


def test_brain_understood_sleep_so_she_really_sleeps(listening, body, brain):
    """The words didn't match, but her brain got it and called go_to_sleep: she says goodnight once, then sleeps."""
    app.on_wake()
    brain([("", [("go_to_sleep", {})]), ("Good night, boss!", [])])
    app.heard("okay I'm done for today, you rest too", via="silent")
    assert not app.convo["on"] and app.nap["asleep"] and sleeps(body)[-1] is True
    assert app.mouth.said.count("Good night, boss!") == 1
    assert not any("nap in the corner" in s for s in app.mouth.said)      # no second goodnight
    assert "sleep_after" not in app.state


def test_silent_loop_stops_after_sleep(monkeypatch, body):
    """After 'go to sleep' the conversation loop must stop listening at once."""
    app.convo.update(on=True, last_heard=time.time())
    heard = []

    class Ears:
        failures = 0

        def listen(self):
            heard.append(1)
            return ("text", "Noah got to sleep.") if len(heard) == 1 else ("text", "No way.")
    monkeypatch.setattr(app, "transcriber", Ears())
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    app.silent_loop()
    assert heard == [1] and not app.convo["on"]


def test_go_to_sleep_tool_is_always_offered():
    import toolpick
    assert "go_to_sleep" in toolpick.pick("okay nova you can rest now")
