"""The manager, step 4b: the worker from the job history, and research first, then coding."""
import json

import pytest

from nova import store
from nova.actions import manager, workers
from nova.core import app
from nova.core.app import managing


def _rec(n, worker, jtype, status="done", files=("a.py",), committed=False):
    return {"key": f"k{n}", "job": n, "worker": worker, "job_type": jtype, "status": status, "started": float(n),
            "files": list(files) if status != "failed" else [], "committed": committed}


def _job(request, kind="coding"):
    return manager.plan(request, kind)


@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(workers, "start", lambda brief, folder, worker="auto", size="small", start_job=None, meta=None:
                        calls.append(("start", brief, folder, worker, meta)) or
                        f"Started background job #7: {worker} is working on it. Tell the user in one short sentence.")
    monkeypatch.setattr(workers, "research", lambda topic, focus="", meta=None, **k:
                        calls.append(("research", topic, focus, meta)) or
                        "Started background job #8: researching it in background Chrome windows. Tell the user.")
    return calls


def _mode(m):
    conf = workers.config()
    conf["manager"]["go_ahead"] = m
    store.current().workers.write_text(json.dumps(conf), encoding="utf-8")


def _tap(client, out, value):
    q = out["question"]
    return client.post("/api/answer", json={"id": q["id"], "answers": {q["questions"][0]["key"]: value}}).get_json()


# --- job types and the pick rule ------------------------------------------------------------------------------

@pytest.mark.parametrize("request_text,kind,jtype", [
    ("update the README", "coding", "readme"),
    ("fix the bug in my project", "coding", "small fix"),
    ("make a website for my Nova project", "coding", "new site"),
    ("build me a todo app", "coding", "new app"),
    ("add a whole new feature to my project", "coding", "feature"),
    ("good note apps", "research", "research"),
])
def test_job_types(request_text, kind, jtype):
    assert _job(request_text, kind)["type"] == jtype


def test_no_history_the_free_worker_first():
    w, why = manager.choose(_job("fix the bug in my project"), recs=[])
    assert w == "opencode" and "hasn't done one before" in why


def test_opencode_that_did_this_type_before_gets_it_with_the_reason():
    recs = [_rec(1, "opencode", "small fix"), _rec(2, "opencode", "small fix", committed=True), _rec(3, "opencode", "small fix"),
            _rec(4, "opencode", "readme", status="failed")]                 # another type doesn't count
    w, why = manager.choose(_job("fix the bug in my project"), recs=recs)
    assert w == "opencode" and why == "OpenCode did 3 of 3 small fixes (1 committed)."


def test_two_failures_in_a_row_suggest_claude_code_but_never_switch_by_themselves(client, brain, started):
    recs = [_rec(1, "opencode", "small fix"), _rec(2, "opencode", "small fix", status="failed"),
            _rec(3, "opencode", "small fix", status="failed")]
    w, why = manager.choose(_job("fix the bug in my project"), recs=recs)
    assert w == "claude" and "the last 2 failed" in why
    for r in recs:
        workers._save_record(r)
    manager.remember("stack", "Python")
    brain([])
    out, _ = app.answer(r"fix the bug in my project at C:\code\shop")
    q = out["question"]["questions"][0]
    assert [c["value"] for c in q["choices"]] == ["go", "other", "change"] and started == []      # only a suggestion
    assert q["choices"][0]["label"].startswith("Go (Claude Code, paid") and "the last 2 failed" in q["why"]
    assert q["choices"][1]["label"] == "Use OpenCode instead (OpenCode, free)"
    _tap(client, out, "other")                                                 # the user keeps the free one
    assert started[0][3] == "opencode"


def test_a_commit_counts_double():
    recs = [_rec(1, "opencode", "small fix", committed=True), _rec(2, "opencode", "small fix", status="failed"),
            _rec(3, "opencode", "small fix", status="failed"), _rec(4, "opencode", "small fix")]
    w, why = manager.choose(_job("fix the bug in my project"), recs=recs)      # newest last: worked, failed, failed, worked
    assert w == "opencode" and why == "OpenCode did 2 of 4 small fixes (1 committed)."


def test_big_jobs_go_to_claude_code():
    assert manager.choose(_job("make a website for my Nova project"), recs=[])[0] == "claude"


def test_the_record_gets_the_job_type(client, brain, started):
    manager.remember("stack", "Python")
    brain([])
    out, _ = app.answer(r"fix the bug in my project at C:\code\shop")
    out = _tap(client, out, "go")
    meta = started[0][4]
    assert meta["job_type"] == "small fix" and meta["manager_job"].startswith("m")
    assert out["reply"] == "Started background job #7: opencode is working on it."        # not the brain's note


