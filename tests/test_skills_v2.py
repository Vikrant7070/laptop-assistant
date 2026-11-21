"""Skills v2: strict matching, code-run steps with checks, honest endings, edits with history, skills from runs."""
import json
import shutil
from pathlib import Path

from nova import store
from nova.actions import checks, skills, tools
from nova.core import app

SHIPPED = Path(__file__).resolve().parent.parent / "skills"


def install(*names):
    store.current().skills_dir.mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copy(SHIPPED / f"{n}.md", store.current().skills_dir / f"{n}.md")


def write(name, text):
    store.current().skills_dir.mkdir(parents=True, exist_ok=True)
    (store.current().skills_dir / f"{name}.md").write_text(text, encoding="utf-8")


def set_autonomy(mode):
    s = app.settings()
    s["autonomy"] = mode
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


def v2(name, body, extra=""):
    return f"---\nname: {name}\nformat: 2\ndescription: test skill {name}\nexamples: {name.replace('-', ' ')}\n{extra}---\n{body}\n"


# ------------------------------------------------------------------ matching

def test_loose_requests_start_no_skill():
    install("web-research", "anime-time", "code-research")
    for q in ["Where you find out?", "what is the research on sleep", "look up my battery", "how do you know that",
              "Can you check the Crunchyroll latest anime and tell me which one should I watch?",
              "tell me latest anime episode to watch", "what's the weather"]:
        assert skills.pick(q) == (None, None), q


def test_clear_requests_still_start_the_skill():
    install("web-research", "anime-time", "movie-time", "morning-check", "vscode-git-push")
    assert skills.pick("anime time")[0]["name"] == "anime-time"
    assert skills.pick("set up crunchyroll please")[0]["name"] == "anime-time"
    s, how = skills.pick("research quantum batteries")
    assert s["name"] == "web-research" and how == "exact" and skills.guess_input(s, "research quantum batteries") == "quantum batteries"
    assert skills.pick("do some research on solar panels")[0]["name"] == "web-research"
    assert skills.pick("hey nova, can you push my code")[0]["name"] == "vscode-git-push"
    assert skills.pick("it's movie time")[0]["name"] == "movie-time"
    assert skills.pick("good morning")[0]["name"] == "morning-check"


def test_one_word_trigger_needs_a_request_that_starts_with_it():
    install("web-research")
    assert skills.pick("research") [0]["name"] == "web-research"          # the whole request
    assert skills.pick("a research paper about cats")[0] is None          # the word in the middle never starts it


def test_not_for_and_requires_any_block_a_skill():
    write("relax", v2("relax", "- set_brightness percent=30", "not_for: dim the lights for the photo\nrequires_any: relax\n"))
    assert skills.pick("relax")[0]["name"] == "relax"
    assert skills.pick("relax please")[0]["name"] == "relax"
    write("relax", v2("relax", "- set_brightness percent=30", "examples: relax, chill out\nnot_for: chill out with a coffee\nrequires_any: relax, chill\n"))
    assert skills.pick("chill out with a coffee")[0] is None             # not_for wins


FUZZY = v2("calm-mode", "s1. set_brightness percent=30\ns2. say Calm.", "").replace(
    "description: test skill calm-mode",
    "description: Dim the screen and play calm relaxing music").replace("examples: calm mode", "examples: calm mode, chill out")


def test_a_sounds_like_match_of_a_skill_that_changes_things_asks_first(brain):
    set_autonomy("trusted")
    write("calm-mode", FUZZY)
    s, how = skills.pick("dim screen calm relaxing music")
    assert (s["name"], how) == ("calm-mode", "fuzzy")
    fake, ran = brain([("", [("run_skill", {"name": "calm-mode"})]), ("ok", [])])
    out, _ = app.answer("dim screen calm relaxing music")
    assert out["pending"][0]["tool"] == "run_skill" and "calm-mode" in out["pending"][0]["label"] and not ran
    out, _ = app.confirm_pending(False)                                   # "no": that sentence never starts it again
    assert skills.stats_get("calm-mode")["denied"] == 1
    assert skills.pick("dim screen calm relaxing music") == (None, None)


def test_exact_request_runs_without_asking(brain):
    set_autonomy("trusted")
    write("calm-mode", FUZZY)
    fake, ran = brain([("", [("run_skill", {"name": "calm-mode"})]), ("ok", [])])
    out, _ = app.answer("calm mode")
    assert not out.get("pending") and ran == [("set_brightness", {"percent": 30})]


