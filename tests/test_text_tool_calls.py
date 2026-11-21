"""A model with native tool calling (qwen2.5-coder) sometimes writes its call as JSON text: it becomes a real call
and is never spoken; ordinary replies still stream as before."""
import pytest

from nova.brain import llm

TOOLS = [{"name": "read_page", "description": "d", "input_schema": {"type": "object", "properties": {}, "required": []}}]


@pytest.fixture
def ollama(monkeypatch):
    monkeypatch.setattr(llm, "supports_tools", lambda m: True)
    monkeypatch.setattr(llm, "capabilities", lambda m: ["tools"])

    def install(pieces):
        def stream(path, body, on_text, timeout=600, on_think=None):
            for p in pieces:
                on_text(p)
            return {"message": {"content": "".join(pieces)}}
        monkeypatch.setattr(llm, "_post_stream", stream)
    return install


def test_json_written_as_text_becomes_a_real_call_and_is_not_spoken(ollama):
    ollama(["```json\n", '{"name": "read_page", ', '"arguments": {"window": "Chrome"}}\n```'])
    said = []
    stored, text, calls = llm.chat("qwen2.5-coder:7b", [], TOOLS, on_text=said.append)
    assert calls == [{"id": "call_0", "name": "read_page", "args": {"window": "Chrome"}}]
    assert text == "" and said == [] and stored["tool_calls"]


def test_old_tool_args_shape_works_too(ollama):
    ollama(['{"tool": "read_page", "args": {}}'])
    _, _, calls = llm.chat("m", [], TOOLS, on_text=lambda p: None)
    assert calls[0]["name"] == "read_page"


def test_ordinary_reply_streams_piece_by_piece(ollama):
    ollama(["Arey ", "boss, ", "done!"])
    said = []
    _, text, calls = llm.chat("m", [], TOOLS, on_text=said.append)
    assert said == ["Arey ", "boss, ", "done!"] and text == "Arey boss, done!" and calls == []


def test_text_that_only_looks_like_code_is_still_said_once(ollama):
    ollama(["`ls`", " lists your files."])
    said = []
    _, text, calls = llm.chat("m", [], TOOLS, on_text=said.append)
    assert "".join(said) == "`ls` lists your files." and calls == []


def test_unknown_tool_with_no_words_is_left_as_text(ollama):
    ollama(['{"name": "launch_rockets", "arguments": {}}'])
    said = []
    _, text, calls = llm.chat("m", [], TOOLS, on_text=said.append)
    assert calls == [] and "".join(said) == text


# ---- a model stuck calling the same tool again and again ----
from nova.core import app  # noqa: E402


def test_same_call_over_and_over_gets_a_nudge_then_stops(brain):
    fake, ran = brain([("", [("get_datetime", {})])] * 30)
    out, _ = app.answer("help me with my week")
    assert len(ran) == app.REPEAT_LIMIT                       # run 3 times, the 4th is refused
    notes = [m["content"] for m in app.state["messages"] if m["role"] == "user" and "called get_datetime" in m["content"]]
    assert len(notes) == 1
    assert "repeating the same step" in out["reply"]


def test_nudge_works_when_she_then_answers(brain):
    fake, ran = brain([("", [("get_datetime", {})])] * 3 + [("It's Tuesday, boss.", [])])
    out, _ = app.answer("help me with my week")
    assert len(ran) == 3 and out["reply"] == "It's Tuesday, boss."


def test_different_calls_are_not_a_loop(brain):
    script = [("", [("get_datetime", {})]), ("", [("get_system_status", {})])] * 3 + [("All good.", [])]
    fake, ran = brain(script)
    out, _ = app.answer("check the time and the laptop a few times")
    assert len(ran) == 6 and out["reply"] == "All good."


def test_same_call_with_a_new_result_each_time_is_not_a_loop(brain, monkeypatch):
    fake, ran = brain([("", [("get_datetime", {})])] * 5 + [("Done.", [])])
    n = iter(range(100))
    monkeypatch.setattr(app.tools, "run_tool", lambda name, a: (ran.append(name), f"10:0{next(n)}")[1])
    out, _ = app.answer("help me with my week")
    assert len(ran) == 5 and out["reply"] == "Done."


def test_scrolling_again_and_again_is_fine(brain):
    fake, ran = brain([("", [("scroll", {"direction": "down"})])] * 6 + [("Found it.", [])])
    out, _ = app.answer("scroll until you find the comments")
    assert len(ran) == 6 and out["reply"] == "Found it."


