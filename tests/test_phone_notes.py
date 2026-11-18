"""The iPhone's calls and notifications (relayed by Phone Link): her brain decides what each one is and how to say it."""
import json
import time

import pytest

from nova.actions import notifications
from nova.core import app


def set_settings(**kw):
    s = app.settings()
    s.update(kw)
    app.SETTINGS_FILE.write_text(json.dumps(s), encoding="utf-8")


def note(i, body, title="Phone", app_name="Phone Link"):
    return {"id": i, "app": app_name, "title": title, "body": body, "time": time.time()}


RINGING = note(1, "Rani 2024 Incoming Call")
UNKNOWN = note(2, "+91 98765 43210 Incoming Call")
MISSED = note(3, "Rani 2024 Missed Call")
WHATSAPP = note(4, "Are you coming tonight?", title="Rahul", app_name="Phone Link")
OTHER_APP = {"id": 9, "app": "WhatsApp", "title": "Mom", "body": "Call me", "time": time.time()}


cues = []          # sounds played at the moment a phone notification arrives


def reply(kind, say):
    return (json.dumps({"kind": kind, "say": say}), [])


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    app.phone_seen.clear()
    cues.clear()
    monkeypatch.setattr(app.sfx, "play", lambda name: cues.append(name) or True)
    app.events.clear()
    forwarded = []
    monkeypatch.setattr(app, "tell_away", lambda line: forwarded.append(line))
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    set_settings(notifications=True)
    return forwarded


# ---------------------------------------------------------------- reading the brain's answer

def test_parse_plain_fenced_and_garbage():
    assert notifications.parse_phone_note('{"kind": "call", "say": "Rahul is calling."}', "fb") == ("call", "Rahul is calling.")
    fenced = '```json\n{"kind": "Missed_Call", "say": "You missed Rahul."}\n```'
    assert notifications.parse_phone_note(fenced, "fb") == ("missed_call", "You missed Rahul.")
    assert notifications.parse_phone_note("Sure! It's a call.", "fb") == ("other", "fb")
    assert notifications.parse_phone_note('{"kind": "call", "say": ""}', "fb") == ("other", "fb")
    assert notifications.parse_phone_note('{"kind": "banana", "say": "Hi"}', "fb") == ("other", "Hi")
    assert notifications.parse_phone_note(None, "fb") == ("other", "fb")


def test_is_phone():
    assert notifications.is_phone(RINGING)
    assert notifications.is_phone(note(5, "x", app_name="Phone"))
    assert not notifications.is_phone(OTHER_APP)


# ---------------------------------------------------------------- calls

def test_call_from_a_saved_contact_is_said_by_the_brain(brain):
    fake, _ = brain([reply("call", "Rani is calling you on your phone.")])
    app.on_notifications([RINGING])
    assert app.mouth.said == ["Rani is calling you on your phone."]
    assert app.events[-1] == {"reply": "🔔 Rani is calling you on your phone.", "kind": "notification"}
    sent = json.dumps(fake.seen[0])
    assert "Rani 2024 Incoming Call" in sent and "unknown number" in sent     # the brain got the text and the rule


def test_call_from_an_unknown_number(brain):
    brain([reply("call", "There's a call from an unknown number on your phone.")])
    app.on_notifications([UNKNOWN])
    assert app.mouth.said == ["There's a call from an unknown number on your phone."]


def test_the_brain_is_the_local_one_even_with_a_cloud_brain_chosen(brain, monkeypatch):
    fake, _ = brain([reply("call", "Rani is calling.")])
    models = []
    real = app.llm.chat
    monkeypatch.setattr(app.llm, "chat", lambda model, *a, **k: (models.append(model), real(model, *a, **k))[1])
    monkeypatch.setattr(app, "brain", lambda s=None: "cloud:gemini")
    set_settings(model="local-model")
    app.on_notifications([RINGING])
    assert models == ["local-model"]
    app.phone_seen.clear()
    set_settings(model="local-model", phone_notes_cloud=True)
    fake.script.append(reply("call", "Rani is calling."))
    app.on_notifications([RINGING])
    assert models[-1] == "cloud:gemini"


def test_a_ringing_call_is_not_forwarded_but_a_missed_one_is(brain, fresh):
    brain([reply("call", "Rani is calling."), reply("missed_call", "You missed a call from Rani.")])
    app.on_notifications([RINGING])
    assert fresh == []
    app.on_notifications([MISSED])
    assert fresh == ["You missed a call from Rani."]
    assert app.mouth.said[-1] == "You missed a call from Rani."


