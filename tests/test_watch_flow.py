"""👀 The conversations around watching: 'watch me' ... 'done' -> a named draft skill, and a skill that learns one step
from the user (they took over, or a step failed). The recorder itself is faked - no real mouse/keyboard hooks."""
import json

import pytest

from nova import store
from nova.actions import recorder, skills
from nova.core import app


@pytest.fixture
def rec(monkeypatch):
    st = {"mode": None, "events": [], "acted": False, "goal": "", "starts": []}

    def start(goal="", mode="record"):
        st["starts"].append(mode)
        st["mode"] = mode
        if mode == "record":
            st["goal"] = goal
        return ""

    monkeypatch.setattr(recorder, "ENABLED", True)
    monkeypatch.setattr(recorder, "start", start)
    monkeypatch.setattr(recorder, "stop", lambda: (st.update(mode=None), list(st["events"]))[1])
    monkeypatch.setattr(recorder, "stop_listening", lambda: st.update(mode=None) if st["mode"] == "listen" else None)
    monkeypatch.setattr(recorder, "recording", lambda: st["mode"] == "record")
    monkeypatch.setattr(recorder, "listening", lambda: st["mode"] == "listen")
    monkeypatch.setattr(recorder, "goal", lambda: st["goal"])
    monkeypatch.setattr(recorder, "limit_hit", lambda: False)
    monkeypatch.setattr(recorder, "user_acted_since", lambda t: st["mode"] == "listen" and st["acted"])
    return st


def set_autonomy(mode):
    s = app.settings()
    s["autonomy"] = mode
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


def write(name, body, extra=""):
    store.current().skills_dir.mkdir(parents=True, exist_ok=True)
    (store.current().skills_dir / f"{name}.md").write_text(
        f"---\nname: {name}\nformat: 2\ndescription: test skill {name}\nexamples: {name.replace('-', ' ')}\n{extra}---\n{body}\n",
        encoding="utf-8")


CHROME = "chrome.exe"
CRUNCHY = [
    {"kind": "focus", "t": 1.0, "window": "Google Chrome", "proc": CHROME, "new_app": True},
    {"kind": "url", "t": 2.0, "url": "https://www.crunchyroll.com", "window": "Crunchyroll - Google Chrome", "proc": CHROME},
    {"kind": "click", "t": 9.0, "name": "Simulcasts", "ctype": "Hyperlink", "browser": True,
     "window": "Crunchyroll - Google Chrome", "proc": CHROME},
    {"kind": "url", "t": 10.0, "url": "https://www.crunchyroll.com/simulcasts", "window": "Crunchyroll - Google Chrome", "proc": CHROME},
]


def test_watch_me_done_and_a_name_make_a_draft_skill(brain, rec):
    set_autonomy("trusted")
    fake, ran = brain([("", [("save_task_as_skill", {"name": "crunchy-new", "examples": "new anime"})]), ("Saved it!", [])])
    out, _ = app.answer("watch me")
    assert "I'm watching" in out["reply"] and rec["mode"] == "record" and not fake.seen      # no brain involved
    rec["events"] = CRUNCHY
    out, _ = app.answer("done")
    assert out["reply"].startswith("Got it - I saw 3 steps") and "What should I call" in out["reply"] and not fake.seen
    assert rec["mode"] is None
    app.answer("call it crunchy new - I'll say new anime")
    assert "You just watched the user" in fake.seen[0][0]["content"]
    s = skills.get("crunchy-new")
    assert s and s["status"] == "draft" and [x["tool"] for x in s["steps"]] == ["open_app", "open_website", "open_website"]
    assert skills.history("crunchy-new")[0]["author"] == "recorder"
    assert app.watch_note() == ""                                            # the note stops once it's saved


def test_done_when_not_watching_is_just_a_message(brain, rec):
    fake, ran = brain([("Great, what's next?", [])])
    out, _ = app.answer("done")
    assert out["reply"] == "Great, what's next?" and fake.seen


def test_no_watching_from_the_phone(brain, rec):
    out, _ = app.answer("watch me", remote=True)
    assert "only watch you when you're at the laptop" in out["reply"] and rec["mode"] is None


def test_nothing_caught(brain, rec):
    app.answer("watch me")
    rec["events"] = [{"kind": "click", "t": 1.0, "name": "Send", "ctype": "Button", "window": "Nova", "proc": "pythonw.exe", "nova": True}]
    out, _ = app.answer("done")
    assert "didn't catch anything" in out["reply"]


