"""The conversation loop: approvals, autonomy levels, error rollback, history trimming and intent shortcuts."""
import json

import pytest

from nova import intents
from nova import store
from nova.core import app


def set_autonomy(mode):
    s = app.settings()
    s["autonomy"] = mode
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


def test_plain_answer(brain):
    fake, ran = brain([("Hello boss!", [])])
    out, status = app.answer("hi there")
    assert status == 200 and out["reply"] == "Hello boss!" and not ran


def test_careful_mode_asks_before_risky_tool(brain):
    set_autonomy("careful")
    fake, ran = brain([("", [("kill_process", {"name_or_pid": "chrome"})]), ("Closed it.", [])])
    out, _ = app.answer("close chrome")
    assert out["pending"][0]["tool"] == "kill_process" and not ran
    out, _ = app.confirm_pending(True)
    assert ran == [("kill_process", {"name_or_pid": "chrome"})]
    assert out["reply"] == "Closed it."


def test_declined_action_does_not_run(brain):
    set_autonomy("careful")
    fake, ran = brain([("", [("kill_process", {"name_or_pid": "chrome"})]), ("Okay, I won't.", [])])
    app.answer("close chrome")
    app.confirm_pending(False)
    assert not ran


def test_after_a_no_her_ill_wait_isnt_pushed_into_asking_again(brain):
    # Found live 2026-11-25: after No, "Okay, I'll wait - no deletion yet" matched PROMISE_RE, the promise nudge sent
    # her back to the tool, and she asked for the same delete a second time.
    set_autonomy("careful")
    fake, ran = brain([("", [("kill_process", {"name_or_pid": "chrome"})]),
                       ("Okay, I'll wait - I won't close it.", []),
                       ("", [("kill_process", {"name_or_pid": "chrome"})])])     # what the nudge used to bring
    app.answer("close chrome")
    out, _ = app.confirm_pending(False)
    assert not ran and not out.get("pending") and "wait" in out["reply"]


def test_moving_on_declines_pending(brain):
    set_autonomy("careful")
    fake, ran = brain([("", [("kill_process", {"name_or_pid": "chrome"})]), ("Hi!", [])])
    app.answer("close chrome")
    app.answer("never mind, hi")
    assert not ran and app.state["pending"] is None


def test_trusted_mode_still_asks_for_dangerous(brain):
    set_autonomy("trusted")
    fake, ran = brain([("", [("power_action", {"action": "shutdown"})])])
    out, _ = app.answer("shut down")
    assert out.get("pending") and not ran


def test_trusted_mode_runs_risky_without_asking(brain):
    set_autonomy("trusted")
    fake, ran = brain([("", [("kill_process", {"name_or_pid": "x"})]), ("Done.", [])])
    out, _ = app.answer("close x")
    assert ran and not out.get("pending")


def test_cancel_shutdown_never_asks(brain):
    set_autonomy("careful")
    assert not app.is_risky({"name": "power_action", "args": {"action": "cancel"}})


def test_promise_without_tool_gets_one_nudge(brain):
    fake, ran = brain([("I'll open Chrome now.", []), ("", [("open_app", {"name": "chrome"})]), ("Opened!", [])])
    out, _ = app.answer("open chrome")
    assert ran == [("open_app", {"name": "chrome"})]


def test_switching_now_without_a_step_gets_a_nudge(brain):
    # Task exam 2026-11-25: "Switching to Calculator now... Calculator is ready." with no tool call passed as done.
    fake, ran = brain([("Switching to Calculator now...", []),
                       ("", [("window_action", {"title": "Calculator", "action": "focus"})]), ("Done!", [])])
    out, _ = app.answer("switch to the calculator")
    step = ran or [(p["tool"], p["input"]) for p in out.get("pending") or []]     # it runs, or it asks first
    assert step == [("window_action", {"title": "Calculator", "action": "focus"})]


