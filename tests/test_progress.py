"""Manager plan 4d: code watches every task step for progress. A loop (the same step with nothing new) or a stall gets
one new plan; still stuck -> she stops and asks ("Stuck at …: try a different way, or will you do this step?").
A worker job that goes quiet (no output, no file change) is "maybe stuck" on /tasks, with Stop."""
import json
import time

import pytest

from nova import progress, store, task_state
from nova.actions import coworker, jobrounds, workers
from nova.core import app
from nova.core.app import session, tasks_page, watching
from tests.test_workers import fake_worker, repo  # noqa: F401  (fixtures)

CLICK = ("click_at", {"x": 10, "y": 20})


@pytest.fixture
def trusted():
    """Clicks run without an Allow / No at this autonomy level."""
    s = app.settings()
    s["autonomy"] = "trusted"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")


def _step(holder, name, args=None, out="ok", ok=True):
    return progress.after_step(holder, [{"name": name, "args": args or {}}], [out], [ok])


# ------------------------------------------------------------------ the rules (pure code)

def test_the_same_click_three_times_is_a_loop():
    h = {}
    assert progress.verdict(_step(h, *CLICK)) == ""
    assert progress.verdict(_step(h, *CLICK)) == ""
    res = _step(h, *CLICK)
    assert progress.verdict(res) == "loop" and res["repeats"] == 3
    assert progress.words(res, "loop") == "did the same step (click_at x=10 y=20) 3 times with nothing new"


def test_a_click_with_a_new_result_each_time_is_progress():
    h = {}
    for i in range(5):
        assert progress.verdict(_step(h, *CLICK, out=f"opened page {i}")) == ""


def test_different_steps_that_say_ok_are_progress_and_scrolling_is_left_out():
    h = {}
    for name in ("open_app", "type_text", "save_file", "close_window"):
        assert progress.verdict(_step(h, name)) == ""
    for _ in range(6):
        assert _step(h, "scroll", {"direction": "down"}) is None       # repeating is the point


def test_three_steps_in_a_row_with_nothing_new_is_no_progress():
    h = {}
    _step(h, "get_datetime"), _step(h, "get_system_status")
    assert progress.verdict(_step(h, "get_datetime")) == ""                     # 1 old result
    assert progress.verdict(_step(h, "get_system_status")) == ""                # 2
    res = _step(h, "get_datetime")
    assert progress.verdict(res) == "no progress" and res["stalled"] == 3
    # a failed step is never progress, even with a new result
    h2 = {}
    _step(h2, "a"), _step(h2, "b", out="no such window", ok=False), _step(h2, "c", out="error 2", ok=False)
    assert progress.verdict(_step(h2, "d", out="error 3", ok=False)) == "no progress"


def test_first_stuck_gets_a_plan_then_the_same_step_again_asks():
    h = {}
    for _ in range(3):
        res = _step(h, *CLICK)
    assert progress.verdict(res) == "loop" and progress.escalate(h) == "replan"
    assert progress.verdict(_step(h, *CLICK)) == "loop"                         # the very same step once more
    assert progress.escalate(h) == "ask"
    progress.reset(h)
    assert h["progress"]["nudges"] == 0


# ------------------------------------------------------------------ in the agent loop

def test_a_task_that_repeats_the_same_click_stops_and_asks(brain, trusted):
    """The plan's Done when: not run to the step limit (24) - one new plan, then the question card."""
    fake, ran = brain([("", [CLICK])] * 30)
    out, _ = app.answer("click the submit button on the form")
    assert len(ran) == 4                                 # 3 clicks -> told it's a loop; the 4th same click -> asks
    notes = [m["content"] for m in app.state["messages"] if m["role"] == "user" and "You are stuck" in m["content"]]
    assert len(notes) == 1 and "click_at" in notes[0]
    q = out["question"]
    assert q["questions"][0]["key"] == "stuck" and "Stuck at" in out["reply"]
    assert [c["value"] for c in q["questions"][0]["choices"]] == ["try", "me", "stop"]
    card = task_state.of(app.state["task"])
    assert card["status"] == "waiting" and "Stuck at" in card["why"]