NOTEPAD = "s1. open_app name=Notepad\ns2. press_keys keys=ctrl+s\ns3. say Saved!"


def test_user_takes_over_and_the_skill_learns_the_extra_step(brain, rec):
    set_autonomy("trusted")
    write("notepad-save", NOTEPAD)
    rec["acted"] = True                                                     # the user clicks while the skill works
    fake, ran = brain([("", [("run_skill", {"name": "notepad-save"})])])
    out, _ = app.answer("notepad save")
    assert rec["starts"][0] == "listen"
    assert "You took over after step s1" in out["pending"][0]["label"] and ran == [("open_app", {"name": "Notepad"})]
    out, _ = app.confirm_pending(True)                                      # "yes, I'll show you"
    assert "show me how step s1" in out["reply"] and rec["mode"] == "record"
    rec["events"] = [{"kind": "key", "t": 1.0, "combo": "ctrl+shift+s", "window": "notes.txt - Notepad", "proc": "notepad.exe"}]
    out, _ = app.answer("done")
    assert "add this after step s1: press_keys keys=ctrl+shift+s" in out["pending"][0]["label"]
    out, _ = app.confirm_pending(True)                                      # "yes, change the skill"
    assert out["reply"].startswith("Changed 'notepad-save'") and out["reply"].endswith("Saved!")
    assert [x["id"] for x in skills.get("notepad-save")["steps"]] == ["s1", "s4", "s2", "s3"]
    assert ran[-1] == ("press_keys", {"keys": "ctrl+s"})                   # the skill carried on after the lesson
    assert skills.stats_get("notepad-save")["runs"] == 1


def test_saying_no_to_show_me_stops_the_skill(brain, rec):
    set_autonomy("trusted")
    write("notepad-save", NOTEPAD)
    rec["acted"] = True
    brain([("", [("run_skill", {"name": "notepad-save"})])])
    app.answer("notepad save")
    out, _ = app.confirm_pending(False)
    assert "stopped my 'notepad-save' skill" in out["reply"] and skills.stats_get("notepad-save")["stopped_by_user"] == 1
    assert app.state.get("skill_run") is None


def test_a_failed_step_is_replaced_by_what_the_user_shows(brain, rec, monkeypatch):
    set_autonomy("trusted")
    write("open-x", 's1. open_website url_or_search=x.com\n    expect: window_open "X"\n    on_fail: stop "X did not open"\ns2. say Here is X!')
    monkeypatch.setattr(app.checks, "evaluate", lambda *a, **k: (False, "no such window"))
    fake, ran = brain([("", [("run_skill", {"name": "open-x"})])])
    out, _ = app.answer("open x")
    q = out["pending"][0]["label"]
    assert "X did not open" in q and "show me how to do step s1" in q
    app.confirm_pending(True)
    rec["events"] = [{"kind": "url", "t": 1.0, "url": "https://x.com/home", "window": "Home - X - Google Chrome", "proc": CHROME}]
    out, _ = app.answer("done")
    assert "do step s1 like this: open_website url_or_search=https://x.com/home" in out["pending"][0]["label"]
    out, _ = app.confirm_pending(True)
    s = skills.get("open-x")
    assert s["steps"][0]["args"] == {"url_or_search": "https://x.com/home"} and s["steps"][0]["expect"]["arg"] == "X"
    assert out["reply"].startswith("Changed 'open-x'") and out["reply"].endswith("Here is X!")
    st = skills.stats_get("open-x")
    assert st["runs"] == 1 and st.get("ok", 0) == 0                           # that run failed - counted once


def test_read_only_skills_never_listen(brain, rec):
    set_autonomy("trusted")
    write("what-time", "s1. get_datetime\ns2. say ok")
    rec["acted"] = True
    brain([("", [("run_skill", {"name": "what-time"})])])
    out, _ = app.answer("what time")
    assert rec["starts"] == [] and out["reply"] == "ok"


def test_the_watch_tools_exist_and_cannot_go_in_a_skill():
    from nova.actions import tools
    assert "start_watching" in tools.FUNCS and "stop_watching" in tools.FUNCS
    assert skills.parse("---\nname: x\nformat: 2\n---\ns1. start_watching")["kind"] == "broken"
