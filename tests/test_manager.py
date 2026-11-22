"""The manager, step 4a: understand -> ask (one card at a time) -> brief -> the user's go -> a worker."""
import json

import pytest

from nova import db, store
from nova.actions import knowledge, learner, manager, workers
from nova.core import app


@pytest.fixture
def started(monkeypatch):
    """workers.start / workers.research recorded instead of run."""
    calls = []
    monkeypatch.setattr(workers, "start", lambda brief, folder, worker="auto", size="small", start_job=None:
                        calls.append(("start", brief, folder, worker, size)) or f"Started background job #7 ({worker}).")
    monkeypatch.setattr(workers, "research", lambda topic, focus="", **k:
                        calls.append(("research", topic, focus)) or "Started background job #8 (research).")
    return calls


@pytest.fixture
def old_job():
    workers._save_record({"key": "k1", "job": 1, "worker": "opencode", "status": "done", "started": 1.0,
                          "folder": "C:/Users/me/projects/nova-site", "brief": "x"})


def _mode(m):
    conf = workers.config()
    conf["manager"]["go_ahead"] = m
    store.current().workers.write_text(json.dumps(conf), encoding="utf-8")


def _tap(client, out, value):
    q = out["question"]
    key = q["questions"][0]["key"]
    r = client.post("/api/answer", json={"id": q["id"], "answers": {key: value}})
    return r.get_json()


# --- understand -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("make a website for my Nova project", "coding"),
    ("fix the bug in my project", "coding"),
    ("research the best chart library, then add it to my project", "research_coding"),
    ("open my project folder", None),
    ("make the app full screen", None),
    ("research green tea", None),          # plain research keeps its own path
])
def test_triage_by_word_rules(text, kind):
    assert manager.triage(text) == kind


def test_a_laptop_task_is_untouched(brain):
    fake, ran = brain([("", [("open_app", {"name": "notepad"})]), ("Opened it.", [])])
    out, _ = app.answer("open notepad")
    assert out["reply"] == "Opened it." and "handoff" not in app.state and len(fake.seen) == 2


# --- ask ------------------------------------------------------------------------------------------------------

def test_a_clear_job_skips_the_brain_and_asks_one_card_at_a_time(brain, client, old_job, started):
    fake, _ = brain([])
    out, _ = app.answer("make a website for my Nova project")
    assert fake.seen == []                                                  # no brain turn at all
    q = out["question"]["questions"][0]
    assert q["key"] == "folder" and q["choices"][0]["value"] == "C:/Users/me/projects/nova-site" and q["why"]
    assert app.state["task"]["card"]["status"] == "waiting"
    out = _tap(client, out, "C:/Users/me/projects/nova-site")
    assert out["question"]["questions"][0]["key"] == "pages"
    out = _tap(client, out, ["home", "docs"])
    assert out["question"]["questions"][0]["key"] == "new"
    out = _tap(client, out, "new")
    assert out["question"]["questions"][0]["key"] == "go" and out["brief"]             # 3 questions, then the brief
    assert fake.seen == [] and started == []                                # nothing started before "go"


def test_questions_the_request_or_an_earlier_answer_covers_are_skipped():
    manager.remember("stack", "Python")
    job = manager.plan(r"fix the bug in my project at C:\code\shop", "coding")
    assert job["folder"] == r"C:\code\shop" and [q["key"] for q in job["queue"]] == []
    job = manager.plan("make a new website in react", "coding")
    assert "stack" not in [q["key"] for q in job["queue"]] and "new" not in [q["key"] for q in job["queue"]]


def test_answers_are_saved_as_unconfirmed_facts_and_not_asked_again(client, brain, started):
    brain([])
    out, _ = app.answer("fix the bug in my project")
    out = _tap(client, out, "C:/work/app")                                  # no known folders: a text answer
    assert out["question"]["questions"][0]["key"] == "stack"
    _tap(client, out, "Python")
    facts = {f["key"]: f for f in learner.load_facts()["facts"] if f.get("kind") == "answer"}
    assert facts["stack"]["value"] == "Python" and facts["stack"]["confirmed"] is False
    assert facts["project_folder"]["value"] == "C:/work/app"
    assert learner.describe_fact(facts["stack"]) == "You told me the language / stack you use: Python."
    assert "stack" not in [q["key"] for q in manager.plan("fix the bug in my project", "coding")["queue"]]
    k = client.get("/api/knowledge").get_json()
    assert any("Python" in f["text"] and not f["confirmed"] for f in k["facts"])


# --- brief ----------------------------------------------------------------------------------------------------

