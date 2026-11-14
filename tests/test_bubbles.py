"""3D Nova's speech bubble: each thing she says appears once, and in step with her voice."""
import json
import queue
import threading
import time

import pytest

import app
import voice


@pytest.fixture
def sent(monkeypatch):
    """Everything sent to 3D Nova: [(do, args)]."""
    log = []
    monkeypatch.setattr(app.avatar_client, "send", lambda do, timeout=20, **a: log.append((do, a)))
    return log


def set_voice(on):
    s = app.settings()
    s["voice"] = on
    app.SETTINGS_FILE.write_text(json.dumps(s), encoding="utf-8")


def bubbles(sent):
    return [a["text"] for do, a in sent if do == "say"]


def test_voice_reply_voice_on_no_extra_bubble(brain, sent):
    """Voice on: the voice shows each sentence while saying it, so nothing else may show the reply."""
    set_voice(True)
    brain([("All good, boss!", [])])
    app.heard("how is my laptop", via="silent")
    assert "All good, boss!" not in bubbles(sent)
    assert any("All good" in t for t in app.mouth.said)


def test_voice_reply_voice_off_exactly_one_bubble(brain, sent):
    set_voice(False)
    brain([("All good, boss!", [])])
    app.heard("how is my laptop", via="silent")
    assert bubbles(sent).count("All good, boss!") == 1


def test_typed_reply_voice_off_one_bubble(brain, sent):
    set_voice(False)
    brain([("Hello!", [])])
    app.answer("hi")
    assert bubbles(sent) == ["Hello!"]


def test_go_to_sleep_says_once(sent):
    set_voice(True)
    app.go_to_sleep("asked")
    assert bubbles(sent) == []                        # the voice will show it while saying it
    assert len([t for t in app.mouth.said if "nap" in t]) == 1


def test_pending_question_once(brain, sent):
    set_voice(False)
    s = app.settings()
    s["autonomy"] = "careful"
    app.SETTINGS_FILE.write_text(json.dumps(s), encoding="utf-8")
    brain([("", [("kill_process", {"name_or_pid": "x"})])])
    app.heard("close x", via="silent")
    asks = [t for t in bubbles(sent) if t.startswith("May I")]
    assert len(asks) == 1


def test_page_no_longer_sends_reply_bubbles():
    html = (app.ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert 'body3d("happy", j.reply)' not in html and 'body3d("happy", line)' not in html


# ---------------------------------------------------------------- the voice's own bubble / lips messages

def bare_mouth(monkeypatch, start=True):
    """A Mouth without its audio threads, to test how it talks to 3D Nova."""
    m = voice.RealMouth.__new__(voice.RealMouth)
    m.gen, m.lips, m.bubbles, m.bubble_until = 0, False, True, 0.0
    m.tells, m.q, m.audio, m.lock = queue.Queue(), queue.Queue(), queue.Queue(), threading.Lock()
    m.pending, m.stop_flag = 0, False
    got = []
    monkeypatch.setattr(voice.avatar_client, "send", lambda do, timeout=20, **a: got.append((do, a)))
    if start:
        threading.Thread(target=m._teller, daemon=True).start()
    return m, got


def wait_for(cond, secs=2):
    end = time.time() + secs
    while not cond() and time.time() < end:
        time.sleep(0.01)


def test_sentence_bubble_lasts_as_long_as_the_sound(monkeypatch):
    m, got = bare_mouth(monkeypatch)
    m._bubble("Done, boss!", 1.5, m.gen)
    wait_for(lambda: got)
    assert got == [("say", {"text": "Done, boss!", "secs": 2.1})]


def test_stop_drops_stale_messages_and_clears_bubble(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", type("sd", (), {"stop": staticmethod(lambda: None)}))
    m, got = bare_mouth(monkeypatch, start=False)
    m._bubble("old sentence", 3, m.gen)      # queued for the reply that is playing ...
    m._lips(True, m.gen)
    m.stop()                                 # ... which is interrupted before 3D Nova got the messages
    threading.Thread(target=m._teller, daemon=True).start()
    wait_for(lambda: got[-1] == ("say", {"text": "", "secs": 0}))
    texts = [a.get("text") for do, a in got if do == "say"]
    assert texts == [""]                                       # stale bubble never shown; bubble cleared
    assert ("anim", {"name": "idle"}) in got and ("anim", {"name": "talk"}) not in got


def test_bubbles_off_shows_nothing(monkeypatch):
    m, got = bare_mouth(monkeypatch)
    m.bubbles = False
    m._bubble("hi", 1, m.gen)
    time.sleep(0.1)
    assert got == []


def test_long_first_sentence_is_split_for_a_quick_start():
    s = voice.RealMouth._sentences("Keep your desk clean and tackle one task for 25 minutes straight without touching "
                                   "your phone, boss. Take a break.")
    assert len(s[0]) <= 80 and len(s) == 3 and s[-1] == "Take a break."
    assert voice.RealMouth._sentences("Done, boss! All good.") == ["Done, boss!", "All good."]
