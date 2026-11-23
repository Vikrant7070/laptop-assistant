"""Manager plan 4c-2: a finished worker job is checked by code, not by the worker's word.

Coding: files changed only inside the folder, and the project's tests by the command found from its files (real
pytest runs on tiny temp projects). Research: at least 2 sources cited, the report file is really there. A check that
can't run is "skipped, with why"; all skipped -> "done, not checked". /tasks shows the checks and "couldn't finish".
"""
import json
import sys

import pytest

from nova.actions import coworker, jobchecks, workers
from nova.core.app import tasks_page
from tests.test_workers import fake_worker, git, repo, run_job  # noqa: F401  (fixtures)

PASSING = "def test_readme():\n    assert 'fixed typo' in open('README.md').read()\n"
FAILING = "def test_readme_is_perfect():\n    assert 'never there' in open('README.md').read()\n"


@pytest.fixture
def py_here(monkeypatch):
    """The project's 'venv' is this Python (it has pytest), so the test command is found and really runs."""
    monkeypatch.setattr(jobchecks, "_venv_python", lambda folder: sys.executable)


def _pyproj(folder, test=PASSING):
    (folder / "pyproject.toml").write_text("[project]\nname = 'p'\nversion = '0'\n", encoding="utf-8")
    (folder / "README.md").write_text("fixed typo\n", encoding="utf-8")
    if test is not None:
        (folder / "test_readme.py").write_text(test, encoding="utf-8")


# ------------------------------------------------------------------ the test command, from the project's files

def test_command_found_from_the_project(tmp_path, monkeypatch):
    found = {"pytest": "C:/py/pytest.exe", "npm": "C:/n/npm.cmd", "pnpm": "C:/n/pnpm.cmd", "cargo": "cargo.exe",
             "go": "go.exe"}
    monkeypatch.setattr(jobchecks, "_which", found.get)
    monkeypatch.setattr(jobchecks, "_venv_python", lambda f: "")

    def at(name, files):
        d = tmp_path / name
        d.mkdir()
        for f, text in files.items():
            (d / f).mkdir() if text is None else (d / f).write_text(text, encoding="utf-8")
        return jobchecks.test_command(d)

    assert at("py", {"pyproject.toml": ""})[0][:2] == ["C:/py/pytest.exe", "-q"]
    assert at("setup", {"setup.py": ""})[0][0] == "C:/py/pytest.exe"
    npm = json.dumps({"scripts": {"test": "jest"}})
    assert at("npm", {"package.json": npm, "node_modules": None})[0] == ["C:/n/npm.cmd", "test"]
    assert at("pnpm", {"package.json": npm, "pnpm-lock.yaml": "", "node_modules": None})[0] == ["C:/n/pnpm.cmd", "test"]
    assert at("yarn", {"package.json": npm, "yarn.lock": "", "node_modules": None}) == (None, "yarn isn't installed")
    assert at("rust", {"Cargo.toml": ""})[0] == ["cargo.exe", "test"]
    assert at("go", {"go.mod": ""})[0] == ["go.exe", "test", "./..."]
    # can't run -> why, never a guess
    assert at("none", {"index.html": ""}) == (None, "no test command found")
    default = json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}})
    assert at("npmdefault", {"package.json": default}) == (None, "package.json has no test script")
    assert "no node_modules" in at("npmbare", {"package.json": npm})[1]
    monkeypatch.setattr(jobchecks, "_which", lambda n: None)
    assert "pytest isn't installed" in at("nopytest", {"pyproject.toml": ""})[1]


def test_the_project_s_own_venv_comes_first(tmp_path, monkeypatch):
    monkeypatch.setattr(jobchecks, "_which", lambda n: "C:/elsewhere/pytest.exe")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    py = tmp_path / ".venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    assert jobchecks.test_command(tmp_path)[0][:3] == [str(py), "-m", "pytest"]


# ------------------------------------------------------------------ running the tests (real pytest)

