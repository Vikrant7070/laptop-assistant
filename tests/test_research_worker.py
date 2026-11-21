"""Manager plan phase 4, step 0: research is a worker - recorded in the job history, set in ⚙ Workers, retried and
reported like a coding job, and pick_worker can choose it. What the user sees for "research X" stays the same."""
import time

from nova.core import app
from nova.actions import coworker
from nova.actions import tools
from nova.actions import workers


def _wait(fn, secs=10):
    end = time.time() + secs
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


def _fake_web(monkeypatch, tmp_path, pages=5):
    """Search and reading are faked; the job, its record and the report file are real."""
    opened = []
    monkeypatch.setattr(coworker.tools, "HOME", tmp_path)
    monkeypatch.setattr(coworker, "_browser", lambda: ("chrome.exe", "chrome"))
    monkeypatch.setattr(coworker, "search", lambda q, p, e, w: [(f"Page number {i} about it", f"https://s{i}.com/x")
                                                                  for i in range(pages)])
    monkeypatch.setattr(coworker, "_open_parked", lambda url, *a, **k: opened.append(url) or url)
    monkeypatch.setattr(coworker, "close_window", lambda h: None)
    monkeypatch.setattr(coworker, "_title", lambda h: "A page - Google Chrome")
    monkeypatch.setattr(coworker.tools, "page_text", lambda h, n=0: "useful facts about tea. " * 20)
    return opened


POINTS = "\n\n1. Green tea has antioxidants [1]\n2. Too much caffeine is bad [1]"


def _hooks(monkeypatch, done, cloud=None, local=None):
    monkeypatch.setitem(coworker._hooks, "write", cloud or (lambda p, n: "Headline: Green tea is good." + POINTS))
    monkeypatch.setitem(coworker._hooks, "write_local", local)
    monkeypatch.setitem(coworker._hooks, "on_done", done.append)
    monkeypatch.setitem(coworker._hooks, "on_note", None)
    monkeypatch.setitem(coworker._hooks, "sites", None)


def test_research_is_recorded_in_the_job_history(monkeypatch, tmp_path):
    _fake_web(monkeypatch, tmp_path)
    done = []
    _hooks(monkeypatch, done)
    msg = tools.research_in_background("green tea", focus="is it healthy")
    assert msg.startswith("Started background job") and "researching 'green tea'" in msg     # same as before
    assert _wait(lambda: done) and done[0]["status"] == "done", done and done[0]["error"]
    rec = workers.last_record("latest", need_files=False, kinds=("research",))
    assert rec["worker"] == "research" and rec["status"] == "done" and rec["topic"] == "green tea"
    assert rec["focus"] == "is it healthy" and rec["path"].endswith("Research - green tea.txt")
    assert rec["sources"] and rec["finished"] and "Green tea is good." in rec["result"]
    recent = workers.overview()["recent"]
    assert recent[0]["worker"] == "Research" and recent[0]["sources"] == len(rec["sources"])
    # coding-only lookups never pick a research record ("commit it" right after research)
    assert workers.last_record("latest", need_files=False) is None
    assert "no finished coding job" in workers.commit()


def test_a_failed_research_is_recorded_as_failed(monkeypatch, tmp_path):
    _fake_web(monkeypatch, tmp_path)
    monkeypatch.setattr(coworker.tools, "page_text", lambda h, n=0: "")        # nothing readable
    done = []
    _hooks(monkeypatch, done)
    tools.research_in_background("empty pages")
    assert _wait(lambda: done) and done[0]["status"] == "failed"
    rec = workers.last_record("latest", need_files=False, kinds=None)
    assert rec["status"] == "failed" and "couldn't read" in rec["note"]


