"""QA plan P2-b: small talk is quick and never "nudged" (QA 2026-11-25: "hi" took 12 s - the tool list changed with
every message, so the model re-read ~5,000 tokens - and its promise nudge turned "Hey Vikrant!" into "Oops! Sorry")."""
from nova import shared
from nova.core import app


def _tools_seen(fake, monkeypatch):
    sent = []
    real = fake.__call__

    def spy(model, messages, tools_, *a, **k):
        sent.append(tools_)
        return real(model, messages, tools_, *a, **k)
    monkeypatch.setattr(app.llm, "chat", spy)
    return sent


def test_hi_goes_with_no_tools_and_is_not_nudged(brain, monkeypatch):
    fake, ran = brain([("Hey Vikrant! I'm here to help with whatever you need.", []), ("Oops! Sorry.", [])])
    sent = _tools_seen(fake, monkeypatch)
    out, _ = app.answer("hi")
    assert out["reply"].startswith("Hey Vikrant!") and "Oops" not in out["reply"]
    assert sent == [[]] and not ran                                   # one brain turn, no tools sent


def test_thanks_and_a_joke_are_small_talk_too(brain, monkeypatch):
    for text in ("thanks nova", "tell me a short joke", "how are you?"):
        fake, _ = brain([("Sure thing.", [])])
        sent = _tools_seen(fake, monkeypatch)
        app.answer(text)
        assert sent == [[]], text


def test_a_greeting_with_a_request_still_gets_its_tools(brain, monkeypatch):
    fake, ran = brain([("", [("open_app", {"name": "chrome"})]), ("Opened Chrome.", [])])
    sent = _tools_seen(fake, monkeypatch)
    app.answer("hi nova, open chrome")
    assert ran == [("open_app", {"name": "chrome"})] and sent[0]


def test_a_whole_line_in_brackets_is_an_aside():
    raw = "Hey Vikrant! What's on your mind?\n\n(And yes — I am Nova, not Lucy. But you can call me either.)"
    assert shared.tidy_reply(raw) == "Hey Vikrant! What's on your mind?"
    assert shared.tidy_reply("(Just checking.)") == "(Just checking.)"             # a reply that is only that keeps it
    assert shared.tidy_reply("It's 5 (five) o'clock.") == "It's 5 (five) o'clock."    # brackets inside a line stay


def test_small_talk_isnt_sent_the_window_list(brain, monkeypatch):
    fake, _ = brain([("Hey!", [])])
    from nova.actions.tools import apps
    monkeypatch.setattr(apps, "open_windows_summary", lambda: "Windows open: 'Downloads - File Explorer'")
    app.answer("hi nova")
    said = [m["content"] for m in fake.seen[0] if m["role"] == "user"][-1]
    assert "File Explorer" not in said and said.startswith("hi nova")
