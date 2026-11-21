"""Manager plan phase 3: coding workers run in the background, never commit or push, one per folder; Nova reports.

The worker is a fake: a small Python script (written per test) that prints OpenCode-style JSON lines and edits files
in the real temporary git repo it is started in - so the git guard, the "after" check and stopping are all real.
"""
import json
import subprocess
import sys
import time

import pytest

from nova import store
from nova.core import app
from nova.actions import coworker
from nova.actions import tools
from nova.actions import workers

FAKE = r'''
import json, os, subprocess, sys, time
mode = sys.argv[1]
model = sys.argv[2]
def say(**e): print(json.dumps(e), flush=True)
say(type="tool_use", part={"tool": "edit", "state": {"input": {"filePath": "README.md"}}})
if mode == "limit" and model == "first":
    say(type="error", error={"message": "Free usage limit reached", "status": 429}); sys.exit(1)
if mode == "pwd":
    with open("pwd.txt", "w") as f:
        f.write(os.environ.get("PWD", ""))
if mode == "slow":
    time.sleep(30)
if mode == "fail":
    say(type="error", error={"message": "model crashed"}); sys.exit(1)
if mode not in ("nothing", "pwd"):
    with open("README.md", "a", encoding="utf-8") as f:
        f.write("fixed typo\n")
if mode == "commit":
    subprocess.run(["git", "commit", "-qam", "sneaky"], check=True)
if mode == "push":
    r = subprocess.run(["git", "push", "origin", "HEAD"], capture_output=True, text=True)
    with open("push.txt", "w") as f:
        f.write(f"{r.returncode}\n{r.stdout}{r.stderr}")
say(type="step_finish", part={"cost": 0.0})
say(type="text", part={"text": "Fixed the typo in README.md (model " + model + ")."})
'''


def git(folder, *args):
    return subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True, check=True).stdout


def wait_for(cond, secs=20):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "T")
    (r / "README.md").write_text("Helo world\n", encoding="utf-8")
    git(r, "add", ".")
    git(r, "commit", "-qm", "first")
    return r


@pytest.fixture
def fake_worker(tmp_path, monkeypatch):
    """Point both workers at the fake script. mode['v'] says what it does this run."""
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE, encoding="utf-8")
    mode = {"v": "edit"}
    monkeypatch.setattr(workers, "find_exe", lambda w, conf=None: sys.executable)
    monkeypatch.setattr(workers, "command",
                        lambda w, brief, model, conf, title="": ([sys.executable, str(script), mode["v"], model or "x"], {}))
    monkeypatch.setattr(workers, "config", lambda: {
        "opencode": {**workers.DEFAULTS["opencode"], "model": "first", "fallback_models": ["second"], "timeout_min": 1},
        "claude": {**workers.DEFAULTS["claude"], "budget_usd": 1.0, "timeout_min": 1},
        "daily_budget_usd": 3.0})
    monkeypatch.setattr(store.current(), "root", tmp_path)          # workers.json, jobs.json, jobs/ go here
    workers._busy.clear()
    workers._lines.clear()
    monkeypatch.setattr(workers, "claude_signed_in", lambda conf=None, fresh=False: True)
    return mode


def run_job(repo, **kw):
    msg = workers.start("Fix the typo in README", str(repo), **kw)
    assert msg.startswith("Started background job #"), msg
    job = coworker.jobs[-1]
    assert wait_for(lambda: job["status"] != "working"), job
    return job, msg


def test_job_edits_files_but_commits_nothing(repo, fake_worker):
    job, msg = run_job(repo)
    assert "OpenCode + DeepSeek" in msg and "Don't do the job yourself" in msg
    assert job["status"] == "done" and job["kind"] == "coding"
    assert job["files"] == ["README.md"]
    assert "Nothing is committed" in job["result"] and "README.md" in job["result"]
    assert "fixed typo" in (repo / "README.md").read_text()
    assert git(repo, "rev-list", "--count", "HEAD").strip() == "1"          # no new commit
    rec = workers.load_jobs()[-1]
    assert rec["status"] == "done" and rec["worker"] == "opencode" and rec["files"] == ["README.md"]
    assert "Fixed the typo" in rec["result"] and workers._busy == {}
    assert "changed 1 file: README.md" in coworker.summary()