def test_tests_that_pass_fail_or_don_t_exist(tmp_path, py_here):
    _pyproj(tmp_path)
    c = jobchecks.coding(tmp_path, tmp_path, ["README.md"])
    assert [x["state"] for x in c] == ["passed", "passed"] and "1 passed" in c[1]["why"]
    (tmp_path / "test_readme.py").write_text(FAILING, encoding="utf-8")
    t = jobchecks.coding(tmp_path, tmp_path, ["README.md"])[1]
    assert t["state"] == "failed" and "1 failed" in t["why"] and "test_readme_is_perfect" in t["why"]
    assert "never there" in t["detail"]                       # the exact failing output, for the send-back (4c-3)
    (tmp_path / "test_readme.py").unlink()
    t = jobchecks.coding(tmp_path, tmp_path, ["README.md"])[1]
    assert t == {"name": "tests", "state": "skipped", "why": "the project has no tests yet"}
    assert not (tmp_path / ".pytest_cache").exists()          # the run leaves nothing behind in the project


def test_a_test_run_that_hangs_is_stopped_and_fails(tmp_path):
    t = jobchecks.run_tests(tmp_path, [sys.executable, "-c", "import time; time.sleep(30)"], secs=1)
    assert t["state"] == "failed" and "ran longer than 1 min" in t["why"]


def test_stopping_the_job_stops_its_tests(tmp_path):
    calls = []

    def checkpoint():
        calls.append(1)
        if len(calls) > 2:
            raise coworker.Stopped()
    with pytest.raises(coworker.Stopped):
        jobchecks.run_tests(tmp_path, [sys.executable, "-c", "import time; time.sleep(30)"], checkpoint=checkpoint)


def test_tests_found_above_the_job_folder_up_to_the_project(tmp_path, py_here):
    _pyproj(tmp_path)
    sub = tmp_path / "src"
    sub.mkdir()
    c = jobchecks.coding(tmp_path, sub, ["src/a.py"])
    assert c[1]["state"] == "passed"


# ------------------------------------------------------------------ files only inside the folder

def test_files_changed_outside_the_job_folder_fail():
    assert jobchecks.files_inside("C:/p", "C:/p", ["a.py", "b/c.py"])["state"] == "passed"
    assert jobchecks.files_inside("C:/p", "C:/p/web", ["web/a.js", "web/b/c.js"])["state"] == "passed"
    bad = jobchecks.files_inside("C:/p", "C:/p/web", ["web/a.js", "server/db.py"])
    assert bad["state"] == "failed" and "server/db.py" in bad["why"] and "web/a.js" not in bad["why"]
    assert jobchecks.files_inside("C:/p", "C:/p", [])["state"] == "skipped"
    assert jobchecks.files_inside("C:/p/x", "C:/PROGRA~1/x", ["a.py"])["state"] == "passed"   # spelled differently


# ------------------------------------------------------------------ research

def test_research_checks_catch_a_missing_report_and_too_few_sources(tmp_path):
    report = tmp_path / "Research - tea.txt"
    report.write_text("Headline: tea [1] [2]", encoding="utf-8")
    ok = jobchecks.research({"path": str(report), "sources": ["a", "b", "c"], "cited": ["a", "b"]}, 3)
    assert jobchecks.verdict(ok) == "passed" and ok[0]["why"] == "2 sources cited"
    # a fake "saved": the job says it wrote the report, the file isn't there
    gone = jobchecks.research({"path": str(tmp_path / "missing.txt"), "sources": ["a", "b"], "cited": ["a", "b"]})
    assert jobchecks.verdict(gone) == "failed" and "the report file is missing" in gone[1]["why"]
    assert jobchecks.left(gone) == [f"report: the report file is missing ({tmp_path / 'missing.txt'})"]
    one = jobchecks.research({"path": str(report), "sources": ["a", "b"], "cited": ["a"]}, 3)
    assert one[0] == {"name": "sources", "state": "failed", "why": "1 source cited (need 2)"}
    # the user asked for one source: one is enough
    assert jobchecks.research({"path": str(report), "sources": ["a"], "cited": ["a"]}, 1)[0]["state"] == "passed"
    assert jobchecks.research({"path": "", "sources": []})[1]["why"] == "no report file was saved"


# ------------------------------------------------------------------ verdict and words

def test_verdict_and_summary():
    p = {"name": "files", "state": "passed", "why": "1 changed"}
    s = {"name": "tests", "state": "skipped", "why": "no test command found"}
    f = {"name": "tests", "state": "failed", "why": "pytest -q: 1 failed"}
    assert jobchecks.verdict([]) == "" and jobchecks.summary([]) == ""
    assert jobchecks.verdict([p, s]) == "passed"
    assert jobchecks.summary([p, s]) == "Checks: files passed; tests: skipped - no test command found."
    assert jobchecks.verdict([s]) == "not checked"
    assert jobchecks.summary([s]).startswith("Done, not checked (tests: skipped")
    assert jobchecks.verdict([p, f]) == "failed" and jobchecks.summary([p, f]) == "But a check failed - tests: pytest -q: 1 failed."