def test_explicit_only_skill_always_needs_the_users_own_words():
    install("vscode-git-push")
    app.state["task"] = {"skill": "", "skill_how": ""}
    assert app.skill_needs_ok({"name": "vscode-git-push"})                # the brain picked it on its own
    app.state["task"] = {"skill": "vscode-git-push", "skill_how": "exact"}
    assert not app.skill_needs_ok({"name": "vscode-git-push"})


# ------------------------------------------------------------------ parsing the new format

FULL = '''---
name: demo
format: 2
version: 3
description: Demo skill
examples: demo time
risk: disruptive
---
s1. open_website url_or_search=https://example.com
    expect: window_open "Example"
    on_fail: retry 1, then stop "Example didn't open"
s2. if page_has "Log In" goto s4
s3. say Already in
s4. login_if_needed window=Example
    on_fail: ask "Sign in yourself, then say yes."
s5. brain "Pick the first video" tools: list_clickable, click_element slot: pick
s6. confirm "Go ahead with {pick}?"
s7. report "Say what you did."
'''


def test_v2_body_parses_checks_branches_and_brain_steps():
    s = skills.parse(FULL)
    assert s["kind"] == "routine" and s["format"] == 2 and s["version"] == 3
    st = {x["id"]: x for x in s["steps"]}
    assert st["s1"]["expect"] == {"check": "window_open", "arg": "Example"}
    assert st["s1"]["on_fail"] == [{"do": "retry", "n": 1}, {"do": "stop", "msg": "Example didn't open"}]
    assert st["s2"]["if"] == {"check": "page_has", "arg": "Log In"} and st["s2"]["goto"] == "s4"
    assert st["s5"]["brain"] == "Pick the first video" and st["s5"]["tools"] == ["list_clickable", "click_element"] and st["s5"]["slot"] == "pick"
    assert st["s6"]["confirm"] == "Go ahead with {pick}?" and st["s7"]["report"] == "Say what you did."
    assert "login_if_needed" in s["tools"] and "click_element" in s["tools"]


def test_v2_rejects_bad_files():
    bad = {"unknown check": "s1. open_app name=x\n    expect: looks_nice",
           "backward goto": "s1. say a\ns2. if page_has \"x\" goto s1",
           "report not last": "s1. report \"x\"\ns2. say a",
           "not a tool": "s1. format_drive drive=C",
           "skill inside skill": "s1. run_skill name=x"}
    for why, body in bad.items():
        assert skills.parse(v2("x", body))["kind"] == "broken", why


def test_render_and_parse_are_inverses():
    s = skills.parse(FULL)
    again = skills.parse(skills.render_v2({**s, "risk_explicit": "disruptive"}, s["steps"]))
    assert [skills.render_step(x) for x in again["steps"]] == [skills.render_step(x) for x in s["steps"]]
    assert again["steps"][0]["expect"] == s["steps"][0]["expect"] and again["steps"][3]["on_fail"] == s["steps"][3]["on_fail"]


def test_risk_never_lower_than_the_steps_need():
    assert skills.parse(v2("a", "s1. get_datetime"))["risk"] == "read_only"
    assert skills.parse(v2("b", "s1. set_dark_mode enabled=true"))["risk"] == "disruptive"
    assert skills.parse(v2("c", "s1. kill_process name_or_pid=x", "risk: read_only\n"))["risk"] == "consequential"
    assert skills.parse(v2("d", "s1. get_datetime", "risk: consequential\n"))["risk"] == "consequential"


# ------------------------------------------------------------------ the executor

def test_failed_check_is_reported_honestly_and_counted(brain, monkeypatch):
    set_autonomy("trusted")
    write("open-x", v2("open-x", 's1. open_website url_or_search=x.com\n    expect: window_open "X"\n    on_fail: retry 1, then stop "X did not open"\ns2. say Opened X!'))
    monkeypatch.setattr(app.checks, "evaluate", lambda *a, **k: (False, "no such window"))
    fake, ran = brain([("", [("run_skill", {"name": "open-x"})]), ("It is all done!", [])])
    out, _ = app.answer("open x")
    assert len(ran) == 2                                                   # once, and the one retry
    assert "stopped my 'open-x' skill: X did not open" in out["reply"] and "Opened X" not in out["reply"]
    st = skills.stats_get("open-x")
    assert st["runs"] == 1 and st.get("ok", 0) == 0 and st["failed_by_step"] == {"s1": 1}


