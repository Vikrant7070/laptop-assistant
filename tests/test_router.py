"""Which local brain does a task: everyday (small), coding, or deep planning (brain/router.py)."""
import pytest

from nova import store
from nova.core import app
from nova.brain import router, retrieval


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(retrieval, "ready", lambda: False)
    monkeypatch.setattr(router, "installed", lambda m: True)
    monkeypatch.setattr(router.llm, "supports_tools", lambda m: True)


def recorded(brain, script):
    fake, ran = brain(script)
    models, tool_sets = [], []

    def call(model, messages, tools_, **kw):
        models.append(model)
        tool_sets.append([t["name"] for t in tools_])
        return fake(model, messages, tools_, **kw)
    app.llm.chat = call
    return models, tool_sets, ran


def test_wording_helpers():
    assert router.code_wanted("fix this python bug in my script")
    assert not router.code_wanted("what's the battery")
    assert router.deep_wanted("make a plan to organize my files step by step")
    assert not router.deep_wanted("open chrome")


def test_code_request_switches_to_the_coder(brain):
    models, _, _ = recorded(brain, [("Fixed it.", [])])
    app.answer("debug my python script")
    assert models == ["qwen2.5-coder:7b"]


def test_next_request_goes_back_to_the_everyday_brain(brain):
    models, _, _ = recorded(brain, [("Fixed it.", []), ("Hi!", [])])
    app.answer("debug my python script")
    app.answer("hello there")
    assert models == ["qwen2.5-coder:7b", app.settings()["model"]]


def test_deep_request_gets_a_plan_and_the_everyday_brain_follows_it(brain, monkeypatch):
    monkeypatch.setattr(router, "make_plan", lambda s, goal, on_think=None: ("1. Open the folder", "deepseek-r1:8b"))
    seen = []
    fake, ran = brain([("Done, boss.", [])])

    def call(model, messages, tools_, **kw):
        seen.append((model, messages[0]["content"]))
        return fake(model, messages, tools_, **kw)
    app.llm.chat = call
    app.answer("make a plan to organize my downloads step by step")
    model, system = seen[0]
    assert model == app.settings()["model"]                 # the plan is followed by the everyday brain
    assert "1. Open the folder" in system


def test_deep_model_that_ignores_tools_never_runs_its_plan(brain, monkeypatch):
    monkeypatch.setattr(router, "make_plan", lambda s, goal, on_think=None: ("1. Do it", "deepseek-r1:8b"))
    monkeypatch.setattr(router, "_probe", {})
    app.save_settings({**app.settings(), "deep_runs": True})
    models, _, _ = recorded(brain, [("Done.", [])])          # the fake brain never calls a tool for the probe
    app.answer("make a plan to organize my downloads step by step")
    assert models[-1] == app.settings()["model"]


def test_deep_model_runs_its_own_plan_only_when_allowed(brain, monkeypatch):
    deep = router.DEFAULTS["deep_model"]
    monkeypatch.setattr(router, "_probe", {deep: True})
    monkeypatch.setattr(router, "make_plan", lambda s, goal, on_think=None: ("1. Do it", deep))
    app.save_settings({**app.settings(), "deep_runs": True})
    models, _, _ = recorded(brain, [("Done.", [])])
    app.answer("make a plan to organize my downloads step by step")
    assert models == [deep]


def test_escalate_tool_switches_mid_task(brain):
    models, _, ran = recorded(brain, [("", [("escalate", {"kind": "code", "why": "needs a script"})]), ("Done.", [])])
    app.answer("please help me with this")
    assert models[1] == "qwen2.5-coder:7b"
    assert all(n != "escalate" for n, _ in ran)          # escalate itself never reaches tools.run_tool


def test_missing_plan_falls_back_to_the_everyday_brain(brain, monkeypatch):
    monkeypatch.setattr(router, "make_plan", lambda s, goal, on_think=None: (None, "deepseek-r1:8b isn't installed"))
    models, _, _ = recorded(brain, [("Ok.", [])])
    out, status = app.answer("make a plan to organize my downloads step by step")
    assert status == 200 and models == [app.settings()["model"]]


