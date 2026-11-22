"""R10: her voice side as one state machine, running in shadow mode next to the old flags."""
import pytest

from nova import db
from nova.core import app
from nova.core.app import voice_machine as vs


# ------------------------------------------------------------------ the table


@pytest.mark.parametrize("row", sorted(vs.TRANSITIONS.items()), ids=lambda r: f"{r[0][0]}+{r[0][1]}")
def test_every_row_of_the_table(row):
    (mode, event), want = row
    m = vs.VoiceState(mode=mode, strict=True)
    got = m.fire(event)
    assert got == (vs.WAITING if want == vs.AWAKE else want) and m.mode == got


def test_every_mode_handles_the_events_that_can_reach_it():
    """Sleep, ears and waking can happen in any mode, so each mode needs a row for them."""
    for mode in vs.MODES:
        for event in ("ears_on", "ears_off", "sleep_asked", "nap_due", "woken"):
            assert (mode, event) in vs.TRANSITIONS, (mode, event)


def test_illegal_transitions_raise_in_strict_mode_and_are_logged_in_shadow_mode():
    with pytest.raises(vs.IllegalTransition):
        vs.VoiceState(mode=vs.WAITING, strict=True).fire("deep_due")         # deep sleep only after a nap
    with pytest.raises(vs.IllegalTransition):
        vs.VoiceState(strict=True).fire("made_up_event")
    m = vs.VoiceState(mode=vs.WAITING)
    assert m.fire("deep_due") == vs.WAITING
    assert db.find_events(kind="voice_illegal")[-1]["text"] == "deep_due in WAITING_FOR_WAKE"


def test_waking_up_remembers_the_ears_switch():
    m = vs.VoiceState(strict=True)
    m.fire("ears_off")
    m.fire("sleep_asked")
    assert m.state == vs.NAPPING
    assert m.fire("woken") == vs.EARS_OFF                                  # still no wake word after the nap
    m.fire("ears_on")
    m.fire("nap_due")
    m.fire("deep_due")
    assert m.fire("woken") == vs.WAITING


def test_activities_sit_on_top_of_the_mode():
    m = vs.VoiceState(strict=True)
    m.fire("wake_heard")
    m.fire("hearing_started")
    assert m.state == vs.HEARING
    m.fire("hearing_ended")
    m.fire("request_heard")
    m.fire("reply_started")
    assert m.state == vs.SPEAKING                                          # talking beats thinking
    m.fire("reply_done")
    assert m.state == vs.THINKING
    m.fire("answered")
    assert m.state == vs.CONVERSATION
    m.fire("sleep_asked")
    m.fire("reply_started")                                                # the goodnight line, said lying down
    assert m.state == vs.SPEAKING and m.mode == vs.NAPPING
    m.fire("barge_in")
    assert m.state == vs.NAPPING


def test_every_change_is_logged_and_published():
    seen = []
    m = vs.VoiceState(publish=lambda kind, data: seen.append((kind, data["state"], data["event"])))
    m.fire("wake_heard")
    m.fire("wake_heard")                                                   # no change: nothing new
    assert seen == [("voice_state", vs.CONVERSATION, "wake_heard")]
    assert db.find_events(kind="voice")[-1]["text"] == "WAITING_FOR_WAKE -> CONVERSATION (wake_heard)"


def test_the_flags_say_the_same_thing():
    assert vs.from_flags({})["mode"] == vs.WAITING
    assert vs.from_flags({"ears_off": True})["mode"] == vs.EARS_OFF
    assert vs.from_flags({"ears_off": True, "convo_on": True})["mode"] == vs.CONVERSATION    # 🎤 button with ears off
    assert vs.from_flags({"asleep": True, "ears_off": True})["mode"] == vs.NAPPING
    assert vs.from_flags({"asleep": True, "deep": True})["mode"] == vs.DEEP_SLEEP


def test_a_disagreement_is_logged():
    m = vs.VoiceState()
    assert m.check({"convo_on": False}) == ""
    note = m.check({"convo_on": True})
    assert "machine WAITING_FOR_WAKE" in note and "flags CONVERSATION" in note
    assert db.find_events(kind="voice_mismatch")[-1]["data"]["machine"] == vs.WAITING


# ------------------------------------------------------------------ shadow mode inside voice_io: no mismatches


@pytest.fixture
def quiet(monkeypatch):
    """voice_io's flows without their side effects (no speech, no 3D body, no ears)."""
    for name in ("announce", "ear_close", "ear_listen", "ear_listen_after_speech", "push_event"):
        monkeypatch.setattr(app.voice_io, name, lambda *a, **k: None)
    monkeypatch.setattr(app.voice_io.ears, "start", lambda *a, **k: None)
    monkeypatch.setattr(app.voice_io.ears, "stop", lambda *a, **k: None)
    app.voice_io.convo.update(on=False, thinking=False)
    app.voice_io.nap.update(asleep=False, deep=False)
    app.voice_io.listen_state["ears_off"] = False
    yield app.voice_io.machine


def _mismatches():
    return db.find_events(kind="voice_mismatch") + db.find_events(kind="voice_illegal")


def test_conversation_nap_deep_sleep_and_waking_agree_with_the_flags(quiet):
    vio = app.voice_io
    vio._start_conversation(greet=False)
    assert quiet.state == vs.CONVERSATION
    vio.go_to_sleep("asked")
    assert quiet.state == vs.NAPPING
    vio.go_deep()
    assert quiet.state == vs.DEEP_SLEEP
    vio.wake_body(clicked=True)
    assert quiet.state == vs.WAITING
    vio._start_conversation(greet=False)
    vio.go_to_sleep("idle")
    vio.note_task()                                   # a typed request wakes her
    assert quiet.state == vs.WAITING and _mismatches() == []


def test_the_ears_switch_agrees_with_the_flags(quiet):
    vio = app.voice_io
    vio._start_conversation(greet=False)
    vio.set_ears(False)
    assert quiet.state == vs.EARS_OFF and not vio.convo["on"]
    vio.go_to_sleep("asked")
    vio.wake_body()
    assert quiet.state == vs.EARS_OFF
    vio.set_ears(True)
    assert quiet.state == vs.WAITING and _mismatches() == []


def test_a_spoken_request_is_thinking_then_back(quiet, brain):
    brain([("Sure.", [])])
    app.voice_io._start_conversation(greet=False)
    seen = []
    real_fire = quiet.fire
    quiet.fire = lambda e: (seen.append(e), real_fire(e))[1]
    app.voice_io.heard("what's two plus two", via="silent")
    assert "request_heard" in seen and "answered" in seen
    assert quiet.state in (vs.CONVERSATION, vs.SPEAKING) and _mismatches() == []


def test_the_lips_drive_speaking(quiet):
    app.voice_io.mouth.on_lips(True)
    assert quiet.state == vs.SPEAKING
    app.voice_io.mouth.on_lips(False)
    assert quiet.state == vs.WAITING


def test_a_broken_machine_never_breaks_her_voice(quiet, monkeypatch):
    def boom(event):
        raise RuntimeError("bug in the table")
    monkeypatch.setattr(quiet, "fire", boom)
    app.voice_io._start_conversation(greet=False)       # still works; the bug is logged
    assert app.voice_io.convo["on"]


def test_the_shadow_week_can_be_read_back(quiet, client):
    app.voice_io._start_conversation(greet=False)
    quiet.check({"convo_on": False})                     # a made-up disagreement
    got = client.get("/api/voice_machine").get_json()
    assert got["state"] == vs.CONVERSATION and got["history"][-1]["event"] == "wake_heard"
    assert len(got["mismatches"]) == 1 and got["illegal"] == []
