"""Manager plan 4c-3: a failed check goes back once to the same worker with the exact failing check; still failing ->
the user is told plainly. Limits (rounds, time, money) stop it with the reason, and "try job #n again" continues.
The brief guard: files changed outside the job's folder are undone and the job stops.

The fake worker (tests/test_workers.py) appends "fixed typo" to README.md on every run, so a project test that wants
it twice passes only after one send-back round.
"""
import sys

import pytest

from nova.actions import coworker, jobchecks, jobrounds, workers
from nova.core.app import tasks_page
from tests.test_workers import fake_worker, git, repo, run_job, wait_for  # noqa: F401  (fixtures)

TWICE = "def test_fixed_twice():\n    assert open('README.md').read().count('fixed typo') >= 2\n"
NEVER = "def test_readme_is_perfect():\n    assert 'never there' in open('README.md').read()\n"


@pytest.fixture
def briefs(fake_worker, monkeypatch):  # noqa: F811
    """Every brief the worker got, in order (the fake worker's command, wrapped)."""
    seen, real = [], workers.command
    monkeypatch.setattr(workers, "command", lambda w, brief, model, conf, title="": (seen.append(brief), real(
        w, brief, model, conf, title))[1])
    monkeypatch.setattr(jobchecks, "_venv_python", lambda folder: sys.executable)
    return seen


def _manager(monkeypatch, **manager):
    real = workers.config()
    monkeypatch.setattr(workers, "config", lambda: {**real, "manager": manager})


def _tests(repo, text):  # noqa: F811
    (repo / "pyproject.toml").write_text("[project]\nname = 'p'\nversion = '0'\n", encoding="utf-8")
    (repo / "test_readme.py").write_text(text, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "tests")


def test_one_send_back_fixes_it(repo, briefs):  # noqa: F811
    _tests(repo, TWICE)
    job, _ = run_job(repo)
    assert len(briefs) == 2
    assert "Nova checked your work and this check failed" in briefs[1] and "test_fixed_twice" in briefs[1]
    assert briefs[1].startswith("Fix the typo in README") and "Left to do:" in briefs[1]     # same brief + rules
    assert "I sent it back once and it fixed it" in job["result"] and "tests passed" in job["result"]
    rec = workers.load_jobs()[-1]
    assert rec["verdict"] == "passed" and rec["sent_back"] == 1 and rec["status"] == "done"


def test_still_failing_after_one_send_back_tells_the_user(repo, briefs):  # noqa: F811
    _tests(repo, NEVER)
    job, _ = run_job(repo)
    assert len(briefs) == 2                                        # one send-back round, then it stops
    assert "still fails - over to you" in job["result"] and "test_readme_is_perfect" in job["result"]
    rec = workers.load_jobs()[-1]
    assert rec["verdict"] == "failed" and rec["status"] == "done" and not rec.get("limit")
    assert tasks_page.detail("j:" + rec["key"])["sent_back"] == 1


def test_sending_back_can_be_turned_off(repo, briefs, monkeypatch):  # noqa: F811
    _manager(monkeypatch, send_backs=0)
    _tests(repo, NEVER)
    job, _ = run_job(repo)
    assert len(briefs) == 1 and "But a check failed" in job["result"] and "sent it back" not in job["result"]


def test_time_limit_stops_it_and_try_again_continues(repo, briefs, monkeypatch):  # noqa: F811
    _manager(monkeypatch, job_min=0.0001)
    _tests(repo, TWICE)
    job, _ = run_job(repo)
    assert len(briefs) == 1
    rec = workers.load_jobs()[-1]
    assert rec["status"] == "limit" and rec["limit"].startswith("time:")
    assert "a limit was reached (time:" in job["result"] and f"try job #{job['id']} again" in job["result"]
    row = tasks_page.job_row(rec)
    assert row["status"] == "stopped" and row["why"].startswith("limit reached - time:")
    acts = tasks_page.detail(row["id"])["actions"]
    assert {"do": "chat", "label": "▶ Continue…", "say": f"try job #{job['id']} again"} in acts
    # Continue: the held-back round runs as a new job with the failing check, same worker, and is not sent back again
    _manager(monkeypatch)
    assert workers.retry_target(str(job["id"]))[1] == "opencode"
    said = workers.retry(str(job["id"]))
    assert said.startswith("Started background job #"), said
    nxt = coworker.jobs[-1]
    assert wait_for(lambda: nxt["status"] != "working"), nxt
    assert len(briefs) == 2 and "Nova checked your work" in briefs[1]
    new = workers.load_jobs()[-1]
    assert new["continues"] == job["id"] and new["sent_back"] == 1 and new["verdict"] == "passed"
    old = next(r for r in workers.load_jobs() if r["key"] == rec["key"])
    assert old["continued"] is True and not jobrounds.can_continue(old)