def test_routing_off_means_no_switch_and_no_escalate_tool(brain):
    app.save_settings({**app.settings(), "auto_route": False})
    models, tool_sets, _ = recorded(brain, [("Fixed it.", [])])
    app.answer("debug my python script")
    assert models == [app.settings()["model"]] and "escalate" not in tool_sets[0]


def test_not_installed_coder_is_skipped(brain, monkeypatch):
    monkeypatch.setattr(router, "installed", lambda m: m == app.settings()["model"])
    models, _, _ = recorded(brain, [("Fixed it.", [])])
    app.answer("debug my python script")
    assert models == [app.settings()["model"]]


def test_planner_gets_room_to_think_and_its_own_sampling(monkeypatch):
    seen = {}

    def chat(model, messages, tools_, **kw):
        seen.update(kw, model=model)
        return {}, "1. Open Downloads\n2. Sort by type", []
    monkeypatch.setattr(router.llm, "chat", chat)
    plan, model = router.make_plan(app.settings(), "organize my downloads step by step")
    assert plan.startswith("1. Open Downloads") and model == "qwen3.5:4b" == seen["model"]
    assert seen["think"] is True and seen["own_sampling"] is True
    assert seen["num_predict"] == router.PLAN_PREDICT >= 3000      # 900 ran out while the model was still thinking


FAKE_FOLDERS = {"Home": r"C:\Users\vikra", "Desktop": r"C:\Users\vikra\OneDrive\Desktop",
                "Documents": r"C:\Users\vikra\OneDrive\Documents", "Downloads": r"C:\Users\vikra\Downloads"}


def test_planner_is_told_the_real_folders(monkeypatch):
    monkeypatch.setattr(router.shared, "_folders", dict(FAKE_FOLDERS))
    sent = []
    monkeypatch.setattr(router.llm, "chat", lambda m, msgs, t, **kw: (sent.append(msgs[0]["content"]), ({}, "1. a", []))[1])
    router.make_plan(app.settings(), "find the biggest folders in my downloads step by step")
    assert r"Downloads = C:\Users\vikra\Downloads" in sent[0] and r"Desktop = C:\Users\vikra\OneDrive\Desktop" in sent[0]


def test_every_brain_is_told_the_real_folders(monkeypatch):
    from nova.core.app import prompts
    monkeypatch.setattr(router.shared, "_folders", dict(FAKE_FOLDERS))
    for model in ("qwen3-vl:4b-instruct", "cloud:google:gemini-2.5-flash"):
        text = prompts.system_prompt(model)
        assert r"Downloads = C:\Users\vikra\Downloads" in text and "never build a path from the user's name" in text


def test_folders_come_from_windows_with_a_home_fallback(monkeypatch):
    from nova import platform
    fake = platform.FakeDesktop([])
    fake.folders = {"Desktop": r"C:\Users\vikra\OneDrive\Desktop"}      # Documents/Downloads: Windows can't say
    monkeypatch.setattr(platform, "current", lambda: fake)
    monkeypatch.setattr(router.shared, "_folders", {})
    got = router.shared.user_folders()
    assert got["Desktop"] == r"C:\Users\vikra\OneDrive\Desktop"
    assert set(got) == {"Home", "Desktop", "Documents", "Downloads"}
    assert got["Downloads"].endswith("Downloads") and got["Downloads"].startswith(got["Home"])


def test_own_sampling_leaves_temperature_and_repeat_penalty_to_the_model(monkeypatch):
    bodies = []
    monkeypatch.setattr(router.llm, "capabilities", lambda m: ["completion", "thinking"])
    monkeypatch.setattr(router.llm, "_run", lambda path, body: (bodies.append(body), {"message": {"content": "ok"}})[1])
    router.llm.chat("qwen3.5:4b", [{"role": "user", "content": "plan"}], [], think=True, own_sampling=True)
    router.llm.chat("qwen3-vl:4b-instruct", [{"role": "user", "content": "hi"}], [])
    assert "temperature" not in bodies[0]["options"] and "repeat_penalty" not in bodies[0]["options"]
    assert bodies[0]["options"]["num_ctx"] and bodies[0]["think"] is True
    assert bodies[1]["options"]["temperature"] == 0.6 and bodies[1]["options"]["repeat_penalty"] == 1.15


