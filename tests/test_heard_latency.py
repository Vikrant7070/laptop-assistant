"""What the user said must show up at once (chat + bubble), not after her answer (2026-11-18: seconds late)."""
import numpy as np

from nova.audio import voice
from nova.core import app


def test_heard_pushes_what_was_said_before_answering(monkeypatch):
    seen = []
    monkeypatch.setattr(app.voice_io, "events", [])
    monkeypatch.setattr(app.voice_io, "wants_sleep", lambda t: False)
    monkeypatch.setattr(app.voice_io, "note_task", lambda: None)
    monkeypatch.setattr(app.integrations, "help_first", lambda: False)
    monkeypatch.setattr(app.integrations, "take_offer", lambda t: None)
    monkeypatch.setattr(app.avatar_client, "send", lambda *a, **k: None)
    monkeypatch.setitem(app.state, "pending", None)

    def fake_answer(text):
        seen.extend(dict(e) for e in app.events)          # what the chat could already see
        return {"reply": "ok"}, 200
    monkeypatch.setattr(app.body, "answer", fake_answer)
    app.heard("what time is it", via="silent")
    assert seen == [{"me": "what time is it"}]
    assert app.events[-1] == {"reply": "ok"}               # no second copy of "me" with the answer


def test_whisper_keeps_careful_settings(monkeypatch):
    """Beam 1 / no VAD made her mishear "Nova" as "Noah" and ignore the wake word (2026-11-18)."""
    calls = []

    class FakeModel:
        class model:
            is_multilingual = False

        def transcribe(self, audio, **kw):
            calls.append(kw)
            return iter([]), None

    w = voice.WhisperTranscriber()
    monkeypatch.setattr(w, "load", lambda: FakeModel())
    audio = np.zeros(1600, np.float32)
    w._decode_now(audio, None)
    w._decode_now(audio, None, force="en")
    assert calls[0]["beam_size"] == 3 and calls[0]["vad_filter"] is True
    assert calls[1]["beam_size"] == 3


def test_timing_note_reads_without_a_recording():
    w = voice.WhisperTranscriber()
    assert "decode 0.00s" in w.timing_note()
    w.timing = {"pause": 0.9, "vad": 0.05, "gpu_wait": 1.2, "decode": 0.4}
    assert "gpu wait 1.20s" in w.timing_note()


def test_misheard_names_still_wake_her_and_the_wake_listener_gets_a_hint():
    for said in ("Hey Inua.", "Hey, Noelle.", "Hey Noah."):
        assert voice.WAKE_RE.search(said), said
    assert "Nova" in voice.WAKE_HINT


def test_live_peek_sends_the_words_so_far_only_while_listening(monkeypatch):
    w = voice.WhisperTranscriber()
    got = []
    w.on_partial = got.append
    monkeypatch.setattr(w, "quick_english", lambda a: "open chrome and")
    w._live_peek(np.zeros(10, np.float32))          # not listening: nothing shown
    assert got == []
    w.live = True
    w._live_peek(np.zeros(10, np.float32))
    assert got == ["open chrome and"]
    w.self_voice = lambda: True                      # her own voice is never shown as the user's words
    w._live_peek(np.zeros(10, np.float32))
    assert got == ["open chrome and"]