# --- research, then coding ------------------------------------------------------------------------------------

CHAIN = r"research the best chart library, then add it to my project at C:\code\shop"


def _chain_started(client, brain, started):
    manager.remember("stack", "Python")
    brain([])
    out, _ = app.answer(CHAIN)
    assert out["question"]["questions"][0]["choices"][0]["label"] == "Go (research first, then OpenCode, free)"
    assert out["brief"].startswith("STEPS: 1. Research: the best chart library")
    out = _tap(client, out, "go")
    assert started[-1][:2] == ("research", "the best chart library") and started[-1][3]["chain_step"] == "1 of 2"
    assert "When it's done I'll show you the coding brief" in out["reply"]
    return out


def test_one_go_starts_the_research_then_the_coding_brief_comes_back_with_what_it_found(client, brain, started, monkeypatch):
    _chain_started(client, brain, started)
    sent = []
    monkeypatch.setattr(app.voice_io, "push_event", lambda e: sent.append(e))
    monkeypatch.setattr(app.voice_io, "announce", lambda *a, **k: None)
    monkeypatch.setattr(workers, "report", lambda which="latest", max_chars=1500, rec=None:
                        "Use Chart.js: small and free." if rec else "no record")
    workers._save_record({"key": "r8", "job": 8, "worker": "research", "status": "done", "started": 9.0,
                          "manager_job": started[-1][3]["manager_job"], "job_type": "research"})
    out = managing.after_job({"id": 8, "kind": "research", "status": "done", "result": "Found it.",
                              "sources": ["https://chartjs.org", "https://example.com/compare"]})
    assert out["question"]["questions"][0]["text"] == "Research done. Here's the coding brief with what it found. Start?"
    assert "WHAT THE RESEARCH FOUND (use it):\nUse Chart.js: small and free.\nSources: https://chartjs.org" in out["brief"]
    assert "STEPS:" not in out["brief"] and sent and sent[-1]["question"]["id"] == out["question"]["id"]
    assert len(started) == 1                                                   # "always": waits for the second go
    _tap(client, out, "go")
    assert started[-1][0] == "start" and started[-1][4]["chain_step"] == "2 of 2" and "Chart.js" in started[-1][1]


def test_never_starts_the_coding_by_itself_after_the_research(client, brain, started, monkeypatch):
    _chain_started(client, brain, started)
    _mode("never")
    monkeypatch.setattr(app.voice_io, "push_event", lambda e: None)
    monkeypatch.setattr(app.voice_io, "announce", lambda *a, **k: None)
    managing.after_job({"id": 8, "kind": "research", "status": "done", "result": "Use Chart.js.", "sources": []})
    assert started[-1][0] == "start"


def test_failed_research_asks_start_without_it_or_drop(client, brain, started, monkeypatch):
    _chain_started(client, brain, started)
    monkeypatch.setattr(app.voice_io, "push_event", lambda e: None)
    monkeypatch.setattr(app.voice_io, "announce", lambda *a, **k: None)
    out = managing.after_job({"id": 8, "kind": "research", "status": "failed", "error": "no pages could be read"})
    assert [c["label"] for c in out["question"]["questions"][0]["choices"]] == ["Start coding without it", "Drop it"]
    r = _tap(client, out, "drop")
    assert r["reply"] == "Okay, I dropped it." and len(started) == 1


def test_the_blocked_card_goes_to_telegram_when_the_user_is_away(client, brain, started, monkeypatch):
    _chain_started(client, brain, started)
    phoned = []
    monkeypatch.setattr(app.integrations, "user_away", lambda: True)
    monkeypatch.setattr(app.integrations.phone, "reply", lambda out: phoned.append(out))
    monkeypatch.setattr(app.voice_io, "push_event", lambda e: None)
    monkeypatch.setattr(app.voice_io, "announce", lambda *a, **k: None)
    monkeypatch.setattr(workers, "report", lambda which="latest", max_chars=1500, rec=None: "Use Chart.js.")
    managing.after_job({"id": 8, "kind": "research", "status": "done", "result": "x", "sources": []})
    assert phoned and phoned[0]["question"]["questions"][0]["key"] == "go"


def test_other_jobs_finishing_are_not_the_managers_business():
    assert managing.after_job({"id": 99, "kind": "research", "status": "done"}) is None