def test_committed_routing_file_names_the_new_planner():
    import json
    from pathlib import Path
    conf = json.loads((Path(__file__).parent.parent / "config" / "model_routing.json").read_text(encoding="utf-8"))
    assert conf["deep_model"] == router.DEFAULTS["deep_model"] == "qwen3.5:4b"


LONG_PLAN = "\n".join(f"{i}. step {i}" for i in range(1, 9))


def test_step_count():
    assert router.step_count(LONG_PLAN) == 8
    assert router.step_count("just words") == 0


def test_long_plan_goes_to_the_stronger_tool_brain(brain, monkeypatch):
    monkeypatch.setattr(router, "make_plan", lambda s, goal, on_think=None: (LONG_PLAN, "deepseek-r1:8b"))
    models, _, _ = recorded(brain, [("Done.", [])])
    app.answer("make a plan to organize my downloads step by step")
    assert models == ["qwen3:8b"]


def test_short_plan_stays_on_the_everyday_brain(brain, monkeypatch):
    monkeypatch.setattr(router, "make_plan", lambda s, goal, on_think=None: ("1. a\n2. b\n3. c", "deepseek-r1:8b"))
    models, _, _ = recorded(brain, [("Done.", [])])
    app.answer("make a plan to organize my downloads step by step")
    assert models == [app.settings()["model"]]


def test_threshold_is_a_setting(brain, monkeypatch):
    monkeypatch.setattr(router, "make_plan", lambda s, goal, on_think=None: ("1. a\n2. b\n3. c", "deepseek-r1:8b"))
    app.save_settings({**app.settings(), "heavy_steps": 2})
    models, _, _ = recorded(brain, [("Done.", [])])
    app.answer("make a plan to organize my downloads step by step")
    assert models == ["qwen3:8b"]


def test_big_local_brain_skips_the_per_step_screen_look():
    assert app._big_brain("qwen3:8b") and app._big_brain("cloud:google:x")
    assert not app._big_brain(app.settings()["model"])


def test_plan_is_given_as_advice_not_orders(brain, monkeypatch):
    monkeypatch.setattr(router, "make_plan", lambda s, goal, on_think=None: ("1. Open the folder", "deepseek-r1:8b"))
    seen = []
    fake, _ = brain([("Done.", [])])

    def call(model, messages, tools_, **kw):
        seen.append(messages[0]["content"])
        return fake(model, messages, tools_, **kw)
    app.llm.chat = call
    app.answer("make a plan to organize my downloads step by step")
    assert "advice from another brain" in seen[0] and "never delete" in seen[0]


def write_conf(**kw):
    import json
    f = router._file()
    router._load()                                           # makes the default file
    f.write_text(json.dumps(kw), encoding="utf-8")
    router._cache["key"] = None


def test_file_is_created_with_defaults_and_help():
    router._load()
    import json
    conf = json.loads(router._file().read_text(encoding="utf-8"))
    assert conf["heavy_steps"] == 6 and "_help" in conf and "how_it_flows" in conf["_help"]


def test_editing_the_file_changes_routing_without_restart(brain):
    write_conf(code_triggers=["banana"], coder_model="my-coder:1b")
    assert router.code_wanted("a banana please") and not router.code_wanted("fix my python bug")
    assert router.options(app.settings())["coder_model"] == "my-coder:1b"


def test_threshold_and_length_come_from_the_file():
    write_conf(heavy_steps=2, long_request_chars=20)
    assert router.options(app.settings())["heavy_steps"] == 2
    assert router.deep_wanted("this request is longer than twenty characters")


def test_broken_file_or_regex_never_stops_nova():
    router._load()
    router._file().write_text("{ not json", encoding="utf-8")
    assert router.code_wanted("debug my script")             # defaults apply
    write_conf(code_triggers=["(unclosed", "script"])
    assert router.code_wanted("a script")


def test_settings_json_overrides_the_file():
    write_conf(heavy_steps=2)
    assert router.options({**app.settings(), "heavy_steps": 9})["heavy_steps"] == 9


def test_model_dialog_saves_routing_into_the_file(client):
    r = client.post("/api/setup", json={"routing": {"auto_route": False, "heavy_steps": 9, "coder_model": "x-coder:1b",
                                                    "bogus": 1}})
    j = r.get_json()
    assert j["routing"]["auto_route"] is False and j["routing"]["heavy_steps"] == 9 and j["routing"]["coder_model"] == "x-coder:1b"
    import json
    saved = json.loads(router._file().read_text(encoding="utf-8"))
    assert saved["heavy_steps"] == 9 and "bogus" not in saved and "code_triggers" in saved   # the rest of the file is kept