def test_a_commit_that_slipped_through_is_undone(repo, fake_worker):
    fake_worker["v"] = "commit"
    job, _ = run_job(repo)
    assert git(repo, "rev-list", "--count", "HEAD").strip() == "1"          # the sneaky commit is gone...
    assert "fixed typo" in (repo / "README.md").read_text()                 # ...but the edit stays
    assert "against the rules" in job["result"] and job["files"] == ["README.md"]


def test_git_push_is_blocked_for_the_worker(repo, fake_worker):
    git(repo, "remote", "add", "origin", "https://example.invalid/nova/proj.git")
    fake_worker["v"] = "push"
    run_job(repo)
    code, out = (repo / "push.txt").read_text().split("\n", 1)
    assert code != "0" and "nova-push-blocked" in out                      # never reached the network


def test_worker_gets_the_job_folder_as_pwd(repo, fake_worker, monkeypatch):
    monkeypatch.setenv("PWD", "/c/somewhere/else")      # OpenCode followed an inherited PWD into the wrong project
    fake_worker["v"] = "pwd"
    run_job(repo)
    assert (repo / "pwd.txt").read_text() == str(repo)


def test_free_limit_moves_to_the_next_model(repo, fake_worker):
    fake_worker["v"] = "limit"
    job, _ = run_job(repo)
    assert job["status"] == "done" and job["model"] == "second"
    assert workers.load_jobs()[-1]["model"] == "second"


def test_failure_offers_claude_code(repo, fake_worker):
    fake_worker["v"] = "fail"
    job, _ = run_job(repo)
    assert job["status"] == "failed" and "model crashed" in job["error"]
    assert "Claude Code" in job["error"]
    assert workers.load_jobs()[-1]["status"] == "failed" and workers._busy == {}


def test_no_changes_is_said_plainly(repo, fake_worker):
    fake_worker["v"] = "nothing"
    job, _ = run_job(repo)
    assert job["status"] == "done" and "didn't change any files" in job["result"]


def test_second_job_waits_in_line_and_stop_kills_it(repo, fake_worker):
    fake_worker["v"] = "slow"
    workers.start("Fix the typo", str(repo))
    first = coworker.jobs[-1]
    assert wait_for(lambda: first["step"].startswith("editing README"))     # the live step from its JSON output
    fake_worker["v"] = "edit"
    msg = workers.start("Something else", str(repo))
    second = coworker.jobs[-1]
    assert msg.startswith(f"Queued background job #{second['id']}") and f"job #{first['id']}" in msg
    assert wait_for(lambda: second["step"] == f"waiting in line behind job #{first['id']}")
    assert any(r["status"] == "queued" and r["job"] == second["id"] for r in workers.load_jobs())
    assert "Stopped" in coworker.stop(str(first["id"]))                    # the first one ends...
    assert wait_for(lambda: first["status"] == "stopped", 15)
    assert wait_for(lambda: second["status"] == "done", 20), second         # ...and the next one runs
    assert second["files"] == ["README.md"] or "README.md" in second["result"] or second["files"] == []
    assert workers._busy == {} and workers._lines == {}


def test_a_waiting_job_can_be_stopped_and_the_line_is_capped(repo, fake_worker, monkeypatch):
    monkeypatch.setattr(workers, "MAX_LINE", 2)
    fake_worker["v"] = "slow"
    workers.start("first", str(repo))
    first = coworker.jobs[-1]
    workers.start("second", str(repo))
    second = coworker.jobs[-1]
    assert "already lined up" in workers.start("third", str(repo))
    assert wait_for(lambda: second["step"].startswith("waiting in line"))
    coworker.stop(str(second["id"]))
    assert wait_for(lambda: second["status"] == "stopped", 10)
    assert wait_for(lambda: len(workers._lines.get(workers._norm(workers.git_root(str(repo))), [])) == 1, 5)
    coworker.stop(str(first["id"]))
    assert wait_for(lambda: first["status"] == "stopped", 15)
    assert wait_for(lambda: workers._lines == {} and workers._busy == {}, 5)


def test_record_shows_checking_then_done(repo, fake_worker, monkeypatch):
    seen = []
    real = workers._save_record
    monkeypatch.setattr(workers, "_save_record", lambda rec: (seen.append(rec["status"]), real(rec)))
    run_job(repo)
    assert seen[0] == "running" and "checking" in seen and seen[-1] == "done"


