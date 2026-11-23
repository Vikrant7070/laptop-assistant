"""Local first, cloud when stuck (nova/core/app/brains.py, docs/plans/local-first-brain.md Part B)."""
import pytest

from nova import store
from nova.brain import cloud, retrieval, router, usage
from nova.core import app

CLOUD = "cloud:google:gemini-3.8-flash"
DAILY_429 = "Quota exceeded for metric: free_tier_requests per day, limit: 250. Please retry in 33s."


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(retrieval, "ready", lambda: False)
    monkeypatch.setattr(router, "installed", lambda m: True)
    monkeypatch.setattr(router.llm, "supports_tools", lambda m: True)
    cloud.set_key("google", "test-key")
    app.save_settings({**app.settings(), "provider": "google", "cloud_models": {"google": "gemini-3.8-flash"},
                       "brain_mode": "local_first"})
    yield
    cloud.set_key("google", "")


def local():
    return app.settings()["model"]


def recorded(brain, script):
    fake, ran = brain(script)
    models = []

    def call(model, messages, tools_, **kw):
        models.append(model)
        return fake(model, messages, tools_, **kw)
    app.llm.chat = call
    return models, ran


def log():
    return store.current().task_log.read_text(encoding="utf-8")


def test_local_first_starts_every_request_on_the_local_brain(brain):
    models, _ = recorded(brain, [("Hi!", [])])
    app.answer("hello there")
    assert models == [local()] and app.config.brain() == local()


def test_cloud_first_is_unchanged(brain):
    app.save_settings({**app.settings(), "brain_mode": "cloud_first"})
    models, _ = recorded(brain, [("Hi!", [])])
    app.answer("hello there")
    assert models == [CLOUD]


def test_asking_for_gemini_starts_the_task_on_the_cloud(brain):
    models, _ = recorded(brain, [("Here's a summary.", [])])
    app.answer("ask gemini to summarise the history of rome")
    assert models == [CLOUD]
    assert "CLIMB -> cloud:google:gemini-3.8-flash: you asked for it" in log()


def test_escalate_cloud_hands_over_and_the_next_request_starts_local_again(brain):
    models, ran = recorded(brain, [("", [("escalate", {"kind": "cloud", "why": "I'm lost"})]), ("Done.", []), ("Hi!", [])])
    app.answer("please help me with this")
    app.answer("hello there")
    assert models == [local(), CLOUD, local()]
    assert all(n != "escalate" for n, _ in ran)


def test_escalate_cloud_in_cloud_first_mode_has_nowhere_to_go(brain):
    app.save_settings({**app.settings(), "provider": "ollama", "brain_mode": "cloud_first"})
    models, _ = recorded(brain, [("", [("escalate", {"kind": "cloud"})]), ("Ok.", [])])
    app.answer("please help me with this")
    assert models == [local(), local()]


def test_a_failed_local_plan_climbs_to_the_cloud(brain, monkeypatch):
    monkeypatch.setattr(router, "make_plan", lambda s, goal, on_think=None: (None, "no plan came back"))
    models, _ = recorded(brain, [("Here is how.", [])])
    app.answer("make a plan to organize my downloads step by step")
    assert models == [CLOUD]
    assert "couldn't write a plan" in log()


def test_the_same_step_three_times_climbs(brain):
    same = ("", [("look_up", {"q": "x"})])
    models, _ = recorded(brain, [same, same, same, ("Found it.", [])])
    out, _ = app.answer("find the thing for me")
    assert models == [local(), local(), local(), CLOUD]
    assert out["reply"] == "Found it."


def test_too_many_turns_climbs(brain):
    app.save_settings({**app.settings(), "cloud_after_turns": 2})
    models, _ = recorded(brain, [("", [("look_up", {"n": 1})]), ("", [("look_up", {"n": 2})]), ("Done.", [])])
    app.answer("find the thing for me")
    assert models == [local(), local(), CLOUD]


def test_saying_it_cant_after_using_tools_climbs(brain):
    models, _ = recorded(brain, [("", [("look_up", {"q": "x"})]), ("Sorry, I can't do that.", []), ("Done it.", [])])
    out, _ = app.answer("find the thing for me")
    assert models == [local(), local(), CLOUD]
    assert out["reply"] == "Done it."


def test_plain_chat_never_climbs_by_itself(brain):
    models, _ = recorded(brain, [("I can't taste food, but pizza sounds great!", [])])
    app.answer("do you like pizza")
    assert models == [local()]


def test_used_up_free_uses_keep_the_task_local_and_say_so(brain):
    usage.hit_limit("google", "gemini-3.8-flash", DAILY_429)
    models, _ = recorded(brain, [("", [("escalate", {"kind": "cloud"})]), ("I'll do my best.", [])])
    app.answer("please help me with this")
    assert models == [local(), local()]
    assert "CLIMB refused" in log() and "used up" in log()


def test_mail_never_climbs_unless_allowed(brain):
    models, _ = recorded(brain, [("Here are your emails.", [])])
    app.answer("ask gemini to read my latest emails")
    assert models == [local()]
    assert "mail stays on this laptop" in log()


def test_a_task_climbs_only_once(brain):
    same = ("", [("look_up", {"q": "x"})])
    models, _ = recorded(brain, [("", [("escalate", {"kind": "cloud"})]), same, same, same, ("Ok.", [])])
    app.answer("find the thing for me")
    assert models[1:] == [CLOUD] * 4
    assert log().count("CLIMB ->") == 1


def test_brain_mode_is_saved_from_the_brain_tab(client):
    r = client.post("/api/setup", json={"brain_mode": "cloud_first"})
    assert r.status_code == 200 and app.settings()["brain_mode"] == "cloud_first"
    client.post("/api/setup", json={"brain_mode": "everything_everywhere"})
    assert app.settings()["brain_mode"] == "cloud_first"               # unknown values are ignored
    assert app.brains.ready_info()["brain_mode"] == "cloud_first"


def test_a_climb_at_answer_time_never_sends_a_history_ending_with_her_own_reply(brain):
    # Exam 2026-11-23: Gemini answered 400 "Requests ending with a model turn are not supported".
    fake, _ = brain([("", [("look_up", {"q": "x"})]), ("Sorry, I can't do that.", []), ("Done it.", [])])
    last_roles = []

    def call(model, messages, tools_, **kw):
        last_roles.append((model, messages[-1]["role"]))
        return fake(model, messages, tools_, **kw)
    app.llm.chat = call
    app.answer("find the thing for me")
    assert last_roles[-1] == (CLOUD, "user")


def test_a_failed_cloud_call_after_a_climb_hands_back_to_the_local_brain(brain):
    # Exam 2026-11-23: the cloud failed, the local fallback got the cloud-sized prompt and Ollama refused it.
    models, _ = recorded(brain, [("", [("look_up", {"q": "x"})]), ("Sorry, I can't do that.", []),
                                 (app.llm.OllamaError("Cloud error 400: nope"), []), ("I did what I could.", [])])
    out, status = app.answer("find the thing for me")
    assert status == 200 and models == [local(), local(), CLOUD, local()]
    assert out["reply"] == "I did what I could."
    assert "CLOUD BRAIN NOT USED (cloud:google:gemini-3.8-flash)" in log()
