"""Characters live in characters/<id>/ (character.json + persona.md + model.vrm), separate from the code: each one
has her own name, wake word, voice, personality, look and 3D fit, and switching changes all of them."""
import json

from nova.audio import voice
from nova.core import app, characters


def test_the_three_characters_load_from_their_folders():
    assert {"nova", "makima", "lucy"} <= set(characters.CHARACTERS)
    for cid in ("nova", "makima", "lucy"):
        ch = characters.get(cid)
        assert ch["persona"]["intro"] and ch["voice"] and ch["wake"]["hint"]
        assert characters.model_path(ch) is not None


def test_a_new_folder_is_a_new_character(tmp_path):
    d = tmp_path / "aria"
    d.mkdir()
    (d / "character.json").write_text(json.dumps({"name": "Aria", "voice": "af_sky"}), encoding="utf-8")
    (d / "persona.md").write_text("# Who you are\nYou are {NAME}.\n# Touch\n- Smile.", encoding="utf-8")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "character.json").write_text("{not json", encoding="utf-8")
    found = characters.load(tmp_path)
    assert list(found) == ["aria"]                                     # the broken folder is skipped
    aria = found["aria"]
    assert aria["wake"]["names"] == ["aria"] and "Hey Aria" in aria["wake"]["hint"]
    assert aria["persona"] == {"intro": "You are {NAME}.", "touch": "- Smile."}


def wake(cid):
    ch = characters.get(cid)
    voice.set_wake(characters.wake_names(ch), ch["wake"]["hint"])
    return voice.WAKE_RE


def test_each_character_answers_only_to_her_own_name():
    try:
        assert wake("lucy").search("Hey Lucy, open Chrome") and not wake("lucy").search("hey nova open chrome")
        assert wake("makima").search("Hi Makima.") and not wake("makima").search("Hey Lucy")
        assert wake("nova").search("hey nova what's the time") and not wake("nova").search("hey makima")
        assert "Lucy" in characters.get("lucy")["wake"]["hint"]
    finally:
        wake("nova")


def test_switching_character_changes_voice_name_and_stop_words(monkeypatch):
    sent = []
    monkeypatch.setattr(app.avatar_client, "send", lambda *a, **k: sent.append((a, k)))
    try:
        ch = app.apply_character("makima")
        assert voice.NEURAL_VOICE == ch["voice"] == "af_nicole"
        assert voice.WAKE_RE.search("hey makima") and not voice.WAKE_RE.search("hey nova")
        assert app.just_stop("makima stop") and not app.just_stop("lucy stop")
        assert any(a[0] == "look" for a, k in sent)                    # her look goes to the 3D body
    finally:
        app.apply_character("nova")


def test_switching_ends_the_old_characters_conversation(monkeypatch):
    """After "Hey Lucy" opened a conversation, switching to Nova must not let Nova take requests without "Hey Nova"."""
    monkeypatch.setattr(app.avatar_client, "send", lambda *a, **k: None)
    closed = []
    monkeypatch.setattr(app, "ear_close", lambda: closed.append(True))
    app.convo["on"] = True
    try:
        app.apply_character("nova")
        assert not app.convo["on"] and closed
    finally:
        app.convo["on"] = False


def test_prompt_uses_her_own_persona_and_name():
    for cid, words in (("nova", "desi dost"), ("makima", "calm, composed"), ("lucy", "books, writing and stars")):
        text = app.persona_text(cid)
        name = characters.get(cid)["name"]
        assert words in text and f'Hey {name}' in text and "{NAME}" not in text and "{DESI_TALK}" not in text