def test_skip_on_fail_keeps_going_but_the_ending_admits_it(brain, monkeypatch):
    set_autonomy("trusted")
    write("two", v2("two", 's1. set_brightness percent=50\n    expect: fullscreen\n    on_fail: skip\ns2. say Lovely!'))
    monkeypatch.setattr(app.checks, "evaluate", lambda *a, **k: (False, "not fullscreen"))
    fake, ran = brain([("", [("run_skill", {"name": "two"})])])
    out, _ = app.answer("two")
    assert "not everything worked" in out["reply"] and "step s1" in out["reply"] and "Lovely" not in out["reply"]


def test_if_goto_branches(brain, monkeypatch):
    set_autonomy("trusted")
    write("branch", v2("branch", 's1. if page_has "Log In" goto s3\ns2. say not signed in\ns3. say finished'))
    monkeypatch.setattr(app.checks, "evaluate", lambda *a, **k: (True, "yes"))
    brain([("", [("run_skill", {"name": "branch"})])])
    out, _ = app.answer("branch")
    assert out["reply"] == "finished"
    monkeypatch.setattr(app.checks, "evaluate", lambda *a, **k: (False, "no"))
    brain([("", [("run_skill", {"name": "branch"})])])
    out, _ = app.answer("branch")
    assert out["reply"] == "not signed in finished"


def test_confirm_step_pauses_and_resumes(brain):
    set_autonomy("trusted")
    write("ship-it", v2("ship-it", 's1. open_app name=Notepad\ns2. confirm "Really ship it?"\ns3. press_keys keys=enter\ns4. say Shipped!'))
    fake, ran = brain([("", [("run_skill", {"name": "ship-it"})])])
    out, _ = app.answer("ship it")
    assert out["pending"][0]["label"] == "Really ship it?" and ran == [("open_app", {"name": "Notepad"})]
    out, _ = app.confirm_pending(True)
    assert ran[-1] == ("press_keys", {"keys": "enter"}) and out["reply"] == "Shipped!"
    assert skills.stats_get("ship-it")["ok"] == 1


def test_confirm_step_no_stops_the_skill(brain):
    set_autonomy("trusted")
    write("ship-it", v2("ship-it", 's1. confirm "Really?"\ns2. press_keys keys=enter\ns3. say Shipped!'))
    fake, ran = brain([("", [("run_skill", {"name": "ship-it"})])])
    app.answer("ship it")
    out, _ = app.confirm_pending(False)
    assert not ran and "stopped" in out["reply"] and skills.stats_get("ship-it")["stopped_by_user"] == 1


def test_brain_step_fills_a_slot_for_later_steps(brain):
    set_autonomy("trusted")
    write("msg-it", v2("msg-it", 's1. brain "Suggest a short commit message" slot: message\ns2. say Message is {message}'))
    fake, ran = brain([("", [("run_skill", {"name": "msg-it"})]), ("Fix the typo", [])])
    out, _ = app.answer("msg it")
    assert out["reply"] == "Message is Fix the typo"
    one_step_prompt = fake.seen[1][0]["content"]                           # it saw only its own step, not the chat
    assert "Suggest a short commit message" in one_step_prompt and "msg it" not in fake.seen[1][1]["content"]


def test_brain_step_must_really_call_a_tool(brain):
    set_autonomy("trusted")
    write("press-f", v2("press-f", 's1. brain "Press f" tools: press_keys\ns2. say Pressed!'))
    fake, ran = brain([("", [("run_skill", {"name": "press-f"})]), ("Done, I pressed it!", [])])
    out, _ = app.answer("press f")
    assert not ran and "stopped my 'press-f' skill" in out["reply"] and "Pressed!" not in out["reply"]
    fake, ran = brain([("", [("run_skill", {"name": "press-f"})]), ("", [("press_keys", {"keys": "f"})]), ("Pressed f.", [])])
    out, _ = app.answer("press f")
    assert ran == [("press_keys", {"keys": "f"})] and out["reply"] == "Pressed!"


def test_report_step_words_the_ending_from_the_results(brain):
    set_autonomy("trusted")
    write("health", v2("health", 's1. get_datetime\ns2. report "Say the time in a sentence."', "risk: read_only\n"))
    fake, ran = brain([("", [("run_skill", {"name": "health"})]), ("It is noon.", [])], tool_output="12:00 pm")
    out, _ = app.answer("health")
    assert out["reply"] == "It is noon." and "12:00 pm" in fake.seen[1][-1]["content"]


# ------------------------------------------------------------------ never claim what did not happen