def test_a_ringing_call_does_not_wait_for_a_long_answer(brain, monkeypatch):
    brain([reply("call", "Rani is calling.")])
    monkeypatch.setattr(app.mouth, "busy", lambda: True)
    t = [1000.0]
    monkeypatch.setattr(app.time, "time", lambda: t[0])
    monkeypatch.setattr(app.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    app.on_notifications([RINGING])
    assert app.mouth.said == ["Rani is calling."]
    assert t[0] - 1000.0 < app.PHONE_CALL_WAIT + 1               # not the 30 s a normal notification waits


def test_calls_off_skips_calls_but_not_messages(brain):
    set_settings(phone_calls=False)
    brain([reply("call", "Rani is calling."), reply("missed_call", "Missed Rani."),
           reply("message", "Rahul on WhatsApp: are you coming tonight?")])
    app.on_notifications([RINGING])
    app.on_notifications([MISSED])
    assert app.mouth.said == [] and app.events == []
    app.on_notifications([WHATSAPP])
    assert app.mouth.said == ["Rahul on WhatsApp: are you coming tonight?"]


def test_the_same_notification_is_said_once(brain):
    brain([reply("call", "Rani is calling."), reply("call", "Rani is calling again.")])
    app.on_notifications([RINGING])
    app.on_notifications([RINGING])
    assert app.mouth.said == ["Rani is calling."]


def test_a_ringing_call_is_said_even_in_an_idle_nap(brain):
    brain([reply("call", "Rani is calling.")])
    app.nap.update(asleep=True, reason="idle")
    try:
        app.on_notifications([RINGING])
    finally:
        app.nap.update(asleep=False, reason=None)
    assert app.mouth.said == ["Rani is calling."] and cues == ["happy"]


def test_go_to_sleep_silences_calls_and_messages(brain):
    brain([reply("call", "Rani is calling."), reply("message", "Rahul: are you coming?")])
    app.nap.update(asleep=True, reason="asked")
    try:
        app.on_notifications([RINGING])
        app.on_notifications([WHATSAPP])
    finally:
        app.nap.update(asleep=False, reason=None)
    assert app.mouth.said == [] and cues == []


def test_an_idle_nap_still_holds_back_messages_and_missed_calls(brain):
    brain([reply("message", "Rahul: are you coming?"), reply("missed_call", "You missed Rani.")])
    app.nap.update(asleep=True, reason="idle")
    try:
        app.on_notifications([WHATSAPP])
        app.on_notifications([MISSED])
    finally:
        app.nap.update(asleep=False, reason=None)
    assert app.mouth.said == []


# ---------------------------------------------------------------- safety and fallbacks

def test_codes_in_the_brains_sentence_are_hidden(brain):
    brain([reply("message", "Your bank OTP is 482913, do not share it")])
    app.on_notifications([note(6, "OTP 482913", title="Bank")])
    assert "482913" not in app.mouth.said[0] and "482913" not in json.dumps(app.events)


@pytest.mark.parametrize("script", [[("I can't help with that", [])], [(RuntimeError("model down"), [])], [("", [])]])
def test_a_bad_brain_falls_back_to_the_plain_text(brain, script):
    brain(script)
    app.on_notifications([MISSED])
    assert app.mouth.said == ["Phone Link: Phone - Rani 2024 Missed Call"]


def test_a_slow_brain_is_not_waited_for_forever(brain, monkeypatch):
    brain([])
    monkeypatch.setattr(app, "PHONE_NOTE_SECS", 0.05)
    monkeypatch.setattr(app.llm, "chat", lambda *a, **k: time.sleep(1) or (None, "late", []))
    monkeypatch.setattr(app.time, "sleep", time.sleep)
    t0 = time.time()
    app.on_notifications([MISSED])
    assert time.time() - t0 < 0.8
    assert app.mouth.said == ["Phone Link: Phone - Rani 2024 Missed Call"]


def test_other_apps_never_reach_the_phone_brain(brain):
    fake, _ = brain([])
    app.on_notifications([OTHER_APP])
    assert fake.seen == []
    assert app.mouth.said == ["WhatsApp: Mom - Call me"]


def test_muting_phone_link_silences_it(brain):
    fake, _ = brain([reply("call", "Rani is calling.")])
    set_settings(notif_muted=["Phone Link"])
    app.on_notifications([RINGING])
    assert fake.seen == [] and app.mouth.said == []


def test_a_burst_mixes_phone_and_other_apps(brain):
    brain([reply("message", "Rahul on WhatsApp: are you coming tonight?")])
    app.on_notifications([WHATSAPP, OTHER_APP])
    assert app.mouth.said == ["2 new notifications. Rahul on WhatsApp: are you coming tonight? … WhatsApp: Mom - Call me"]


def test_settings_switches_are_saved(client):
    client.post("/api/setup", json={"phone_calls": False, "phone_notes_cloud": True})
    s = app.settings()
    assert s["phone_calls"] is False and s["phone_notes_cloud"] is True
    st = client.get("/api/status").get_json()
    assert st["phone_calls"] is False and st["phone_notes_cloud"] is True


# ---------------------------------------------------------------- speed: a sound at once, the words follow

def test_a_sound_plays_the_moment_a_phone_notification_arrives(brain):
    brain([reply("call", "Rani is calling.")])
    app.on_notifications([RINGING])
    assert cues == ["happy"]


def test_no_sound_for_other_apps_duplicates_or_when_calls_are_off(brain):
    brain([reply("call", "Rani is calling.")])
    app.on_notifications([OTHER_APP])
    assert cues == []
    app.on_notifications([RINGING])
    app.on_notifications([RINGING])                    # same toast again
    assert cues == ["happy"]
    cues.clear()
    set_settings(phone_calls=False)
    app.on_notifications([note(7, "Rani 2024 Incoming Call")])
    assert cues == []


def test_the_watcher_polls_every_second():
    assert app.notif_watcher.every == 1