def test_dialog_choice_beats_an_old_settings_json_copy(client):
    app.save_settings({**app.settings(), "heavy_steps": 2})
    client.post("/api/setup", json={"routing": {"heavy_steps": 7}})
    assert router.options(app.settings())["heavy_steps"] == 7


def test_borrowed_model_leaves_the_gpu_soon_but_the_everyday_one_stays(brain):
    fake, _ = brain([("Fixed it.", []), ("Hi!", [])])
    kept = []

    def call(model, messages, tools_, **kw):
        kept.append((model, kw.get("keep_alive")))
        return fake(model, messages, tools_, **kw)
    app.llm.chat = call
    app.answer("debug my python script")
    app.answer("hello there")
    assert kept[0] == ("qwen2.5-coder:7b", router.BORROW_KEEP)
    assert kept[1] == (app.settings()["model"], None)


def test_explicit_high_reasoning_makes_a_local_model_think_but_auto_does_not(brain):
    seen = []
    fake, _ = brain([("ok", []), ("ok", [])])

    def call(model, messages, tools_, **kw):
        seen.append(kw.get("think"))
        return fake(model, messages, tools_, **kw)
    app.llm.chat = call
    app.save_settings({**app.settings(), "reasoning": "auto", "auto_route": False})
    app.answer("explain why the sky is blue and compare it with a sunset")     # a "hard" question, but auto
    app.save_settings({**app.settings(), "reasoning": "high"})
    app.answer("hi")
    assert seen == [False, True]


def test_a_planned_task_gets_the_long_step_budget(brain, monkeypatch):
    monkeypatch.setattr(router, "make_plan", lambda s, goal, on_think=None: ("1. a\n2. b", "deepseek-r1:8b"))
    steps = [("", [("get_datetime", {"n": i})]) for i in range(30)] + [("Done.", [])]
    models, _, ran = recorded(brain, steps)
    app.answer("make a plan to check the time step by step")
    assert len(ran) > app.MAX_TASK_TURNS - 2               # not cut off at the ordinary task limit


# ---- the cloud brain fails: the rest of the task runs locally, with the local plan / coder help ----
CLOUD = "cloud:google:gemini-test"


def cloud_that_fails(brain, monkeypatch, script):
    """The chosen brain is a cloud one that always fails, like llm.chat: with a fallback the turn runs on that local
    model (models shows it as the cloud id, the way llm.chat is called), without one it raises."""
    monkeypatch.setattr(app.config, "brain", lambda s=None: CLOUD)
    monkeypatch.setattr(app.llm, "_cloud_down", {"until": 0.0, "why": ""})
    fake, ran = brain(script)
    models = []

    def call(model, messages, tools_, **kw):
        models.append(model)
        if model == CLOUD and not kw.get("fallback"):
            raise app.llm.OllamaError("no internet")
        out = fake(model, messages, tools_, **kw)
        app.llm.LAST["fallback"] = "no internet" if model == CLOUD else None
        return out
    app.llm.chat = call
    return models, fake


def test_cloud_fails_on_a_plan_request_then_the_local_plan_comes_first(brain, monkeypatch):
    plans = []
    monkeypatch.setattr(router, "make_plan",
                        lambda s, goal, on_think=None: (plans.append(goal), ("1. Open the folder", "deepseek-r1:8b"))[1])
    models, fake = cloud_that_fails(brain, monkeypatch, [("Done.", [])])
    out, _ = app.answer("make a plan to organize my downloads step by step")
    assert models == [CLOUD, app.settings()["model"]]       # the failed cloud turn, then the everyday brain with the plan
    assert plans == ["make a plan to organize my downloads step by step"]
    assert "1. Open the folder" in fake.seen[0][0]["content"]  # the plan was there before the first local answer
    assert out["reply"] == "Done."


