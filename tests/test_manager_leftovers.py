"""Manager plan leftovers (4c-1, 4c-3, 4d-1 "not built", section 6 "Not now"):
- a re-plan (stuck, or a laptop task's read-back failed) comes from the cloud brain first when one is on, else the
  local deep brain - sent the goal and one-line step outcomes only, never raw tool results;
- a research job whose check fails goes back once ("read more pages, cite at least 2"), then is reported;
- a worker at its usage limit stops the job as `limit` and a card asks "Carry on with <other> (cost)" / "Wait" -
  the other worker never starts without the answer."""
import json
import sys

import pytest

from nova import store, task_state
from nova.actions import coworker, jobrounds, tools, workers
from nova.brain import cloud, router
from nova.core import app
from nova.core.app import integrations, managing, session, watching
from tests.test_research_worker import _fake_web, _hooks, _wait
from tests.test_workers import git, repo, wait_for  # noqa: F401  (fixture)

PLAN = "1. Open the Downloads folder - File Explorer - the folder shows\n2. Sort the files by type - File Explorer - sorted"
RAW = "RAW-RESULT: invoice_2026.pdf 4,213 bytes, card ending 4242"


def _settings(**kw):
    s = app.settings()
    s.update(kw)
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


@pytest.fixture
def real_planner(monkeypatch):
    """conftest says there is no planner: these tests use the real cloud-first choice (the brains are faked)."""
    monkeypatch.setattr(watching, "make_plan", watching.plan_from_brains)


@pytest.fixture
def cloud_on(monkeypatch):
    _settings(provider="google", cloud_models={"google": "gemini-x"}, brain_mode="cloud_first")
    monkeypatch.setattr(cloud, "get_key", lambda p: "k")


def _card(goal="tidy my downloads folder"):
    card = task_state.new(goal)
    task_state.record(card, {"name": "list_folder", "args": {"path": "C:/Users/someone/Downloads"}},
                      RAW + "\nmore raw lines from the tool", True)
    task_state.record(card, {"name": "click_at", "args": {"x": 1, "y": 2}}, "Clicked. " + RAW, False)
    return card


# ------------------------------------------------------------------ 1. re-plan: cloud first, outcomes only

def test_the_cloud_replan_is_sent_step_outcomes_never_raw_results(monkeypatch, real_planner, cloud_on):
    sent = []
    monkeypatch.setattr(cloud, "chat", lambda model, msgs, tools_, reasoning="low", on_think=None: (
        sent.append((model, msgs)), ({}, PLAN, []))[1])
    monkeypatch.setattr(router, "make_plan", lambda *a, **k: pytest.fail("the deep brain was asked"))
    card = _card()
    assert watching.replan({"card": card}, "Stuck: it did the same step 3 times.")
    model, msgs = sent[0]
    body = json.dumps(msgs)
    assert model == "cloud:google:gemini-x"
    assert "tidy my downloads folder" in body and "list_folder: done" in body and "click_at: failed" in body
    assert "RAW-RESULT" not in body and "4242" not in body and "invoice" not in body and "more raw" not in body
    assert "C:/Users/someone" not in body and "(not shared" in body          # no folder paths to the cloud either
    assert card["source"] == "cloud" and card["plan"][0]["text"].startswith("Open the Downloads folder")


def test_no_cloud_brain_means_the_deep_brain_plans(monkeypatch, real_planner):
    _settings(provider="ollama")
    monkeypatch.setattr(cloud, "chat", lambda *a, **k: pytest.fail("a cloud brain was asked"))
    asked = []
    monkeypatch.setattr(router, "make_plan", lambda s, goal, on_think=None: (asked.append(goal), (PLAN, "deepseek-r1:8b"))[1])
    card = _card()
    assert watching.replan({"card": card}, "Stuck: it did the same step 3 times.")
    assert card["source"] == "deep brain" and "RAW-RESULT" not in asked[0]


def test_a_cloud_brain_that_fails_or_mail_kept_local_falls_back_to_the_deep_brain(monkeypatch, real_planner, cloud_on):
    def limited(*a, **k):
        raise cloud.CloudError("limit", code=429)
    monkeypatch.setattr(cloud, "chat", limited)
    monkeypatch.setattr(router, "make_plan", lambda s, goal, on_think=None: (PLAN, "deepseek-r1:8b"))
    card = _card()
    assert watching.replan({"card": card}, "Stuck.") and card["source"] == "deep brain"
    # her mail never goes to a cloud brain unless ⚙ allows it
    monkeypatch.setattr(cloud, "chat", lambda *a, **k: pytest.fail("mail went to the cloud"))
    _settings(provider="google", cloud_models={"google": "gemini-x"}, email_cloud=False)
    assert watching.cloud_planner(app.settings(), "check my email and reply to Sam") is None
    assert watching.cloud_planner(app.settings(), "tidy my downloads") == "cloud:google:gemini-x"