def test_money_limit_for_claude_code(repo, briefs, monkeypatch):  # noqa: F811
    monkeypatch.setattr(workers, "spent_today", lambda now=None: 2.5)          # daily 3.00, one round up to 1.00
    _tests(repo, NEVER)
    job, _ = run_job(repo, worker="claude")
    assert len(briefs) == 1
    rec = workers.load_jobs()[-1]
    assert rec["status"] == "limit" and rec["limit"].startswith("money:") and "$3.00" in rec["limit"]


def test_files_changed_outside_the_job_folder_are_undone_and_the_job_stops(repo, fake_worker):  # noqa: F811
    (repo / "web").mkdir()
    (repo / "web" / "index.html").write_text("<p>hi</p>\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "web")
    msg = workers.start("Fix the page", str(repo / "web"))                  # the fake edits README.md at the top
    assert msg.startswith("Started"), msg
    job = coworker.jobs[-1]
    assert wait_for(lambda: job["status"] != "working"), job
    assert job["status"] == "failed" and "outside the job's folder (README.md)" in job["error"]
    assert (repo / "README.md").read_text() == "Helo world\n"               # put back
    rec = workers.load_jobs()[-1]
    assert rec["status"] == "failed" and rec["guard"] == ["README.md (put back)"] and rec["files"] == []
    assert rec["left"][0].startswith("files changed outside the job's folder")
    # a new file outside the folder is moved aside, never deleted
    fake_worker["v"] = "pwd"
    workers.start("Fix the page again", str(repo / "web"))
    job = coworker.jobs[-1]
    assert wait_for(lambda: job["status"] != "working"), job
    rec = workers.load_jobs()[-1]
    assert not (repo / "pwd.txt").exists() and rec["guard"][0].startswith("pwd.txt (moved to ")
    assert (workers.store.current().job_logs / f"{rec['key']}-undone" / "pwd.txt").exists()


def test_limit_rules_and_the_send_back_brief():
    m, conf = {}, {"claude": {"budget_usd": 1.0}, "daily_budget_usd": 3.0}
    assert jobchecks.limit_hit(m, "opencode", started=100, sent_back=0, now=200) == ""
    assert jobchecks.limit_hit(m, "opencode", 100, 1, now=200).startswith("rounds: it was already sent back 1 time")
    assert jobchecks.limit_hit({"send_backs": 0}, "opencode", 100, 0, now=200) == "rounds: sending back is turned off"
    assert jobchecks.limit_hit({"job_min": 1}, "opencode", 100, 0, now=200) == "time: the job has used its 1 minutes"
    assert jobchecks.limit_hit(m, "claude", 100, 0, conf, spent=1.5, now=200) == ""
    assert jobchecks.limit_hit(m, "claude", 100, 0, conf, spent=2.5, now=200).startswith("money:")
    assert jobchecks.limit_hit(m, "opencode", 100, 0, conf, spent=99, now=200) == ""          # free: no money limit
    b = jobchecks.send_back_brief("Do X", [{"name": "files", "state": "passed", "why": "ok"},
                                           {"name": "tests", "state": "failed", "why": "1 failed", "detail": "E assert 1"}])
    assert b.startswith("Do X\n\nNova checked your work") and "- tests: 1 failed" in b and "E assert 1" in b
    assert "files" not in b.split("failed:")[1].split("\n\n")[0]