def test_a_recipe_the_model_ignores_is_not_reported_as_done(brain):
    set_autonomy("trusted")
    write("rec", "---\nname: rec\nwhen: do my recipe\nkind: recipe\n---\n1. Call list_notes\n2. Summarise them")
    fake, ran = brain([("", [("run_skill", {"name": "rec"})]), ("All set, boss!", []), ("All set, boss!", [])])
    out, _ = app.answer("do my recipe")
    assert not ran and "didn't actually run my 'rec' skill" in out["reply"]


def test_claiming_done_without_calling_anything_is_caught(brain):
    set_autonomy("trusted")
    install("movie-time")
    fake, ran = brain([("Wah boss, you're all set!", []), ("Wah boss, you're all set!", [])])
    out, _ = app.answer("movie time")
    assert not ran and "haven't actually done that" in out["reply"]
    fake, ran = brain([("Sure, tell me more about the movie you like.", [])])      # an ordinary reply is left alone
    out, _ = app.answer("movie time")
    assert "tell me more" in out["reply"]


# ------------------------------------------------------------------ login_if_needed never types a password

def _box(name, password=False, filled=False):
    return {"name": name, "type": "Edit", "password": password, "filled": filled, "ctrl": None, "x": 0, "y": 0, "offscreen": False}


def _btn(name):
    return {"name": name, "type": "Button", "x": 0, "y": 0, "offscreen": False, "ctrl": None}


def login_world(monkeypatch, forms, items):
    calls = []
    seq = list(forms)
    monkeypatch.setattr(tools.forms, "_form_boxes", lambda w: ((1, "Site - Chrome", "chrome.exe"), seq.pop(0) if len(seq) > 1 else seq[0]))
    monkeypatch.setattr(tools.forms, "_scan", lambda w, limit=0, **k: ((1, "Site - Chrome", "chrome.exe"), items))
    monkeypatch.setattr(tools.forms, "click_element", lambda name, window="", **k: calls.append(("click", name)) or "Clicked")
    monkeypatch.setattr(tools.forms, "help_login", lambda window="", wait_secs=150: calls.append(("help_login",)) or "The login page has moved on")
    monkeypatch.setattr(tools.forms, "wait_for_code", lambda window="", wait_secs=180: calls.append(("code",)) or "ok")
    monkeypatch.setattr(tools.windows, "_find_window", lambda t: None)
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    return calls


def test_login_if_needed_already_signed_in(monkeypatch):
    calls = login_world(monkeypatch, [[_box("Search")]], [_btn("Profile")])
    assert tools.login_if_needed("Site").startswith("Already signed in") and calls == []


def test_login_if_needed_presses_log_in_when_the_browser_filled_it(monkeypatch):
    calls = login_world(monkeypatch, [[_box("Email", filled=True), _box("Password", password=True)], [_box("Search")]],
                        [_btn("Log in")])
    assert tools.login_if_needed("Site").startswith("Signed in")
    assert calls == [("click", "Log in")]                                  # pressed the button, never typed anything


def test_login_if_needed_waits_for_the_users_saved_login(monkeypatch):
    calls = login_world(monkeypatch, [[_box("Email"), _box("Password", password=True)], [_box("Search")]], [])
    assert tools.login_if_needed("Site").startswith("Signed in")
    assert calls == [("help_login",)]


# ------------------------------------------------------------------ the checks

def test_parse_check_and_unknown_names():
    assert skills.parse_check('window_open "Crunchyroll"') == {"check": "window_open", "arg": "Crunchyroll"}
    assert skills.parse_check("fullscreen") == {"check": "fullscreen", "arg": ""}
    assert skills.parse_check("window_open") is None and skills.parse_check("looks_nice") is None


def test_checks_see_windows_and_poll(monkeypatch):
    monkeypatch.setattr(tools.windows, "_windows", lambda: [(1, "Crunchyroll - Google Chrome", "chrome.exe")])
    assert checks.real_evaluate({"check": "window_open", "arg": "crunchyroll"})[0]
    assert not checks.real_evaluate({"check": "window_open", "arg": "netflix"}, timeout=0.2, interval=0.05)[0]
    assert checks.real_evaluate({"check": "result_has", "arg": "Clicked"}, {"result": "Clicked 'Log in'"})[0]
    assert not checks.real_evaluate({"check": "nonsense", "arg": ""})[0]


# ------------------------------------------------------------------ edits, history, stats