def test_local_first_replans_with_the_cloud_brain_it_would_climb_to(monkeypatch, cloud_on):
    _settings(provider="google", cloud_models={"google": "gemini-x"}, brain_mode="local_first")
    assert watching.cloud_planner(app.settings(), "tidy my downloads") == "cloud:google:gemini-x"


def test_a_failed_read_back_gets_one_new_plan_for_the_one_more_go(brain, monkeypatch, tmp_path):
    _settings(autonomy="full")
    goals = []
    monkeypatch.setattr(watching, "make_plan", lambda goal: (goals.append(goal), (
        "1. Write the file again - write_text_file - it is there\n2. Read it back - read_text_file - same text",
        "cloud:google:gemini-x"))[1])
    target = tmp_path / "multi.txt"
    fake, ran = brain([("", [("write_text_file", {"path": str(target), "content": "step one done"})]),
                       ("Saved it!", []), ("Saved it, really!", [])], tool_output=f"Wrote {target}")
    out, _ = app.answer(f"write step one done into {target}")
    assert len(goals) == 1                                                       # one new plan, not one per stop
    assert "A check by code failed: multi.txt is missing" in goals[0] and "write_text_file: " in goals[0]
    assert f"Wrote {target}" not in goals[0]                                     # outcomes only
    note = next(m["content"] for m in app.state["messages"] if m["role"] == "user" and "A new plan is on the card"
                in str(m["content"]))
    assert "Write the file again" in note
    card = app.state["task"]["card"]
    assert card["source"] == "cloud"
    assert out["reply"] == "I don't think I finished that - multi.txt is missing. Want me to try again?"


# ------------------------------------------------------------------ 2. research: sent back once

def _research(monkeypatch, tmp_path, writes):
    _fake_web(monkeypatch, tmp_path)
    done, prompts = [], []

    def write(prompt, n):
        prompts.append(prompt)
        return writes[min(len(prompts), len(writes)) - 1]
    _hooks(monkeypatch, done, cloud=write)
    tools.research_in_background("green tea")
    assert _wait(lambda: done) and done[0]["status"] == "done", done and done[0]["error"]
    return workers.last_record("latest", need_files=False, kinds=("research",)), prompts


ONE = "Headline: Green tea is good.\n\n1. Antioxidants [1]\n2. Caffeine [1]"
TWO = "Headline: Green tea is good.\n\n1. Antioxidants [1]\n2. Caffeine [2]"


def test_a_research_job_citing_one_source_is_sent_back_once_then_reported(monkeypatch, tmp_path):
    notes = []
    monkeypatch.setattr(coworker, "_note", notes.append)
    rec, prompts = _research(monkeypatch, tmp_path, [ONE])
    assert len(prompts) == 2                                                     # sent back once, not again
    assert "this check failed: sources: 1 source cited (need 2)" in prompts[1] and "cite at least 2" in prompts[1]
    assert rec["sent_back"] == 1 and rec["status"] == "done" and rec["verdict"] == "failed"
    assert "sent it back once to read more pages and it still fails" in rec["result"]
    assert any("sending the research back once" in n for n in notes)


def test_a_research_send_back_that_fixes_it_says_so(monkeypatch, tmp_path):
    rec, prompts = _research(monkeypatch, tmp_path, [ONE, TWO])
    assert len(prompts) == 2 and rec["verdict"] == "passed" and rec["sent_back"] == 1
    assert "sent it back once to read more pages and it fixed it" in rec["result"]
    assert rec["path"].endswith("Research - green tea (2).txt")                 # the second report is the one kept


def test_research_send_back_keeps_the_limits(monkeypatch, tmp_path):
    store.current().workers.write_text(json.dumps({"manager": {"send_backs": 0}}), encoding="utf-8")
    rec, prompts = _research(monkeypatch, tmp_path, [ONE])
    assert len(prompts) == 1 and not rec.get("sent_back") and rec["verdict"] == "failed"
    store.current().workers.write_text(json.dumps({"manager": {"job_min": 0.0001}}), encoding="utf-8")
    monkeypatch.setattr(jobrounds.jobchecks.time, "time", lambda: 10 ** 10)
    rec = {"started": 1, "focus": ""}
    checks = jobrounds.research_round({"id": 1, "sources": ["a"], "cited": ["a"], "path": ""}, rec, 3,
                                      workers.config(), lambda f, n: pytest.fail("sent back past the time limit"))
    assert rec["limit"].startswith("time") and jobchecks_failed(checks)
    assert "a limit was reached (time" in jobrounds.research_words(rec, 4)


def jobchecks_failed(checks):
    return any(c["state"] == "failed" for c in checks)


