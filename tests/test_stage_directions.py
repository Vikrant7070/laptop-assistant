"""The small brain writes '[react: "laugh"]' or '*(I'm dancing!)*' instead of calling react. She must not say those
out loud (2026-11-18: she read the reaction instead of doing it) - her body plays the move they name instead."""
import pytest

from nova import shared
from nova.audio import voice
from nova.core import app


@pytest.mark.parametrize("text, said", [
    ('[react: "laugh"]', ""),
    ("*(I'm dancing - no Bollywood, just desi vibes.)*", ""),
    ("*Roaring like a Bengal tiger in the jungle - ROOOAR!*", ""),
    ("Ha! *laughs* That was good.", "Ha! That was good."),
    ("I'm not a tiger, but I can *roar* like one!", "I'm not a tiger, but I can roar like one!"),   # emphasis stays
    ("**Laptop Assistant** bots like Nova", "Laptop Assistant bots like Nova"),
    ("I'm *always* here.", "I'm always here."),
])
def test_stage_directions_are_not_spoken(text, said):
    assert voice._clean_for_speech(text) == said


@pytest.mark.parametrize("text, move", [
    ('It said "typing me out." [react: "laugh"]', "laugh"),
    ("*(I just did a little spin and clap for you.)*", "spin"),
    ("Okay! *waves*", "wave"),
    ("Just a normal answer, with *emphasis*.", None),
])
def test_stage_direction_names_the_move(text, move):
    assert shared.stage_reaction(shared.stage_directions(text)[1]) == move


def test_stream_waits_for_a_stage_direction_to_close(monkeypatch):
    said = []
    monkeypatch.setattr(app.voice_io, "speak", lambda s: said.append(s))
    st = app.SpeechStream()
    for piece in ["Sure! ", "*(Nap in the corner. ", "No more dancing.)*", "\nBye now. ", "Done"]:
        st.feed(piece)
    st.flush()
    spoken = [voice._clean_for_speech(s) for s in said]
    assert [s for s in spoken if s] == ["Sure!", "Bye now.", "Done"]


def test_written_reaction_is_played_and_hidden(monkeypatch):
    played = []
    monkeypatch.setattr(app.body, "react_later", lambda name, wait_for_speech=True, repeat=None: played.append(name))
    app.state.pop("react_after", None)
    app.after_turn("tell me a joke", {"reply": 'Why did the laptop... ha! [react: "laugh"]', "activity": []})
    assert played == ["laugh"]


@pytest.mark.parametrize("text, move", [("[react, reaction=clap]", "clap"), ('[react: "laugh"]', "laugh"),
                                        ("[react: spin]", "spin")])
def test_marker_variants(text, move):
    assert voice._clean_for_speech("Hi. " + text) == "Hi."
    assert shared.stage_reaction(shared.stage_directions(text)[1]) == move