def test_claude_code_must_be_signed_in(repo, fake_worker, monkeypatch):
    monkeypatch.setattr(workers, "claude_signed_in", lambda conf=None, fresh=False: False)
    msg = workers.start("Build a site", str(repo), worker="claude")
    assert msg.startswith("Not started") and "auth login" in msg
    assert workers.start("Fix it", str(repo)).startswith("Started")         # OpenCode doesn't need it
    assert wait_for(lambda: coworker.jobs[-1]["status"] != "working")


def test_not_logged_in_from_the_worker_becomes_sign_in_help(repo, fake_worker, monkeypatch):
    monkeypatch.setattr(workers, "claude_signed_in", lambda conf=None, fresh=False: None)   # unknown -> try
    monkeypatch.setattr(workers, "run_once", lambda *a, **k: {"text": "", "cost": 0.0, "denied": [], "lines": [],
                                                              "error": "Not logged in · Please run /login"})
    job, _ = run_job(repo, worker="claude")
    assert job["status"] == "failed" and "auth login" in job["error"]
    assert workers.SIGNED_IN["ok"] is False


def test_worker_env_drops_an_inherited_claude_session(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "x")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "kept")
    env = workers._guard_env()
    assert "CLAUDE_CODE_SESSION_ID" not in env and "CLAUDECODE" not in env and "ANTHROPIC_BASE_URL" not in env
    assert env["ANTHROPIC_API_KEY"] == "kept"
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com")
    assert workers._guard_env()["ANTHROPIC_BASE_URL"] == "https://proxy.example.com"


def test_refusals_before_starting(repo, fake_worker, tmp_path, monkeypatch):
    assert "need the project folder" in workers.start("Fix it", "")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "isn't a git project" in workers.start("Fix it", str(plain))
    assert "brief is empty" in workers.start("  ", str(repo))
    monkeypatch.setattr(workers, "find_exe", lambda w, conf=None: "")
    assert "can't find OpenCode" in workers.start("Fix it", str(repo))


def test_claude_daily_budget(repo, fake_worker, monkeypatch):
    monkeypatch.setattr(workers, "spent_today", lambda now=None: 3.5)
    assert "daily limit" in workers.start("Build a site", str(repo), worker="claude")
    assert workers.start("Fix it", str(repo)).startswith("Started")         # the cheap worker isn't limited
    assert wait_for(lambda: coworker.jobs[-1]["status"] != "working")


def test_commit_takes_only_the_jobs_files(repo, fake_worker):
    (repo / "mine.txt").write_text("the user's own work\n")
    run_job(repo)
    msg = workers.commit("latest", "Fix README typo")
    assert "Committed 1 file" in msg and "Nothing was pushed" in msg
    assert git(repo, "log", "-1", "--format=%s").strip() == "Fix README typo"
    assert git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["README.md"]
    assert "mine.txt" in git(repo, "status", "--porcelain")                 # left alone
    assert "nothing to commit" in workers.commit("latest")
    assert workers.load_jobs()[-1]["committed"] is True


def test_tool_wrappers(repo, fake_worker):
    assert tools.start_coding_job("Fix the typo", str(repo)).startswith("Started background job")
    assert wait_for(lambda: coworker.jobs[-1]["status"] != "working")
    assert "Committed" in tools.commit_coding_job()


def test_coding_requests_always_get_the_coding_tools():
    from nova.brain import toolpick
    for q in [r"Fix the spelling mistakes in the README.md of my project at C:\Users\x\demo-site",
              "fix the bug in the login page", "refactor my code", "use OpenCode for this", "commit it",
              r"add a contact page to my web app in C:\code\site"]:
        assert "start_coding_job" in toolpick.pick(q) and "commit_coding_job" in toolpick.pick(q), q
    for q in ["open spotify", "what's the weather", "close my app", "digital clock"]:
        assert not toolpick.CODING.search(q), q                            # everyday requests don't pull them in


