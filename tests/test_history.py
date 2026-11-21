"""How much conversation each model gets: a local 8K window and a cloud window are not the same budget."""
from nova.brain import llm
from nova.core import app

LOCAL = "qwen3-vl:4b-instruct"
ANTHROPIC = "cloud:anthropic:claude-opus-5-5"


def test_local_models_keep_the_measured_history():
    assert llm.context_size() == llm.NUM_CTX
    assert llm.context_size(LOCAL) == llm.NUM_CTX
    assert llm.context_size("not-a-cloud-model") == llm.NUM_CTX
    assert app.history_limit() == app.MAX_HISTORY
    assert app.history_limit(LOCAL) == app.MAX_HISTORY


def test_cloud_models_get_their_provider_window():
    assert llm.context_size(ANTHROPIC) == llm.CLOUD_CONTEXT["anthropic"]
    assert llm.context_size("cloud:google:gemini-x") == llm.CLOUD_CONTEXT["google"]
    assert llm.context_size("cloud:openai:gpt-x") == llm.CLOUD_CONTEXT["openai"]
    assert llm.context_size("cloud:local-llm:whatever") == llm.DEFAULT_CLOUD_CONTEXT


def test_cloud_context_can_be_overridden(monkeypatch):
    monkeypatch.setenv(llm.CLOUD_CTX_ENV, "32768")
    assert llm.context_size(ANTHROPIC) == 32768
    assert app.history_limit(ANTHROPIC) > app.MAX_HISTORY


def test_cloud_history_is_larger_but_bounded():
    limit = app.history_limit(ANTHROPIC)
    assert app.MAX_HISTORY < limit <= app.CLOUD_HISTORY_MAX


def test_trimmed_history_uses_the_model_budget():
    app.state["messages"] = [{"role": "user", "content": f"q{i}"} for i in range(40)]
    assert len(app.trimmed_history()) <= app.MAX_HISTORY
    assert len(app.trimmed_history(ANTHROPIC)) == 40


def test_trimming_still_starts_at_a_real_user_message():
    app.state["messages"] = ([{"role": "assistant", "content": "hello"},
                              {"role": "user", "content": "q"},
                              {"role": "assistant", "content": "", "tool_calls": [{}]},
                              {"role": "tool", "content": "r"}] * 20)
    h = app.trimmed_history(ANTHROPIC)
    assert h and h[0]["role"] == "user" and h[0]["content"] == "q"