def test_what_the_worker_says_it_left():
    said = "Fixed the README.\nLeft to do: the Docs page links\n"
    assert jobchecks.worker_left(said) == ["the Docs page links"]
    assert jobchecks.worker_left("Done.\nLeft to do: nothing") == []
    assert jobchecks.worker_left("All good.") == []
    f = {"name": "tests", "state": "failed", "why": "1 failed"}
    assert jobchecks.left([f], said) == ["tests: 1 failed", "the Docs page links"]
    assert "Left to do:" in workers.BRIEF_RULES


# ------------------------------------------------------------------ in a real job (the fake worker edits README.md)

def test_a_project_with_no_test_command_shows_tests_skipped(repo, fake_worker):  # noqa: F811
    job, _ = run_job(repo)
    assert "tests: skipped - no test command found" in job["result"]
    assert job["result"].index("tests: skipped") < job["result"].index("Nothing is committed")
    rec = workers.load_jobs()[-1]
    assert rec["verdict"] == "passed" and [c["state"] for c in rec["checks"]] == ["passed", "skipped"]


def test_a_job_whose_tests_fail_is_reported_plainly(repo, fake_worker, py_here):  # noqa: F811
    _pyproj(repo, FAILING)
    (repo / "README.md").write_text("Helo world\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "tests")
    job, _ = run_job(repo)
    assert job["status"] == "done" and "But a check failed - tests:" in job["result"]
    assert "test_readme_is_perfect" in job["result"]
    rec = workers.load_jobs()[-1]
    assert rec["verdict"] == "failed" and rec["left"][0].startswith("tests: ")
    row = tasks_page.job_row(rec)
    assert row["verdict"] is False and row["not_checked"] is False
    d = tasks_page.detail(row["id"])
    assert [c["state"] for c in d["checks"]] == ["passed", "failed"] and d["left"] == rec["left"]


def test_a_job_whose_tests_pass(repo, fake_worker, py_here):  # noqa: F811
    _pyproj(repo, PASSING)
    (repo / "README.md").write_text("Helo world\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "tests")
    job, _ = run_job(repo)
    assert "Checks: files passed; tests passed." in job["result"]
    assert tasks_page.job_row(workers.load_jobs()[-1])["verdict"] is True


def test_old_records_and_not_checked_on_the_tasks_page(client):
    workers._save_record({"key": "old", "job": 1, "worker": "opencode", "status": "done", "started": 1.0,
                          "finished": 2.0, "brief": "an old job", "files": ["a"]})
    workers._save_record({"key": "nc", "job": 2, "worker": "research", "status": "done", "started": 3.0,
                          "finished": 4.0, "brief": "tea", "verdict": "not checked",
                          "checks": [{"name": "report", "state": "skipped", "why": "x"}], "left": []})
    rows = {r["goal"]: r for r in client.get("/api/tasks").get_json()["rows"]}
    assert rows["an old job"]["verdict"] is None and rows["an old job"]["not_checked"] is False
    assert rows["tea"]["verdict"] is None and rows["tea"]["not_checked"] is True
    d = client.get("/api/tasks/j:old").get_json()
    assert d["checks"] == [] and d["left"] == []


def test_a_real_research_job_is_checked(monkeypatch, tmp_path):
    from nova.actions import tools
    from tests.test_research_worker import _fake_web, _hooks, _wait
    _fake_web(monkeypatch, tmp_path)
    done = []
    _hooks(monkeypatch, done)                               # the report cites only [1]
    tools.research_in_background("green tea")
    assert _wait(lambda: done) and done[0]["status"] == "done"
    rec = workers.last_record("latest", need_files=False, kinds=("research",))
    assert {c["name"]: c["state"] for c in rec["checks"]} == {"sources": "failed", "report": "passed"}
    assert rec["verdict"] == "failed" and rec["left"] == ["sources: 1 source cited (need 2)"]
    assert "But a check failed - sources: 1 source cited (need 2)." in rec["result"]