def test_job_status_questions_keep_the_main_brain():
    for q in ["how's the coding job going?", "is the coding job done", "status of the coding job",
              "is the research done?", "is my job finished"]:
        assert app.JOB_STATUS_RE.search(q), q
    for q in ["fix the bug in my python code", "write a function to sort a list", "I lost my job yesterday"]:
        assert not app.JOB_STATUS_RE.search(q), q


def test_pick_worker():
    assert workers.pick_worker("auto", "small") == "opencode"
    assert workers.pick_worker("auto", "big") == "claude"
    assert workers.pick_worker("Claude Code", "small") == "claude"
    assert workers.pick_worker("deepseek", "big") == "opencode"


def test_parse_outputs():
    oc = workers.parse_opencode([json.dumps({"type": "step_finish", "part": {"cost": 0.01}}),
                                 json.dumps({"type": "text", "part": {"text": "Done."}}), "not json"])
    assert oc == {"text": "Done.", "cost": 0.01, "error": "", "limit": False}
    cc = workers.parse_claude(["junk", json.dumps({"result": "All good", "total_cost_usd": 0.42, "is_error": False,
                                                    "permission_denials": [{"tool_name": "Bash"}]})])
    assert cc == {"text": "All good", "cost": 0.42, "error": "", "denied": ["Bash"]}
    assert workers.parse_claude([])["error"]


def test_commands_have_the_safety_flags(monkeypatch):
    monkeypatch.setattr(workers, "find_exe", lambda w, conf=None: "exe")
    conf = {**workers.DEFAULTS}
    args, env = workers.command("opencode", "brief", "m1", conf)
    assert args[:3] == ["exe", "run", "--standalone"] and args[-1] == "brief" and "-m" in args
    perm = json.loads(env["OPENCODE_CONFIG_CONTENT"])["permission"]
    assert perm["*"] == perm["shell"] == perm["execute"] == perm["subagent"] == "deny" and perm["edit"] == "allow"
    args, _ = workers.command("claude", "brief", "", conf)
    assert "--max-budget-usd" in args and "Bash(git push:*)" in args and "Bash(git commit:*)" in args
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"


def test_approvals(monkeypatch):
    s = dict(app.settings())
    monkeypatch.setattr(app.config, "settings", lambda: {**s, "autonomy": "full"})
    monkeypatch.setitem(app.state, "remote", False)
    cheap = {"name": "start_coding_job", "args": {"brief": "x", "folder": "C:\\p"}}
    paid = {"name": "start_coding_job", "args": {"brief": "x", "folder": "C:\\p", "size": "big"}}
    assert not app.is_risky(cheap)                                          # cheap worker: no question
    assert app.is_risky(paid)                                               # Claude Code always asks...
    assert app.is_risky({"name": "commit_coding_job", "args": {}})          # ...and so does every commit
    assert "Claude Code (paid" in app.ask_text(paid)
    monkeypatch.setitem(app.state, "remote", True)
    assert app.is_risky(cheap)                                              # from the phone: ask


def test_what_changed_is_short_and_readable(repo, fake_worker):
    (repo / "notes.txt").write_text("the user's own edit\n")
    run_job(repo)
    text = workers.changes("latest")
    assert "changed 1 file" in text and "README.md" in text and "Not committed" in text
    assert "+fixed typo" in text and "notes.txt" not in text                # only the job's files
    assert tools.show_coding_changes() == text
    workers.commit("latest")
    assert "aren't there any more" in workers.changes("latest")


def test_retry_gives_the_same_brief_and_folder_to_another_worker(repo, fake_worker):
    fake_worker["v"] = "fail"
    first, _ = run_job(repo)
    assert first["status"] == "failed"
    fake_worker["v"] = "edit"
    msg = tools.retry_coding_job("latest", "claude")
    assert msg.startswith("Started background job") and "Claude Code" in msg
    second = coworker.jobs[-1]
    assert wait_for(lambda: second["status"] != "working")
    rec = workers.load_jobs()[-1]
    assert rec["worker"] == "claude" and rec["brief"] == "Fix the typo in README" and second["status"] == "done"