def test_settings_pick_how_many_pages_and_which_brain(monkeypatch, tmp_path):
    opened = _fake_web(monkeypatch, tmp_path)
    done, used = [], []
    _hooks(monkeypatch, done, cloud=lambda p, n: used.append("cloud") or "Headline: A." + POINTS,
           local=lambda p, n: used.append("local") or "Headline: A." + POINTS)
    out = workers.save_settings({"research_max_sources": 2, "research_cloud_report": False})
    assert out["config"]["research"] == {"max_sources": 2, "cloud_report": False}
    tools.research_in_background("tea")
    assert _wait(lambda: done) and done[0]["status"] == "done", done[0]["error"]
    assert len(opened) == 2 and used == ["local"]
    assert workers.last_record("latest", False, ("research",))["model"] == "local brain"
    # the user's own number wins (still at most 5); numbers are clamped
    done.clear()
    opened.clear()
    workers.save_settings({"research_max_sources": 99, "research_cloud_report": True})
    assert workers.config()["research"]["max_sources"] == 5
    tools.research_in_background("coffee", max_sources=1)
    assert _wait(lambda: done) and len(opened) == 1 and used[-1] == "cloud"
    # other settings are kept when research ones are saved
    assert workers.config()["opencode"]["model"] == workers.DEFAULTS["opencode"]["model"]


def test_retry_runs_research_again_and_coding_still_goes_to_claude(monkeypatch, tmp_path):
    _fake_web(monkeypatch, tmp_path)
    done = []
    _hooks(monkeypatch, done)
    tools.research_in_background("tea")
    assert _wait(lambda: done)
    rec, w = workers.retry_target("latest", "")
    assert w == "research" and rec["topic"] == "tea"
    assert not app.is_risky({"name": "retry_coding_job", "args": {}})               # research again: free, no ask
    done.clear()
    assert tools.retry_coding_job().startswith("Started background job")
    assert _wait(lambda: done) and done[0]["kind"] == "research"
    assert len([r for r in workers.load_jobs() if r["worker"] == "research"]) == 2
    # a coding record newer than the research: no worker named -> Claude Code (asks first)
    workers._save_record({"key": "k1", "job": 77, "worker": "opencode", "brief": "fix", "folder": str(tmp_path),
                          "status": "failed", "started": time.time(), "files": []})
    assert workers.retry_target("latest", "")[1] == "claude"
    assert app.is_risky({"name": "retry_coding_job", "args": {}})
    # "try the research again" when a coding job is newer still finds the research
    assert workers.retry_target("latest", "research")[0]["topic"] == "tea"


def test_pick_worker_can_choose_research():
    assert workers.pick_worker("research") == "research"
    assert workers.pick_worker("auto", kind="research") == "research"
    assert workers.pick_worker("auto") == "opencode" and workers.pick_worker("auto", "big") == "claude"


def test_report_and_finding_past_research_by_day_or_words(monkeypatch, tmp_path):
    report = tmp_path / "r.txt"
    report.write_text("Research: tea\n\nHeadline: green tea is fine.\n\nSources:\n[1] x", encoding="utf-8")
    now = time.time()
    tue = now - ((time.localtime(now).tm_wday - 1) % 7 or 7) * 86400      # the last Tuesday before today
    workers._save_record({"key": "a", "job": 1, "worker": "research", "brief": "green tea", "topic": "green tea",
                          "status": "done", "started": tue, "path": str(report), "sources": ["x"]})
    workers._save_record({"key": "b", "job": 2, "worker": "research", "brief": "coffee", "topic": "coffee",
                          "status": "done", "started": now, "path": "", "result": "Coffee is fine.", "sources": ["y"]})
    assert "green tea is fine" in tools.research_report("the research from Tuesday")
    assert "green tea is fine" in tools.research_report("tea")
    assert "Coffee is fine." in tools.research_report("latest")              # file gone: the record still helps
    other = next(d for i, d in enumerate(workers.DAYS) if i not in (1, time.localtime(now).tm_wday))
    assert workers.last_record(f"the research from {other}", False, ("research",)) is None   # no job that day
    # after a restart (no jobs in memory), "show it" opens the saved report
    monkeypatch.setattr(coworker, "jobs", [])
    opened = []
    monkeypatch.setattr(coworker.os, "startfile", opened.append, raising=False)
    assert tools.show_finished_work("tea").startswith("Opened") and opened == [str(report)]


def test_phone_hints_for_research(monkeypatch):
    assert "send the report" in app._phone_hint({"kind": "research"})
    assert "try again" in app._phone_hint({"kind": "research"}, failed=True)
    assert app._phone_hint({"kind": "draft_email"}) == ""


def test_research_tools_are_picked_by_their_words():
    from nova.brain import toolpick
    assert "research_report" in toolpick.pick("send me the report of the research")
    assert "research_in_background" in toolpick.pick("research the best python web framework")