def test_turn_limit(brain):
    fake, ran = brain([("", [("get_datetime", {"n": i})]) for i in range(40)])
    out, _ = app.answer("loop forever")
    assert "lot of steps" in out["reply"] and len(ran) <= app.MAX_TASK_TURNS


def test_brain_error_rolls_back_history(brain):
    fake, ran = brain([(app.llm.OllamaError("down"), [])])
    out, status = app.answer("hello")
    assert status == 503 and app.state["messages"] == []


def test_unexpected_error_rolls_back_and_returns_500(brain):
    fake, ran = brain([(RuntimeError("boom"), [])])
    out, status = app.answer("hello")
    assert status == 500 and "error" in out
    assert app.state["messages"] == []
    assert not app.lock.locked()


def test_history_is_capped(brain):
    fake, ran = brain([("ok", [])] * 400)
    for i in range(200):
        app.answer(f"message {i}")
    assert len(app.state["messages"]) <= app.MAX_STORED


def test_trimmed_history_starts_with_user():
    app.state["messages"] = ([{"role": "user", "content": "q"}, {"role": "assistant", "content": "", "tool_calls": [{}]},
                              {"role": "tool", "content": "r"}] * 10)
    h = app.trimmed_history()
    assert h[0]["role"] == "user" and len(h) <= app.MAX_HISTORY


def test_trimmed_history_keeps_task_request():
    app.state["messages"] = [{"role": "user", "content": "the big task"}] + \
        [{"role": "assistant", "content": "", "tool_calls": [{}]}, {"role": "tool", "content": "r"}] * 20
    app.state["task"] = {"start": 0, "steps": 5}
    h = app.trimmed_history()
    assert h[0]["content"] == "the big task" and h[1]["role"] != "tool"


@pytest.mark.parametrize("text,tool", [
    ("what time is it", "get_datetime"), ("abhi kitne baje hain", "get_datetime"),
    ("how much battery do I have", "get_system_status"), ("is my internet working", "network_info"),
])
def test_fact_shortcuts(text, tool):
    assert next(t for rx, t in intents.FACT if rx.search(text)) == tool


@pytest.mark.parametrize("text,action", [
    ("make the video full screen", "fullscreen"), ("exit full screen", "exit_fullscreen"),
    ("turn on subtitles", "captions"), ("skip this video", "next"),
])
def test_video_shortcuts(text, action):
    assert next(a for rx, a in intents.VIDEO if rx.search(text)) == action


@pytest.mark.parametrize("text,yes", [("yes please", True), ("haan kar do", True), ("no thanks", False), ("nahi", False)])
def test_voice_yes_no(text, yes):
    assert bool(intents.YES.search(text)) == yes
    assert bool(intents.NO.search(text)) == (not yes)


def test_speech_stream_hides_window_list():
    said = []
    orig = app.speak
    app.voice_io.speak = said.append
    try:
        s = app.SpeechStream()
        s.feed("Done boss! [open windows, front first: 'secret.txt'] ")
        s.feed("All good.")
        s.flush()
    finally:
        app.voice_io.speak = orig
    assert not any("secret" in x for x in said) and "Done boss!" in said


def test_reaction_rules():
    assert app.pick_reaction("thank you", {"reply": "Anytime!"}) == "bow"
    assert app.pick_reaction("tell me a joke", {"reply": "Why..."}) == "laugh"
    assert app.pick_reaction("x", {"reply": "x", "error": "e"}) == "sad"


def test_review_triggers():
    assert app.why_review("that's wrong", {"reply": "ok"})
    assert app.why_review("my name is Asha", {"reply": "Hi"})
    assert app.why_review("hello", {"reply": "Hi"}) is None