def test_phone_gets_reply_hints_for_coding_jobs(monkeypatch):
    sent = []
    monkeypatch.setattr(app.integrations, "user_away", lambda: True)
    monkeypatch.setattr(app.phone, "notify", lambda text: sent.append(text) or 1)
    monkeypatch.setattr(app.voice_io, "announce", lambda *a, **k: None)
    monkeypatch.setattr(app.voice_io, "wake_body", lambda: None)
    monkeypatch.setattr(app.body, "react_later", lambda *a, **k: None)
    monkeypatch.setattr(app.learner, "job_finished", lambda job: None)
    monkeypatch.setitem(app.settings(), "wake", False)
    app.job_done({"id": 91, "kind": "coding", "title": "t", "status": "failed", "error": "boom", "worker": "opencode"})
    assert "try Claude Code" in sent[-1] and "boom" in sent[-1]
    app.job_done({"id": 92, "kind": "coding", "title": "t", "status": "done", "result": "OpenCode finished.",
                  "files": ["README.md"], "worker": "opencode"})
    assert "what changed?" in sent[-1] and "commit it" in sent[-1]
    app.job_done({"id": 93, "kind": "research", "title": "t", "status": "done", "result": "Found it."})
    assert "send the report" in sent[-1]                   # research has its own reply (phase 4 step 0)
    app.job_done({"id": 94, "kind": "write_document", "title": "t", "status": "done", "result": "Written."})
    assert "on the laptop for when you're back" in sent[-1]


def test_phone_status_lists_running_jobs(monkeypatch):
    monkeypatch.setattr(coworker, "jobs", [{"id": 5, "kind": "coding", "title": "Fix README", "status": "working"}])
    assert "Working on: Fix README" in app.phone_status()


def test_retry_approvals(monkeypatch):
    s = dict(app.settings())
    monkeypatch.setattr(app.config, "settings", lambda: {**s, "autonomy": "full"})
    monkeypatch.setitem(app.state, "remote", False)
    assert app.is_risky({"name": "retry_coding_job", "args": {}})                      # default = Claude Code
    assert not app.is_risky({"name": "retry_coding_job", "args": {"worker": "opencode"}})
    assert not app.is_risky({"name": "show_coding_changes", "args": {}})


def test_workers_settings_api(tmp_path, monkeypatch):
    monkeypatch.setattr(store.current(), "root", tmp_path)
    monkeypatch.setattr(workers, "claude_signed_in", lambda conf=None, fresh=False: False)
    monkeypatch.setattr(workers, "find_exe", lambda w, conf=None: sys.executable)   # CI has no Claude Code installed
    c = app.app.test_client()
    j = c.get("/api/workers").get_json()
    assert j["config"]["opencode"]["model"] == workers.DEFAULTS["opencode"]["model"] and "auth login" in j["sign_in_help"]
    j = c.post("/api/workers", json={"opencode_model": "opencode/other", "opencode_fallbacks": "a/b, c/d",
                                     "claude_budget_usd": "999", "daily_budget_usd": 7, "claude_timeout_min": 0,
                                     "evil": "x"}).get_json()
    cfg = j["config"]
    assert cfg["opencode"]["model"] == "opencode/other" and cfg["opencode"]["fallback_models"] == ["a/b", "c/d"]
    assert cfg["claude"]["budget_usd"] == 50.0 and cfg["daily_budget_usd"] == 7.0 and cfg["claude"]["timeout_min"] == 1
    assert "evil" not in json.loads((tmp_path / "workers.json").read_text())
    r = c.post("/api/workers", json={"claude_model": "rm -rf /"})
    assert r.status_code == 400 and "model name" in r.get_json()["error"]
    r = c.post("/api/workers", json={"claude_exe": str(tmp_path / "nope.exe")})
    assert r.status_code == 400 and "no program" in r.get_json()["error"]
    r = c.post("/api/workers", json={"claude_budget_usd": "lots"})
    assert r.status_code == 400 and "must be a number" in r.get_json()["error"]
    assert json.loads((tmp_path / "workers.json").read_text())["claude"]["budget_usd"] == 50.0      # unchanged


def test_settings_page_has_the_workers_tab():
    static = workers.Path(__file__).resolve().parent.parent / "static"
    page = (static / "index.html").read_text(encoding="utf-8")
    js = (static / "nova.js").read_text(encoding="utf-8")
    assert 'data-tab="workers"' in page and 'data-pane="workers"' in page
    assert "loadWorkers();" in js and "function showWorkers" in js and "innerHTML" not in js.split("function showWorkers")[1][:3000]
