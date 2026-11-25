"""QA plan P2-a: a failed task's transcript doesn't steer the next request (QA 2026-11-25: after a failed folder
step, four unrelated requests kept talking about the missing todo.txt)."""
import json

from nova import store
from nova.core import app


def _trusted():
    s = app.settings()
    s["autonomy"] = "trusted"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


def _fail_a_folder(brain, tmp_path):
    """A request whose step fails its read-back twice: the task ends not done, with tool calls in the history."""
    _trusted()
    play = tmp_path / "play"
    play.mkdir(parents=True)
    brain([("", [("create_folder", {"path": str(play), "name": "qa-notes"})]), ("Made it!", []),
           ("Made it, really!", [])])                       # the fake tool makes nothing: the read-back fails
    out, _ = app.answer(f"make a folder called qa-notes in {play}")
    assert app.state["task"]["done"] is False
    return play


def test_after_a_failed_task_the_next_request_sees_one_line_not_the_transcript(brain, tmp_path):
    play = _fail_a_folder(brain, tmp_path)
    before = len(app.state["messages"])
    seen = []
    fake, _ = brain([("It has big.bin and small.txt.", [])])
    fake_chat = app.llm.chat

    def spy(model, msgs, *a, **k):
        seen.append([dict(m) for m in msgs])
        return fake_chat(model, msgs, *a, **k)
    app.llm.chat = spy
    try:
        app.answer(f"what's in {play}?")
    finally:
        app.llm.chat = fake_chat
    sent = seen[0]
    users = [m["content"] for m in sent if m["role"] == "user"]
    assert any("make a folder called qa-notes" in u for u in users)            # the request is kept (its path too)
    notes = [m["content"] for m in sent if m["role"] == "assistant" and "I didn't finish that" in str(m["content"])]
    assert len(notes) == 1
    assert not any(m.get("tool_calls") or m["role"] == "tool" for m in sent[:-1]
                   if "qa-notes" not in str(m.get("content")))               # no tool steps from the failed task
    assert len(app.state["messages"]) < before + 3


def test_try_again_and_a_correction_keep_the_whole_story(brain, tmp_path):
    for follow_up in ("try again", "no, I meant the other folder"):
        app.state["messages"].clear()
        app.state.pop("task", None)
        _fail_a_folder(brain, tmp_path / follow_up.replace(" ", "_").replace(",", ""))
        kept = len(app.state["messages"])
        brain([("On it.", [])])
        app.answer(follow_up)
        assert len(app.state["messages"]) > kept                              # nothing was folded away
        assert not any("I didn't finish that" in str(m.get("content")) for m in app.state["messages"])


def test_a_task_that_worked_is_left_alone(brain, tmp_path):
    _trusted()
    (tmp_path / "fine").mkdir()                                  # what the (faked) step made: the read-back passes
    brain([("", [("create_folder", {"path": str(tmp_path), "name": "fine"})]), ("Made it!", [])])
    app.answer(f"make a folder called fine in {tmp_path}")
    assert app.state["task"]["done"] is not False
    kept = len(app.state["messages"])
    brain([("Hello!", [])])
    app.answer("hi")
    assert not any("I didn't finish that" in str(m.get("content")) for m in app.state["messages"])
    assert len(app.state["messages"]) == kept + 2
