"""How hard the brain thinks: the Low/High/Max/Auto setting, sent to each cloud provider, with its reasoning shown."""
import types

import app
import cloud
import llm


def _ok(content="Done!", **msg):
    return {"choices": [{"message": dict({"content": content}, **msg)}]}


def test_auto_is_quick_for_chat_and_deep_for_hard_asks():
    s = {"reasoning": "auto"}
    assert app.reasoning_level(s, {"goal": "open notepad", "steps": 0}) == "low"
    assert app.reasoning_level(s, {"goal": "hi nova", "steps": 0}) == "low"
    assert app.reasoning_level(s, {"goal": "explain why my laptop is slow", "steps": 0}) == "high"
    assert app.reasoning_level(s, {"goal": "compare these two phones", "steps": 0}) == "high"
    assert app.reasoning_level(s, {"goal": "open notepad", "steps": 4}) == "high"          # a long task
    assert app.reasoning_level(s, {"goal": "open notepad", "steps": 1, "done": False}) == "high"
    assert app.reasoning_level(s) == "low"


def test_fixed_level_wins():
    assert app.reasoning_level({"reasoning": "max"}, {"goal": "hi", "steps": 0}) == "max"
    assert app.reasoning_level({"reasoning": "low"}, {"goal": "explain why", "steps": 9}) == "low"


def test_gemini_gets_effort_and_its_thoughts_are_shown(monkeypatch):
    sent = []

    def fake_http(method, url, key, body=None, timeout=90):
        sent.append((body, timeout))
        return _ok("<thought>The user wants a plan. First list the steps.</thought>Here is the plan.")

    monkeypatch.setattr(cloud, "_http", fake_http)
    monkeypatch.setattr(cloud, "get_key", lambda p: "k")
    got = []
    _, text, _ = cloud.chat("cloud:google:gemini-4-carbon", [{"role": "user", "content": "plan"}], [],
                            reasoning="high", on_think=got.append)
    body, timeout = sent[0]
    assert body["reasoning_effort"] == "high" and body["max_tokens"] == cloud.LEVEL_TOKENS["high"]
    assert body["extra_body"]["google"]["thinking_config"]["include_thoughts"] is True
    assert timeout == cloud.LEVEL_TIMEOUT["high"]
    assert text == "Here is the plan." and "First list the steps" in got[0]


def test_max_steps_down_when_a_model_has_no_xhigh(monkeypatch):
    sent = []

    def fake_http(method, url, key, body=None, timeout=90):
        sent.append(body.get("reasoning_effort"))
        if body.get("reasoning_effort") == "xhigh":
            raise cloud.CloudError("Error 400: invalid reasoning_effort 'xhigh'")
        return _ok()

    monkeypatch.setattr(cloud, "_http", fake_http)
    monkeypatch.setattr(cloud, "get_key", lambda p: "k")
    cloud.chat("cloud:openai:gpt-test-max", [{"role": "user", "content": "x"}], [], reasoning="max")
    assert sent == ["xhigh", "high"]
    sent.clear()
    cloud.chat("cloud:openai:gpt-test-max", [{"role": "user", "content": "x"}], [], reasoning="max")
    assert sent == ["high"]                     # remembers what worked: no second 400


def test_other_errors_are_not_retried(monkeypatch):
    calls = []

    def fake_http(method, url, key, body=None, timeout=90):
        calls.append(1)
        raise cloud.CloudError("Error 503: overloaded")

    monkeypatch.setattr(cloud, "_http", fake_http)
    monkeypatch.setattr(cloud, "get_key", lambda p: "k")
    try:
        cloud.chat("cloud:google:gemini-x-503", [{"role": "user", "content": "x"}], [], reasoning="high")
    except cloud.CloudError:
        pass
    assert len(calls) == 1


def test_claude_gets_effort_and_summarized_thinking(monkeypatch):
    seen = {}

    class Messages:
        def create(self, **kw):
            seen.update(kw)
            return types.SimpleNamespace(stop_reason="end_turn", content=[
                types.SimpleNamespace(type="thinking", thinking="Weigh both options first."),
                types.SimpleNamespace(type="text", text="Go with the first one.")])

    client = types.SimpleNamespace(messages=Messages(), with_options=lambda **kw: seen.update(opts=kw) or client)
    monkeypatch.setattr(cloud, "_anthropic_client", lambda key: client)
    monkeypatch.setattr(cloud, "get_key", lambda p: "k")
    got = []
    stored, text, _ = cloud.chat("cloud:anthropic:claude-opus-5-5", [{"role": "user", "content": "which?"}], [],
                                 reasoning="max", on_think=got.append)
    assert seen["extra_body"]["output_config"]["effort"] == "max"
    assert seen["extra_body"]["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert seen["max_tokens"] <= 16000 and seen["opts"]["timeout"] == cloud.LEVEL_TIMEOUT["max"]
    assert text == "Go with the first one." and got == ["Weigh both options first."]
    assert "Weigh" not in str(stored)            # thinking is shown, never stored in her history


def test_llm_passes_level_to_the_cloud(monkeypatch):
    seen = {}
    monkeypatch.setattr(cloud, "chat", lambda m, msgs, t, **k: seen.update(k) or ({"role": "assistant"}, "ok", []))
    llm.reset_cloud()
    llm.chat("cloud:google:gemini-x", [], [], reasoning="high", on_think=print)
    assert seen["reasoning"] == "high" and seen["on_think"] is print