# ------------------------------------------------------------------ 3. a worker at its usage limit

FAKE = r'''
import json, sys
worker = sys.argv[1]
if worker == "opencode":
    with open("README.md", "a", encoding="utf-8") as f:
        f.write("half done\n")
    print(json.dumps({"type": "error", "error": {"message": "Rate limit exceeded", "status": 429}}), flush=True)
    sys.exit(1)
with open("claude_was_here.txt", "w") as f:
    f.write("finished")
print(json.dumps({"result": "Finished the job.", "total_cost_usd": 0.2}), flush=True)
'''


@pytest.fixture
def limited_workers(tmp_path, monkeypatch):
    """OpenCode edits README.md, then every model says 429; Claude Code (when it runs) finishes the job."""
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE, encoding="utf-8")
    started = []
    monkeypatch.setattr(workers, "find_exe", lambda w, conf=None: sys.executable)
    monkeypatch.setattr(workers, "command", lambda w, brief, model, conf, title="": (
        started.append((w, model, brief)), ([sys.executable, str(script), w], {}))[1])
    monkeypatch.setattr(workers, "config", lambda: {
        "opencode": {**workers.DEFAULTS["opencode"], "model": "deepseek", "fallback_models": ["opencode/big-pickle"],
                     "timeout_min": 1},
        "claude": {**workers.DEFAULTS["claude"], "budget_usd": 1.0, "timeout_min": 1},
        "manager": {}, "daily_budget_usd": 3.0})
    monkeypatch.setattr(store.current(), "root", tmp_path)
    monkeypatch.setattr(workers, "claude_signed_in", lambda conf=None, fresh=False: True)
    monkeypatch.setitem(coworker._hooks, "on_done", integrations.job_done)
    workers._busy.clear()
    workers._lines.clear()
    return started


def test_a_429_limited_job_asks_and_never_starts_the_other_worker_by_itself(repo, limited_workers):  # noqa: F811
    msg = workers.start("Fix the typo in README", str(repo), worker="opencode")
    assert msg.startswith("Started background job #"), msg
    job = coworker.jobs[-1]
    assert wait_for(lambda: session.state.get("question"))
    assert [(w, m) for w, m, _ in limited_workers] == [("opencode", "deepseek"), ("opencode", "opencode/big-pickle")]
    rec = workers.load_jobs()[-1]
    assert rec["status"] == "limit" and rec["limit"].startswith("usage") and rec["files"] == ["README.md"]
    assert rec["handoff"] == "claude" and "hit its usage limit" in job["result"] and "haven't switched" in job["result"]
    q = session.state["question"]
    assert q["owner"] == "limit"
    assert [c["label"] for c in q["questions"][0]["choices"]] == ["Carry on with Claude Code, paid, up to $1.00", "Wait"]
    assert len(limited_workers) == 2                                     # nothing else started while it waits

    said = managing._limit_answered(q, {"handoff": {"value": "wait"}})
    assert "I'll wait - nothing switched" in said["reply"] and len(limited_workers) == 2

    said = managing._limit_answered(q, {"handoff": {"value": "go"}})
    assert said["reply"].startswith("Started background job")
    assert wait_for(lambda: len(limited_workers) == 3) and limited_workers[-1][0] == "claude"
    brief = limited_workers[-1][2]
    assert brief.startswith("Fix the typo in README") and "stopped at its usage limit" in brief and "README.md" in brief
    new = coworker.jobs[-1]
    assert wait_for(lambda: new["status"] != "working")
    recs = workers.load_jobs()
    assert recs[-1]["worker"] == "claude" and recs[-1]["continues"] == rec["job"]
    assert set(recs[-1]["files"]) == {"README.md", "claude_was_here.txt"}            # same folder state, carried on
    assert recs[-2]["handed_over"] is True
    assert "isn't waiting" in jobrounds.hand_over(recs[-2])                          # once only


def test_claude_codes_usage_limit_is_found_only_in_its_error():
    assert jobrounds.usage_limited("claude", {"error": "Claude AI usage limit reached|1764000000", "text": ""})
    assert jobrounds.usage_limited("claude", {"error": "You've hit your limit · resets 3pm"})
    assert not jobrounds.usage_limited("claude", {"error": "", "text": "Added rate limiting to the API."})
    assert not jobrounds.usage_limited("opencode", {"error": "model crashed", "limit": False})
    assert jobrounds.usage_limited("opencode", {"error": "Rate limit exceeded", "limit": True})


def test_continue_on_a_usage_limited_job_hands_it_to_the_other_worker():
    rec = {"job": 3, "status": "limit", "handoff": "claude", "worker": "opencode", "brief": "x", "files": []}
    workers._save_record({**rec, "key": "1-3"})
    got, w = workers.retry_target("latest")
    assert got["job"] == 3 and w == "claude"