def test_edit_changes_one_step_and_keeps_the_old_version():
    write("calm", v2("calm", "s1. set_brightness percent=30\ns2. say Calm."))
    msg = skills.edit("calm", [{"op": "insert_step", "after": "s1", "step": "set_volume action=down, steps=5"},
                               {"op": "set_expect", "id": "s1", "expect": 'front_window "Settings"'},
                               {"op": "add_not_for", "text": "calm down"}])
    assert "version 2" in msg
    s = skills.get("calm")
    assert [x["id"] for x in s["steps"]] == ["s1", "s3", "s2"] and s["version"] == 2     # ids are never reused
    assert s["steps"][0]["expect"] == {"check": "front_window", "arg": "Settings"} and "calm down" in s["not_for"]
    assert (store.current().skills_dir / ".history" / "calm.v1.md").exists() and skills.history("calm")[-1]["op"].startswith("added s3")
    assert "Put back" in skills.undo("calm") and [x["id"] for x in skills.get("calm")["steps"]] == ["s1", "s2"]
    assert "Put back" in skills.undo("calm") and len(skills.get("calm")["steps"]) == 3         # undo again = redo


def test_bad_edits_change_nothing():
    write("calm", v2("calm", "s1. set_brightness percent=30\ns2. say Calm."))
    before = (store.current().skills_dir / "calm.md").read_text(encoding="utf-8")
    for op in [{"op": "replace_step", "id": "s9", "step": "wait 1"}, {"op": "insert_step", "after": "s1", "step": "format_drive drive=C"},
               {"op": "insert_step", "after": "s1", "step": "run_skill name=calm"}, {"op": "set_expect", "id": "s1", "expect": "looks_nice"},
               {"op": "set_risk", "level": "read_only"}, {"op": "insert_step", "after": "s1", "step": "say my password is hunter2"}]:
        assert "Nothing was changed" in skills.edit("calm", [op]) or "won't save" in skills.edit("calm", [op]), op
    assert (store.current().skills_dir / "calm.md").read_text(encoding="utf-8") == before
    assert "too many" in skills.edit("calm", [{"op": "delete_step", "id": "s2"}] * 6)


def test_editing_an_old_routine_turns_it_into_the_new_format():
    write("old", "---\nname: old\nwhen: old thing\n---\n- minimize_all_windows\n- say Hi")
    assert skills.get("old")["format"] == 1
    skills.edit("old", [{"op": "replace_step", "id": "s2", "step": "say Hello"}])
    s = skills.get("old")
    assert s["format"] == 2 and s["steps"][1]["say"] == "Hello" and s["examples"] == ["old thing"]


def test_a_recipe_cannot_be_patched_step_by_step():
    skills.save("report", "weekly report", "Read my notes\nWrite a summary")
    assert "plain-English recipe" in skills.edit("report", [{"op": "delete_step", "id": "s1"}])


def test_edit_tool_is_registered_and_a_skill_cannot_use_it():
    for n in ("edit_skill", "undo_skill_change", "save_task_as_skill", "login_if_needed"):
        assert n in tools.FUNCS
    assert skills.parse(v2("x", "s1. edit_skill name=x"))["kind"] == "broken"


def test_correction_right_after_a_skill_points_the_brain_at_edit_skill():
    write("calm", v2("calm", "s1. set_brightness percent=30"))
    app.state["last_skill"] = {"name": "calm", "ts": __import__("time").time(), "result": "worked"}
    note = app.skill_fix_note("no, that's wrong, also mute it")
    assert "edit_skill" in note and "s1: set_brightness" in note and skills.stats_get("calm")["corrections"] == 1
    assert app.skill_fix_note("what time is it") == ""


def test_stats_promote_a_draft_and_retire_a_failing_skill():
    write("d", v2("d", "s1. say hi", "status: draft\ninvoke: explicit_only\n"))
    assert skills.record_run("d", True) == "" and skills.get("d")["status"] == "draft"
    assert "keeping it" in skills.record_run("d", True) and skills.get("d")["status"] == "active"
    notes = [skills.record_run("d", False, failed_step="s1", error="boom") for _ in range(5)]
    assert skills.get("d")["status"] == "deprecated" and any("stopped using it" in n for n in notes)
    assert sum(1 for n in notes if n) == 1                                  # said once, not on every later failure
    assert skills.pick("d")[0] is None                                      # a retired skill never starts
    assert skills.stats_get("d")["failed_by_step"] == {"s1": 5}