def test_answer_dressed_as_a_call_to_a_made_up_tool_is_said_as_words(ollama):
    ollama(["```json\n", '{"name": "print", "arguments": {"text": "You can\'t subtract a number from a string."}}', "\n```"])
    said = []
    _, text, calls = llm.chat("qwen2.5-coder:7b", [], TOOLS, on_text=said.append)
    assert calls == [] and text == "You can't subtract a number from a string." and said == [text]


def test_same_result_with_a_different_step_note_is_still_a_loop(brain, monkeypatch):
    """Live bug: qwen2.5-coder ran `python --version` 24 times; each result ended in a new "[check after step N]"."""
    fake, ran = brain([("", [("get_system_status", {})])] * 30)
    n = iter(range(1, 100))
    monkeypatch.setattr(app.judging, "execute", lambda calls, approved, activity, model: (
        ran.append(calls[0]["name"]),
        app.state.update(last_outs=[f"Python 3.12.10\n[check after step {next(n)}] Windows now: none."]),
        [app.llm.tool_result_message(model, calls[0], app.state["last_outs"][0])])[2])
    out, _ = app.answer("help me with my week")
    assert len(ran) == app.REPEAT_LIMIT and "repeating the same step" in out["reply"]


def test_call_in_a_json_block_after_some_words_becomes_a_real_call(ollama):
    """Live: "Let me try a different approach: ```json {"name": "run_in_terminal", ...}```" was left as the reply."""
    tools_ = TOOLS + [{"name": "run_in_terminal", "description": "d",
                       "input_schema": {"type": "object", "properties": {}, "required": []}}]
    ollama(["Let me try a different approach:\n\n", '```json\n{"name": "run_in_terminal", ',
            '"arguments": {"command": "where python"}}\n```'])
    _, text, calls = llm.chat("qwen2.5-coder:7b", [], tools_, on_text=lambda p: None)
    assert calls[0]["name"] == "run_in_terminal" and calls[0]["args"] == {"command": "where python"}
    assert text == "Let me try a different approach:"


def test_a_json_example_for_a_made_up_name_after_words_stays_text(ollama):
    ollama(['Here is the shape:\n```json\n{"name": "example_shape", "arguments": {}}\n```'])
    _, text, calls = llm.chat("m", [], TOOLS, on_text=lambda p: None)
    assert calls == [] and "example_shape" in text


TERMINAL = [{"name": "run_in_terminal", "description": "d",
             "input_schema": {"type": "object", "required": ["command"],
                              "properties": {"command": {"type": "string"}, "folder": {"type": "string"},
                                             "show": {"type": "boolean"}}}}]


def test_call_written_as_python_code_becomes_a_real_call(ollama):
    """Live: "Sure! Let's check. ```python\nrun_in_terminal("python --version")\n```" - nothing ran."""
    ollama(["Sure! Let's check the Python version. \n\n", '```python\nrun_in_terminal("python --version")\n```'])
    _, text, calls = llm.chat("qwen2.5-coder:7b", [], TERMINAL, on_text=lambda p: None)
    assert calls[0]["name"] == "run_in_terminal" and calls[0]["args"] == {"command": "python --version"}
    assert text == "Sure! Let's check the Python version."


def test_code_call_with_keywords(ollama):
    ollama(['```\nrun_in_terminal(command="dir", show=True)\n```'])
    _, _, calls = llm.chat("m", [], TERMINAL, on_text=lambda p: None)
    assert calls[0]["args"] == {"command": "dir", "show": True}


def test_ordinary_code_in_a_reply_is_not_a_call(ollama):
    ollama(["Fix it like this:\n```python\nprint('hi' + '1')\n```"])
    _, text, calls = llm.chat("m", [], TERMINAL, on_text=lambda p: None)
    assert calls == [] and "print('hi' + '1')" in text


def test_code_call_with_unknown_argument_is_left_alone(ollama):
    ollama(['```python\nrun_in_terminal(cmd=os.getcwd())\n```'])
    _, _, calls = llm.chat("m", [], TERMINAL, on_text=lambda p: None)
    assert calls == []


# ---- "Sure! Let's run the command:" before a step isn't glued onto the real answer ----
def test_lead_in_before_a_step_is_left_out_of_the_answer(brain):
    fake, ran = brain([("Sure! Let's check the time:", [("get_system_status", {})]),
                       ("Ah! The laptop is fine, boss.", [])])
    out, _ = app.answer("help me with my week")
    assert out["reply"] == "Ah! The laptop is fine, boss." and ran


def test_lead_in_is_kept_when_no_answer_follows(brain):
    fake, ran = brain([("Checking your laptop now.", [("get_system_status", {})]), ("", [])])
    out, _ = app.answer("help me with my week")
    assert out["reply"] == "Checking your laptop now."