def test_the_brief_is_the_template_filled_by_code_and_marks_guesses(client, brain, old_job, started):
    p = knowledge.load()
    p["profile"]["work"] = "student"
    p["profile"]["preferences"] = ["short answers", "my password is hunter2"]
    knowledge.save(p)
    brain([])
    out, _ = app.answer("make a website for my Nova project")
    out = _tap(client, out, {"skip": True})                                 # folder: her guess
    out = _tap(client, out, ["home"])
    out = _tap(client, out, "new")
    b = out["brief"]
    for part in ("GOAL (the user's words): make a website for my Nova project", "ABOUT THE USER:", "- Work: student",
                 "REQUIREMENTS:", "- Pages: Home", "FOLDER: C:/Users/me/projects/nova-site", "DONE MEANS:",
                 "MUST NOT: commit, push"):
        assert part in b, part
    assert "hunter2" not in b and "short answers" not in b                 # her own preferences aren't the worker's
    assert "- Stack: HTML + CSS + JavaScript  (my guess - not asked)" in b
    assert "FOLDER: C:/Users/me/projects/nova-site  (my guess - the user skipped this)" in b
    assert out["question"]["questions"][0]["choices"][0]["label"] == "Go (Claude Code, paid, up to $1.00)"


def test_change_it_adds_to_the_brief_and_shows_it_again(client, brain, started):
    manager.remember("stack", "Python")
    brain([])
    out, _ = app.answer(r"fix the bug in my project at C:\code\shop")
    assert out["question"]["questions"][0]["key"] == "go"                   # nothing to ask: straight to the brief
    out = _tap(client, out, "change")
    assert out["question"]["questions"][0]["kind"] == "text"
    out = _tap(client, out, "also add a test for it")
    assert "- Change asked by the user: also add a test for it" in out["brief"]
    assert out["question"]["questions"][0]["key"] == "go"


def test_go_starts_the_worker_with_the_brief(client, brain, started):
    manager.remember("stack", "Python")
    brain([])
    out, _ = app.answer(r"fix the bug in my project at C:\code\shop")
    assert out["question"]["questions"][0]["choices"][0]["label"] == "Go (OpenCode, free)"
    out = _tap(client, out, "go")
    kind, brief, folder, worker, size = started[0]
    assert (kind, folder, worker, size) == ("start", r"C:\code\shop", "opencode", "small") and "GOAL" in brief
    assert out["reply"].startswith("Started background job #7")
    card = [c for c in db.load_tasks() if c["goal"].startswith("fix the bug")][0]
    assert card["status"] == "done" and card["why"] == "handed to the worker"


# --- the go-ahead setting -------------------------------------------------------------------------------------

def test_never_starts_right_after_the_questions(brain, started):
    _mode("never")
    manager.remember("stack", "Python")
    brain([])
    out, _ = app.answer(r"fix the bug in my project at C:\code\shop")
    assert started and out["reply"].startswith("Started")


def test_paid_asks_only_for_claude_code(brain, started):
    _mode("paid")
    manager.remember("stack", "Python")
    brain([])
    app.answer(r"fix the bug in my project at C:\code\shop")                # small: OpenCode, no go needed
    assert started[-1][3] == "opencode"
    out, _ = app.answer(r"make a new website in python at C:\code\site")     # new site: Claude Code, asks first
    assert len(started) == 1 and out["question"]["questions"][0]["key"] == "pages"


def test_the_setting_is_saved_from_the_workers_tab(client):
    assert workers.config()["manager"]["go_ahead"] == "always"               # the user's default
    workers.save_settings({"manager_go_ahead": "paid"})
    assert workers.config()["manager"]["go_ahead"] == "paid"
    workers.save_settings({"research_max_sources": 2})                       # saving the rest keeps it
    assert workers.config()["manager"]["go_ahead"] == "paid"
    with pytest.raises(ValueError):
        workers.save_settings({"manager_go_ahead": "sometimes"})


# --- the brain's tool -----------------------------------------------------------------------------------------

def test_the_brains_hand_off_job_leads_to_the_same_flow(brain, client, started, monkeypatch):
    fake, _ = brain([("", [("hand_off_job", {"request": "research good note apps", "kind": "research"})]),
                     ("This should not be said.", [])])
    monkeypatch.setattr(app.tools, "run_tool", lambda n, a: manager.hand_off(**a) if n == "hand_off_job" else "ok")
    out, _ = app.answer("could you look into good note apps for me as a proper job")
    assert len(fake.seen) == 1 and out["question"]["questions"][0]["key"] == "focus"
    out = _tap(client, out, "free ones for Windows")
    assert out["question"]["questions"][0]["choices"][0]["label"] == "Go (Research, free)"
    _tap(client, out, "go")
    assert started == [("research", "research good note apps", "free ones for Windows")]


def test_a_new_request_drops_the_job(brain, old_job, started):
    brain([])
    app.answer("make a website for my Nova project")
    fake, _ = brain([("It's 5 pm.", [])])
    out, _ = app.answer("what time is it")
    assert out["reply"] == "It's 5 pm." and "handoff" not in app.state and started == []
    old = [c for c in db.load_tasks() if c["goal"].startswith("make a website")][0]
    assert old["status"] == "stopped" and "moved on" in old["why"]


def test_the_workers_tab_has_the_manager_setting():
    page = (app.ROOT / "static" / "index.html").read_text(encoding="utf-8")
    js = (app.ROOT / "static" / "nova.js").read_text(encoding="utf-8")
    assert 'id="wkGoAhead"' in page and "manager_go_ahead" in js
