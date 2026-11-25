"""Fixes for what the end-user QA sweep found on 2026-11-25 (44 requests on a throwaway copy, local brain)."""
import pytest

from nova import shared
from nova.actions import readback, undo
from nova.actions.tools import apps, files
from nova.core import app
from nova.prompts import text as prompt_text


# --- every reply ended in "*(aside)*" lines and a pile of emoji, and she copied it from her own history --------

def test_asides_and_the_emoji_tail_are_tidied():
    raw = "Teal! 🌈 \n*(I’ve got it saved — and I’ll always remember your favorite color.)*\n\nAnything else? 😊📖🎬"
    assert shared.tidy_reply(raw) == "Teal! 🌈\n\nAnything else?"
    assert shared.tidy_reply("Calculator opened! 🧮") == "Calculator opened! 🧮"          # one emoji is fine
    assert shared.tidy_reply("Use *bold* words") == "Use *bold* words"                    # not an aside
    assert shared.tidy_reply("") == ""


def test_her_history_keeps_the_tidy_reply(brain):
    brain([("Teal! *(I saved it.)* Anything else? 😊📚🎬", [])])
    out, _ = app.answer("what's my favourite colour?")
    assert "📚" not in out["reply"] and "*(" not in out["reply"] and out["reply"].startswith("Teal!")
    stored = [m["content"] for m in app.state["messages"] if m["role"] == "assistant"][-1]
    assert "📚" not in stored and "*(" not in stored


# --- "what time is it": the right answer was replaced by a nudge reply ---------------------------------------

def test_a_fact_answer_with_im_all_set_isnt_nudged(brain):
    # 2026-11-25: get_datetime ran, she said "12:32 PM ... I'm all set to help", PROMISE_RE matched "I'm ... set" and
    # the nudge's "Oops! I'll fix that" became her answer.
    fake, ran = brain([("", [("get_datetime", {})]),
                       ("12:32 PM on Wednesday. I'm all set to help with whatever's next.", []),
                       ("Oops! I'll fix that.", [])])
    out, _ = app.answer("what time is it")
    assert out["reply"].startswith("12:32 PM") and "Oops" not in out["reply"]


def test_a_real_promise_is_still_nudged(brain):
    fake, ran = brain([("I'll open Chrome now.", []), ("", [("open_app", {"name": "chrome"})]), ("Opened!", [])])
    app.answer("open chrome")
    assert ran == [("open_app", {"name": "chrome"})]


# --- "she's called Lucy": she corrected the user who said "hi nova" -------------------------------------------

def test_the_prompt_says_nova_is_her_too():
    text = prompt_text("system")
    assert "This app is called Nova" in text and "Never correct" in text


# --- "make a folder called qa-notes in X" made nothing, 3 times out of 3 ---------------------------------------

def test_create_folder_takes_a_name_inside_the_path(tmp_path):
    out = files.create_folder(str(tmp_path), name="qa-notes")
    assert (tmp_path / "qa-notes").is_dir() and out == f"Created {tmp_path / 'qa-notes'}"
    args = {"path": str(tmp_path), "name": "qa-notes"}
    assert readback.read_back("create_folder", args, out)["state"] == "passed"
    assert files.folder_target({"path": str(tmp_path / "qa-notes"), "name": "qa-notes"}) == tmp_path / "qa-notes"
    assert files.create_folder(str(tmp_path / "solo")).startswith("Created")           # a full path still works


def test_undo_takes_away_the_named_folder(tmp_path):
    args = {"path": str(tmp_path), "name": "made-here"}
    snap = undo.before({"name": "create_folder", "args": args})
    out = files.create_folder(**args)
    assert snap["path"] == str(tmp_path / "made-here") and snap["new"] and out.startswith("Created")


# --- "open youtube and search lofi music" clicked the search box 3 times and stalled ---------------------------

@pytest.mark.parametrize("words, query", [("youtube lofi music", "lofi+music"), ("search youtube for lofi", "lofi"),
                                          ("youtube: cat videos", "cat+videos"), ("lofi beats on youtube", "lofi+beats")])
def test_youtube_search_opens_the_results_page(monkeypatch, words, query):
    opened = []
    monkeypatch.setattr(apps, "_browser_in_use", lambda: None)
    monkeypatch.setattr(apps.webbrowser, "open", lambda url: opened.append(url))
    apps.open_website(words)
    assert opened == [f"https://www.youtube.com/results?search_query={query}"]


def test_plain_words_still_go_to_google_and_urls_open(monkeypatch):
    opened = []
    monkeypatch.setattr(apps, "_browser_in_use", lambda: None)
    monkeypatch.setattr(apps.webbrowser, "open", lambda url: opened.append(url))
    apps.open_website("best budget earphones")
    apps.open_website("youtube.com")
    assert opened == ["https://www.google.com/search?q=best+budget+earphones", "https://youtube.com"]


def test_a_youtube_search_request_opens_the_results_page_by_code(brain):
    fake, ran = brain([("Here are the lofi music results on YouTube.", [])])
    out, _ = app.answer("open youtube and search lofi music")
    assert ran[0] == ("open_website", {"url_or_search": "youtube lofi music"})
    assert "lofi" in out["reply"]


def test_open_youtube_alone_is_left_to_the_brain(brain):
    fake, ran = brain([("", [("open_website", {"url_or_search": "youtube.com"})]), ("Opened YouTube.", [])])
    app.answer("open youtube")
    assert ran == [("open_website", {"url_or_search": "youtube.com"})]


# --- "undo that" after a step that changed nothing undid an older change ---------------------------------------

def test_undo_that_doesnt_reach_past_a_request_that_changed_nothing(brain, monkeypatch, tmp_path):
    import json as _json
    from nova import store
    from nova.actions import tools as _tools
    real = _tools.run_tool
    s = app.settings()
    s["autonomy"] = "trusted"
    store.current().settings.write_text(_json.dumps(s), encoding="utf-8")
    todo = tmp_path / "todo.txt"
    fake, ran = brain([("", [("write_text_file", {"path": str(todo), "content": "buy milk"})]), ("Wrote it.", []),
                       ("", [("move_or_rename", {"source": str(tmp_path / "nope.txt"),
                                                 "destination": str(tmp_path / "tasks.txt")})]), ("Couldn't.", []),
                       ("I couldn't - nope.txt isn't there.", []),           # the one more go after the failed check
                       ("", [("undo_last_change", {"which": "that"})]), ("Nothing to undo from that.", [])])
    outs = []
    monkeypatch.setattr(_tools, "run_tool", lambda n, a: (ran.append((n, a)), outs.append(real(n, a)), outs[-1])[2])
    app.answer(f"write buy milk to {todo}")
    app.answer(f"rename {tmp_path / 'nope.txt'} to tasks.txt")
    app.answer("undo that")
    assert todo.exists()                                                    # the earlier write was NOT undone
    assert outs[-1].startswith("Nothing to undo from the last request") and "undo change #" in outs[-1]