def test_the_new_plan_goes_on_the_card(brain, monkeypatch, trusted):
    goals = []
    monkeypatch.setattr(watching, "make_plan", lambda goal: (goals.append(goal), (
        "1. Press Enter to submit (press_keys)\n2. Look at the page (read_page)", "deep"))[1])
    fake, ran = brain([("", [CLICK])] * 3 + [("Submitted it.", [])])
    out, _ = app.answer("click the submit button on the form")
    assert out["reply"] == "Submitted it." and len(ran) == 3
    assert "Already tried, step by step" in goals[0] and "Stuck: it did the same step (click_at" in goals[0]
    note = next(m["content"] for m in app.state["messages"] if m["role"] == "user" and "You are stuck" in m["content"])
    assert "Here is a new plan" in note and "Press Enter" in note
    card = task_state.of(app.state["task"])
    assert card["source"] == "deep brain" and card["plan"][0]["text"].startswith("Press Enter")


def test_the_answers(brain, trusted):
    fake, ran = brain([("", [CLICK])] * 30)
    out, _ = app.answer("click the submit button on the form")
    card = task_state.of(app.state["task"])
    qcard = session.state["question"]
    said = watching._answered(qcard, {"stuck": {"value": "me"}})
    assert "that step is yours" in said["reply"] and card["status"] == "stopped" and task_state.resumable(card)
    said = watching._answered(qcard, {"stuck": {"value": "stop"}})
    assert card["status"] == "dropped" and "stopped that task" in said["reply"]
    task_state.mark(card, "waiting", "x")
    assert watching._answered(qcard, {"stuck": {"value": "try"}}) is None        # queued to carry on
    nxt = session.state["resume_next"]
    assert nxt["card"] is card and any("DIFFERENT way" in e for e in nxt["extra"])
    assert card["progress"]["nudges"] == 0                                       # fresh chances


# ------------------------------------------------------------------ worker jobs that go quiet

def test_a_quiet_worker_is_maybe_stuck_until_it_moves(tmp_path, monkeypatch):
    notes = []
    monkeypatch.setattr(coworker, "_note", notes.append)
    monkeypatch.setattr(workers, "_changed", lambda folder: {"a.py"})
    (tmp_path / "a.py").write_text("1", encoding="utf-8")
    q, job, now = jobrounds.Quiet(str(tmp_path)), {"id": 7}, time.time()
    q.last = now - 11 * 60
    q.check(job, now)
    assert job["maybe_stuck"] == "no output and no file change for 11 min" and "Job #7 may be stuck" in notes[0]
    q.check(job, now + 1)                                    # looks only every QUIET_EVERY seconds
    assert len(notes) == 1
    q.heard()                                                # output again
    q.check(job, time.time() + 60)
    assert "maybe_stuck" not in job
    # quiet output, but the files keep changing: working
    q.last = now - 11 * 60
    (tmp_path / "a.py").write_text("22", encoding="utf-8")
    import os
    os.utime(tmp_path / "a.py", (now + 5, now + 5))
    q.check(job, time.time() + 120)
    assert "maybe_stuck" not in job and len(notes) == 1


def test_tasks_page_shows_maybe_stuck_with_stop(monkeypatch):
    workers._save_record({"key": "k9", "job": 9, "worker": "opencode", "status": "running", "started": time.time(),
                          "finished": None, "brief": "fix it", "files": []})
    monkeypatch.setattr(coworker, "jobs", [{"id": 9, "status": "working", "maybe_stuck": "no output for 12 min"}])
    rec = workers.load_jobs()[-1]
    assert tasks_page.job_row(rec)["maybe_stuck"] == "no output for 12 min"
    acts = tasks_page.detail("j:k9")["actions"]
    assert {"do": "chat", "label": "■ Stop…", "say": "stop job #9"} in acts
    rec["status"] = "done"
    assert tasks_page.job_row(rec)["maybe_stuck"] == ""


def test_a_real_quiet_run_is_flagged_and_can_be_stopped(repo, fake_worker, monkeypatch):  # noqa: F811
    from tests.test_workers import wait_for
    notes = []
    monkeypatch.setattr(coworker, "_note", notes.append)
    monkeypatch.setattr(jobrounds, "QUIET_MIN", 0.01)             # 0.6 s of quiet
    monkeypatch.setattr(jobrounds, "QUIET_EVERY", 0.1)
    fake_worker["v"] = "slow"                                       # one line of output, then 30 s of nothing
    workers.start("Fix the typo", str(repo))
    job = coworker.jobs[-1]
    assert wait_for(lambda: job.get("maybe_stuck"), 15), job
    assert any(f"Job #{job['id']} may be stuck" in n for n in notes)
    coworker.stop(str(job["id"]))
    assert wait_for(lambda: job["status"] == "stopped", 15)
