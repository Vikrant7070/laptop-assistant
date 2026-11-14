"""Knowledge bank, cloud history conversion, key storage and the local-model text protocol."""
import cloud
import knowledge
import llm
import tools


# ---------------------------------------------------------------- knowledge bank

def test_profile_add_dedupe_remove():
    assert "Noted" in knowledge.update_profile("interests", "cricket and chai")
    assert "already" in knowledge.update_profile("interests", "Cricket and chai")
    assert "Removed 1" in knowledge.update_profile("interests", "cricket", remove=True)


def test_secrets_never_stored():
    for v in ("my password is hunter2", "PIN 1234", "card 4111111111111111", "api key sk-abc"):
        assert "don't keep" in knowledge.update_profile("facts", v)
    assert knowledge.facts() == []


def test_unknown_field():
    assert "Unknown profile field" in knowledge.update_profile("shoe_size", "9")


def test_lessons_merge_and_cap():
    knowledge.learn_lesson("opening youtube", "Wait for the page to load before clicking")
    assert "already" in knowledge.learn_lesson("opening youtube", "Wait for the page to load before clicking")
    for i in range(knowledge.MAX_LESSONS + 10):
        knowledge.learn_lesson(f"case {i} zebra{i}", f"unique step number {i} quokka{i} walrus{i}")
    assert len(knowledge.load()["lessons"]) <= knowledge.MAX_LESSONS


def test_delete_lesson_bad_id_is_safe():
    assert knowledge.delete_lesson("abc") == 0


def test_forget_and_prompt_block():
    knowledge.update_profile("city", "Pune")
    assert "Pune" in knowledge.prompt_block()
    knowledge.forget("pune")
    assert "Pune" not in knowledge.prompt_block()


def test_corrupt_file_recovers():
    knowledge.FILE.parent.mkdir(parents=True, exist_ok=True)
    knowledge.FILE.write_text("{not json", encoding="utf-8")
    assert knowledge.load()["profile"]["name"] == ""


def test_remember_fact_tool():
    assert "Noted" in tools.remember_fact("likes masala dosa")
    assert "likes masala dosa" in tools.memories()


# ---------------------------------------------------------------- cloud

def test_parse_model_ids():
    assert cloud.parse("cloud:google:gemini-x") == ("google", "gemini-x")
    assert cloud.parse("qwen3-vl:4b") is None
    assert cloud.parse("cloud:broken") is None


def test_key_roundtrip_is_encrypted():
    cloud.set_key("openai", "sk-test-123")
    assert "sk-test-123" not in cloud.KEYS_FILE.read_text()
    assert cloud.get_key("openai") == "sk-test-123"
    cloud.set_key("openai", "")
    assert cloud.get_key("openai") == ""


def test_neutral_pairs_tool_results():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "a", "arguments": {}}},
                                                                {"function": {"name": "b", "arguments": "{}"}}]},
            {"role": "tool", "content": "ra", "tool_name": "a"}, {"role": "user", "content": "next"}]
    system, turns = cloud._neutral(msgs)
    tool_turns = [t for t in turns if t["kind"] == "tool"]
    assert system == "sys" and [t["name"] for t in tool_turns] == ["a", "b"]
    assert tool_turns[1]["content"] == "(no result)"


def test_no_key_raises_cloud_error():
    try:
        cloud.chat("cloud:openai:gpt-x", [], [])
    except cloud.CloudError as e:
        assert "No API key" in str(e)
    else:
        raise AssertionError("expected CloudError")


def test_cloud_failure_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(cloud, "chat", lambda *a, **k: (_ for _ in ()).throw(cloud.CloudError("429")))
    monkeypatch.setattr(llm, "supports_tools", lambda m: False)
    monkeypatch.setattr(llm, "capabilities", lambda m: set())
    monkeypatch.setattr(llm, "_run", lambda path, body: {"message": {"content": "local says hi"}})
    stored, text, calls = llm.chat("cloud:openai:gpt-x", [], [], fallback="qwen")
    assert text == "local says hi" and llm.LAST["fallback"] == "429"


# ---------------------------------------------------------------- local text protocol

def test_parse_text_call():
    names = {"get_datetime", "open_app"}
    assert llm._parse_text_call('sure {"tool": "open_app", "args": {"name": "x"}}', names) == ("open_app", {"name": "x"})
    assert llm._parse_text_call('{"tool": "rm_rf", "args": {}}', names) is None
    assert llm._parse_text_call("no json {here", names) is None


def test_think_tags_removed():
    assert llm.clean("<think>hmm</think>Hello") == "Hello"


def test_tool_result_truncated(monkeypatch):
    monkeypatch.setattr(llm, "supports_tools", lambda m: True)
    m = llm.tool_result_message("x", {"name": "t"}, "a" * 5000)
    assert len(m["content"]) < 1700


def test_contact_details_never_stored():
    for v in ("Email: someone@example.com", "my number is +91 98765 43210", "call 9876543210"):
        assert "don't keep" in knowledge.update_profile("facts", v), v
    assert "Noted" in knowledge.update_profile("facts", "Birthday is on 2026-11-14")   # dates are fine
    assert "don't keep" in knowledge.learn_lesson("mailing", "Send it to a@b.co")