def test_cloud_fails_on_a_code_request_then_the_coder_answers_it(brain, monkeypatch):
    models, _ = cloud_that_fails(brain, monkeypatch, [("Fixed.", [])])
    out, _ = app.answer("debug my python script")
    assert models == [CLOUD, "qwen2.5-coder:7b"]           # not the everyday brain first
    assert out["reply"] == "Fixed." and "Used the local brain" in out["activity"]


def test_cloud_fails_on_a_plain_question_then_the_everyday_brain_answers_at_once(brain, monkeypatch):
    models, _ = cloud_that_fails(brain, monkeypatch, [("Paris.", [])])
    out, _ = app.answer("what is the capital of France?")    # ("2 + 2" is worked out by code now: no brain at all)
    assert models == [CLOUD] and out["reply"] == "Paris."  # the same turn ran locally, no retry


def test_cloud_fails_mid_task_then_the_rest_runs_locally(brain, monkeypatch):
    models, _ = cloud_that_fails(brain, monkeypatch, [("", [("get_datetime", {})]), ("Done.", [])])
    app.answer("what is the date and time?")
    assert models == [CLOUD, app.settings()["model"]]       # no second cloud try in the same task


def test_cloud_resting_at_the_start_routes_locally_from_the_first_turn(brain, monkeypatch):
    models, _ = cloud_that_fails(brain, monkeypatch, [("Fixed.", [])])
    app.llm._cloud_down.update(until=app.time.time() + 60, why="no internet")
    app.answer("debug my python script")
    assert models == ["qwen2.5-coder:7b"]


def test_working_cloud_brain_is_never_routed(brain, monkeypatch):
    monkeypatch.setattr(app.config, "brain", lambda s=None: CLOUD)
    monkeypatch.setattr(app.llm, "_cloud_down", {"until": 0.0, "why": ""})
    models, _, _ = recorded(brain, [("", [("get_datetime", {})]), ("Fixed.", [])])
    app.answer("debug my python script")
    assert models == [CLOUD, CLOUD]


def test_coder_is_told_windows_python_and_to_just_answer_questions(brain):
    """Live bug: the coder ran `python3`, got the Microsoft Store shortcut, and said Python wasn't installed."""
    fake, _ = brain([("You can't subtract a number from a string.", [])])
    app.answer("debug my python script: print('hi' - 1) gives an error")
    system = fake.seen[0][0]["content"]
    assert "CODING BRAIN" in system and "never `python3`" in system and "run nothing" in system


def test_everyday_brain_does_not_get_the_coder_note(brain):
    fake, _ = brain([("Hi!", [])])
    app.answer("hello there")
    assert "CODING BRAIN" not in fake.seen[0][0]["content"]


# ---- a coding QUESTION gets the coder with no tools; a coding JOB keeps them ----
def test_question_or_job():
    assert router.code_question("debug my python script: print('hi' - 1) gives an error, what's wrong? short answer")
    assert router.code_question("explain what a python decorator is")
    assert router.code_question("how do i write a regex for emails")
    assert not router.code_question("run python --version in the terminal and tell me which python I have")
    assert not router.code_question("what is wrong with C:/code/app.py")      # a file here: look at it
    assert not router.code_question("why does main.py crash")
    assert not router.code_question("fix this python bug")                     # no question words: a job


def test_coding_question_gets_the_coder_with_no_tools(brain):
    models, tool_sets, ran = recorded(brain, [("You can't subtract a number from a string.", [])])
    out, _ = app.answer("debug my python script: print('hi' - 1) gives an error, what's wrong?")
    assert models == ["qwen2.5-coder:7b"] and tool_sets == [[]]
    assert out["reply"] == "You can't subtract a number from a string." and ran == []


def test_coding_job_keeps_its_tools(brain):
    models, tool_sets, _ = recorded(brain, [("Python 3.12.", [])])
    app.answer("run python --version in the terminal and tell me which python I have")
    assert models == ["qwen2.5-coder:7b"] and "run_in_terminal" in tool_sets[0]


def test_question_words_are_editable_in_the_file(brain):
    import json
    conf = json.loads(store.current().routing.read_text(encoding="utf-8")) if store.current().routing.exists() else dict(router.DEFAULTS)
    conf["question_triggers"] = ["kya galat hai"]
    store.current().routing.write_text(json.dumps(conf), encoding="utf-8")
    assert router.code_question("python code mein kya galat hai")
    assert not router.code_question("explain this python code")
