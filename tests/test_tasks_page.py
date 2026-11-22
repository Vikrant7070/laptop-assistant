"""The Tasks page (manager plan 4-pre d): laptop tasks and worker jobs in one list, one row in full, why it ended."""
import json
import time

import pytest

from nova import db, store, task_state as ts
from nova.actions import workers
from nova.core import app, mobile as mobile_mod
from nova.core.app import tasks_page


@pytest.fixture
def trusted():
    s = app.settings()
    s["autonomy"] = "trusted"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


def _card(goal, status="done", age=0, why="", plan=()):
    card = ts.new(goal, plan, "deep brain" if plan else "none", tid=f"{time.time() - age:.6f}")
    card["started"] -= age
    ts.record(card, {"name": "get_system_status", "args": {}}, "Disk 77%", True)
    ts.mark(card, status, why)
    card["updated"] -= age
    db.save_task(card)
    return card


def _job(n, worker="opencode", status="done", age=0, **extra):
    rec = {"key": f"k{n}", "job": n, "worker": worker, "status": status, "started": time.time() - age,
           "finished": time.time() - age + 30, "brief": f"job number {n}", "folder": "C:/proj", "files": [],
           "sources": [], "result": "", "note": "", "cost": 0.0, **extra}
    workers._save_record(rec)
    return rec


def test_tasks_and_jobs_in_one_list_newest_first(client):
    _card("clean my laptop", age=300)
    _job(1, age=200, files=["README.md"])
    _job(2, worker="research", age=100, sources=["https://a.example", "https://b.example"])
    j = client.get("/api/tasks").get_json()
    assert [r["goal"] for r in j["rows"]] == ["job number 2", "job number 1", "clean my laptop"]
    kinds = {r["goal"]: (r["kind"], r["who"]) for r in j["rows"]}
    assert kinds["clean my laptop"][0] == "laptop" and kinds["job number 1"] == ("coding", "OpenCode + DeepSeek")
    assert kinds["job number 2"][0] == "research" and j["rows"][0]["sources"] == 2


@pytest.mark.parametrize("status,kind,want", [
    ("done", "", {"clean my laptop", "job number 1"}),
    ("failed", "", {"job number 3"}),
    ("stopped", "", {"sort photos"}),
    ("waiting", "", {"job number 4"}),
    ("", "laptop", {"clean my laptop", "sort photos"}),
    ("", "workers", {"job number 1", "job number 3", "job number 4"}),
])
def test_filters(client, status, kind, want):
    _card("clean my laptop", age=50)
    _card("sort photos", status="interrupted", why="restart", age=40)
    _job(1, age=30)
    _job(3, status="failed", age=20, note="tests fail")
    _job(4, status="needs-user", age=10)
    rows = client.get(f"/api/tasks?status={status}&kind={kind}").get_json()["rows"]
    assert {r["goal"] for r in rows} == want


def test_an_interrupted_task_is_at_the_top_with_continue(client):
    _card("clean my laptop")
    card = _card("sort photos", status="interrupted", why="restart")
    j = client.get("/api/tasks").get_json()
    assert [t["id"] for t in j["top"]] == ["t:" + card["id"]] and j["top"][0]["can_continue"]


def test_a_laptop_task_opens_with_every_step_and_why_it_ended(client):
    plan = [{"text": "Check the disk", "tool": "get_system_status"}, {"text": "Clean temp files", "tool": "clean_temp_files"}]
    card = _card("clean my laptop", status="stopped", why="out of steps", plan=plan)
    d = client.get(f"/api/tasks/t:{card['id']}").get_json()
    assert d["why"] == "out of steps" and d["card"]["source"] == "deep brain"
    assert [s["state"] for s in d["card"]["plan"]] == ["done", "pending"] and d["card"]["runs"][0]["result"] == "Disk 77%"
    assert client.get("/api/tasks/t:nope").status_code == 404


def test_a_coding_job_opens_with_read_only_actions_and_chat_handoffs(client, monkeypatch):
    _job(5, files=["README.md"], result="Fixed 2 typos.")
    _job(6, status="failed", note="tests fail")
    d = client.get("/api/tasks/j:k5").get_json()
    assert d["files_list"] == ["README.md"] and d["result"] == "Fixed 2 typos."
    assert [(a["do"], a.get("say")) for a in d["actions"]] == [("changes", None), ("chat", "commit coding job #5")]
    seen = []
    monkeypatch.setattr(workers, "changes", lambda which="latest", max_chars=1500, rec=None: seen.append(rec["key"]) or "1 file")
    assert client.get("/api/tasks/j:k5/changes").get_json() == {"text": "1 file"} and seen == ["k5"]
    failed = client.get("/api/tasks/j:k6").get_json()
    assert [a.get("say") for a in failed["actions"]] == ["try job #6 with Claude Code"]