def test_describe_shows_outcomes():
    write("calm", v2("calm", "s1. say hi"))
    skills.record_run("calm", True)
    skills.record_run("calm", False, failed_step="s1", error="x")
    assert "[1/2 ok]" in skills.describe()


# ------------------------------------------------------------------ skills from what Nova really did

TRACE = [
    {"tool": "list_windows", "args": {}, "ok": True, "out": "x"},
    {"tool": "open_website", "args": {"url_or_search": "https://youtube.com/results?search_query=lofi"}, "ok": True, "out": "x"},
    {"tool": "click_at", "args": {"x": 10, "y": 20}, "ok": True, "out": "x"},
    {"tool": "press_keys", "args": {"keys": "f"}, "ok": False, "out": "Error: nope"},
    {"tool": "press_keys", "args": {"keys": "f"}, "ok": True, "out": "x"},
    {"tool": "press_keys", "args": {"keys": "f"}, "ok": True, "out": "x"},
    {"tool": "look_at_screen", "args": {}, "ok": True, "out": "playing"},
]


def test_trace_becomes_clean_steps():
    steps, why = skills.steps_from_trace(TRACE, "play lofi on youtube")
    kinds = [("tool" in s and s["tool"]) or ("brain" in s and "brain") or ("report" in s and "report") for s in steps]
    assert kinds == ["open_website", "brain", "press_keys", "look_at_screen", "report"]      # coordinates became a brain step
    assert all("x=" not in skills.render_step(s) for s in steps) and [s["id"] for s in steps][:2] == ["s1", "s2"]


def test_a_trace_with_a_secret_is_never_saved():
    steps, why = skills.steps_from_trace([{"tool": "type_text", "args": {"text": "my password is hunter2"}, "ok": True, "out": ""}], "x")
    assert steps is None and "won't save" in why


def test_draft_applies_only_valid_hints_and_runs_by_name_only():
    steps, _ = skills.steps_from_trace(TRACE[1:2] + TRACE[4:5], "play lofi")
    msg = skills.draft_from_steps("lofi-play", "Plays lofi on YouTube", "lofi time", steps, hints={
        "slots": [{"step": "s1", "arg": "url_or_search", "find": "lofi"}, {"step": "s9", "arg": "x", "find": "y"}],
        "expect": {"s1": 'window_open "YouTube"', "s2": "not a check at all"}, "not_for": ["what is lofi"]}, goal="play lofi")
    assert msg.startswith("Saved a draft skill 'lofi-play'")
    s = skills.get("lofi-play")
    assert s["status"] == "draft" and s["invoke"] == "explicit_only"
    assert s["steps"][0]["args"]["url_or_search"].endswith("search_query={input}") and s["steps"][0]["expect"]["arg"] == "YouTube"
    assert s["steps"][1]["expect"] is None and s["not_for"] == ["what is lofi"]
    assert skills.pick("lofi time")[0]["name"] == "lofi-play"            # a draft starts only from its own words...
    assert skills.pick("play some chill music")[0] is None                # ...never from a fuzzy guess
    assert "already have a skill" in skills.draft_from_steps("lofi-play", "x", "y", steps)


def test_remember_that_saves_the_task_that_just_finished(brain):
    set_autonomy("trusted")
    fake, ran = brain([("", [("open_website", {"url_or_search": "https://example.com"})]), ("", [("press_keys", {"keys": "f"})]), ("Done.", [])])
    app.answer("open example and go fullscreen")
    fake, ran = brain([("", [("save_task_as_skill", {"name": "example-full", "examples": "example fullscreen"})]), ("Saved.", [])])
    app.answer("remember that")
    s = skills.get("example-full")
    assert s and s["status"] == "draft" and [x["tool"] for x in s["steps"]] == ["open_website", "press_keys"]
    assert skills.history("example-full")[0]["author"] == "recorder"


def test_remember_that_with_nothing_to_remember(brain):
    fake, ran = brain([("", [("save_task_as_skill", {"name": "x", "examples": "x"})]), ("Hm.", [])])
    app.answer("remember that")
    assert skills.get("x") is None


def test_library_cap_and_duplicates(monkeypatch):
    skills.save("one", "Dim the screen for a movie", "set_brightness percent=30", when="dim for movie")
    assert "looks like my 'one'" in skills.save("two", "Dim the screen for a movie", "set_brightness percent=40", when="dim for movie")
    monkeypatch.setattr(skills, "MAX_ACTIVE", 1)
    assert "already have 1 skills" in skills.save("three", "Open the garden gate", "open_app name=Gate", when="garden")
    assert skills.least_used() == "one"