def test_deep_cloud_task_gets_more_steps_and_offers_to_keep_going(brain, monkeypatch):
    fake, ran = brain([("", [("scroll", {"direction": "down"})])] * 80)
    monkeypatch.setattr(app.config, "brain", lambda s=None: "cloud:google:gemini-test")
    s = app.settings()
    s["reasoning"] = "max"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")
    out, _ = app.answer("find my github username and set up the repo")
    assert app.MAX_TASK_TURNS < len(ran) <= app.DEEP_TASK_TURNS
    assert "lot of steps" in out["reply"] and "keep going" in out["reply"]


def test_long_task_does_not_count_as_quiet_time(brain):
    fake, ran = brain([("All done!", [])])
    app.nap["last_task"] = app.time.time() - app.IDLE_NAP - 60     # the ask came in over 5 minutes ago
    app.answer("a long job")
    assert not app.nap_due()                                       # she just finished: no nap straight away


def test_command_only_task_gets_task_steps_not_question_limit(brain, monkeypatch):
    # A job done with commands (git, PowerShell) has no on-screen clicks; it used to stop after 10 turns.
    fake, ran = brain([("", [("get_datetime", {"n": i})]) for i in range(80)])
    monkeypatch.setattr(app.config, "brain", lambda s=None: "cloud:google:gemini-test")
    s = app.settings()
    s["reasoning"] = "max"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")
    out, _ = app.answer("connect the new github branch to my local repo")
    assert app.MAX_TASK_TURNS < len(ran) <= app.DEEP_TASK_TURNS
    assert app.state["task"]["paused"]


def test_keep_going_resumes_the_paused_task(brain):
    fake, ran = brain([("", [("get_datetime", {"n": i})]) for i in range(app.MAX_TASK_TURNS)] + [("All done!", [])])
    app.answer("set up the repo for me")
    assert app.state["task"]["paused"]
    out, _ = app.answer("keep going")
    task = app.state["task"]
    assert task["goal"] == "set up the repo for me" and not task["paused"]
    asked = [m["content"] for m in app.state["messages"] if m["role"] == "user"]
    assert any(c.startswith("keep going") and "carry on" in c for c in asked)
    assert out["reply"] == "All done!"


def test_keep_going_without_a_paused_task_is_a_normal_request(brain):
    fake, ran = brain([("Okay!", []), ("Sure!", [])])
    app.answer("hello")
    app.answer("keep going")
    assert app.state["task"]["goal"] == "keep going"


TEXT = "Line one of the paragraph.\nLine two goes on.\nAnd the last line."


def test_read_selected_text_is_read_out_in_full(brain, monkeypatch):
    fake, ran = brain([], tool_output=TEXT)
    said = []
    monkeypatch.setattr(app.voice_io, "speak", said.append)
    out, _ = app.answer("[spoken; speech recognition may have misheard words] Can you read what is selected on my screen?")
    assert ran == [("read_selection", {})] and not fake.seen      # copied the text; no screenshot, no brain
    assert out["reply"] == TEXT and said == [TEXT]


def test_summarise_selection_hands_the_text_to_the_brain(brain):
    fake, ran = brain([("It's about three lines.", [])], tool_output=TEXT)
    out, _ = app.answer("summarise the selected text")
    assert ran == [("read_selection", {})] and fake.seen and out["reply"] == "It's about three lines."


def test_nothing_selected_goes_to_the_brain(brain):
    fake, ran = brain([("Highlight some text first, boss.", [])], tool_output="Nothing is selected in 'Notepad'.")
    out, _ = app.answer("read the highlighted text")
    assert fake.seen and "Highlight" in out["reply"]


def test_read_selection_restores_the_clipboard(monkeypatch, desktop):
    desktop.set_windows([(1, "Notepad", "notepad.exe")]).clipboard = "my old clipboard"
    monkeypatch.setattr(app.tools.forms, "_target_window", lambda w: (1, "Notepad", "notepad.exe"))
    monkeypatch.setattr(desktop, "press", lambda k: setattr(desktop, "clipboard", "the selected words"))   # ctrl+c
    assert app.tools.read_selection() == "the selected words"
    assert desktop.clipboard == "my old clipboard"