def test_commit_and_retry_are_never_run_from_the_page(client, monkeypatch):
    for name in ("commit", "retry", "start"):
        monkeypatch.setattr(workers, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran from the page")))
    _job(5, files=["README.md"])
    client.get("/api/tasks")
    client.get("/api/tasks/j:k5")
    assert client.post("/api/tasks/j:k5").status_code == 405                 # read-only: no POST at all


def test_a_research_job_report(client, monkeypatch):
    _job(7, worker="research")
    monkeypatch.setattr(workers, "report", lambda which="latest", max_chars=1500, rec=None: f"report of #{rec['job']}")
    assert [a["do"] for a in client.get("/api/tasks/j:k7").get_json()["actions"]] == ["report"]
    assert client.get("/api/tasks/j:k7/report").get_json() == {"text": "report of #7"}
    assert client.get("/api/tasks/j:k7/changes").status_code == 404


def test_why_it_ended_after_stop_barge_in_and_a_failed_check(brain, trusted, monkeypatch):
    def stop_on(name):
        def run(n, a):
            if n == name:
                raise app.conversation.Stopped()
            return "ok"
        return run
    brain([("", [("click_element", {"name": "a"})])])
    monkeypatch.setattr(app.tools, "run_tool", stop_on("click_element"))
    monkeypatch.setattr(app.conversation, "check_stop", lambda: (_ for _ in ()).throw(app.conversation.Stopped())
                        if app.state.get("task", {}).get("calls") else None)
    app.state["stop_why"] = "you talked over her"
    app.answer("tidy my icons")
    card = app.state["task"]["card"]
    assert card["status"] == "stopped" and card["why"] in ("you talked over her", "Stop pressed")


def test_a_barge_in_stop_is_recorded_as_such(monkeypatch):
    monkeypatch.setattr(app.session.lock, "locked", lambda: True)
    monkeypatch.setattr(app.conversation, "stop_now", lambda: None)
    monkeypatch.setattr(app.voice_io, "on_wake", lambda: None)
    monkeypatch.setattr(app.voice_io.runtime, "spawn", lambda *a, **k: None)
    app.voice_io.on_barge("stop", "stop")
    assert app.state["stop_why"] == "you talked over her"


def test_a_failed_task_says_which_check_failed(brain, trusted, monkeypatch):
    from nova.actions import checks
    monkeypatch.setattr(checks, "evaluate", lambda c, ctx=None, **k: (False, "no such window"))
    brain([("", [("open_app", {"name": "spotify"})]), ("Spotify is open!", []), ("Spotify is open now!", [])])
    app.answer("open Spotify")
    saved = [c for c in db.load_tasks() if c["goal"] == "open Spotify"][0]
    assert saved["status"] == "failed" and "spotify" in saved["why"].lower()
    assert saved["verdict"] is False and saved["checks"]


def test_nothing_secret_on_the_page(client):
    _job(8, brief="log in with password hunter2", result="the code is 123456")
    text = json.dumps(client.get("/api/tasks").get_json()) + json.dumps(client.get("/api/tasks/j:k8").get_json())
    assert "hunter2" not in text and "123456" not in text


def test_the_iphone_list_needs_the_phone_key():
    mob = mobile_mod.Mobile(tasks=lambda: [{"goal": "x"}])
    c = mob.app.test_client()
    assert c.get("/m/tasks").status_code == 401
    mob.new_pair_code()
    key, _ = mob.pair(mob.pair_code, "iPhone")
    assert c.get("/m/tasks", headers={"Authorization": "Bearer " + key}).get_json() == {"rows": [{"goal": "x"}]}
    mobile_mod._file().unlink(missing_ok=True)


def test_telegram_job_messages_point_at_the_tasks_page(monkeypatch):
    sent = []
    monkeypatch.setattr(app.integrations, "user_away", lambda: True)
    monkeypatch.setattr(app.phone, "notify", lambda text: sent.append(text) or 1)
    monkeypatch.setattr(app.voice_io, "announce", lambda *a, **k: None)
    monkeypatch.setattr(app.voice_io, "wake_body", lambda: None)
    monkeypatch.setattr(app.learner, "job_finished", lambda job: None)
    monkeypatch.setitem(app.settings(), "wake", False)
    app.job_done({"id": 91, "kind": "coding", "title": "t", "status": "failed", "error": "boom", "worker": "opencode"})
    app.job_done({"id": 93, "kind": "research", "title": "t", "status": "done", "result": "Found it."})
    assert all(s.endswith(tasks_page_line()) for s in sent) and len(sent) == 2


def tasks_page_line():
    return app.integrations.TASKS_LINE


def test_the_pages_are_wired_up(client):
    assert client.get("/tasks").status_code == 200
    static = app.ROOT / "static"
    page = (static / "tasks.html").read_text(encoding="utf-8")
    js = (static / "tasks.js").read_text(encoding="utf-8")
    assert 'name="viewport"' in page and "max-width:640px" in page and page.index("markup.js") < page.index("tasks.js")
    assert "innerHTML" not in js and "/api/task/pick" in js and "/?draft=" in js
    main = (static / "index.html").read_text(encoding="utf-8")
    nova = (static / "nova.js").read_text(encoding="utf-8")
    assert "location.href='/tasks'" in main and 'get("draft")' in nova and "send(draft" not in nova     # never sent by itself
    assert "/tasks#t:" in (static / "taskstrip.js").read_text(encoding="utf-8")
    mob = (static / "mobile" / "index.html").read_text(encoding="utf-8")
    assert '"/m/tasks"' in mob and "innerHTML" not in mob
    assert tasks_page.rows(limit=1) == []                                    # no tasks: an empty list, no error


def test_what_changed_for_a_folder_that_is_gone_says_so(client):
    _job(9, files=["README.md"], folder="C:/no/such/folder/anywhere")
    j = client.get("/api/tasks/j:k9/changes").get_json()
    assert j == {"text": "The project folder isn't there any more (C:/no/such/folder/anywhere)."}
