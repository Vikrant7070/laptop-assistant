"""R11: background threads are named, logged when they crash, can restart, and are listed."""
import ast
import threading
import time
from pathlib import Path

import pytest

from nova import runtime, store

ROOT = Path(__file__).resolve().parent.parent

# Files that still make their own threads. Shrink this list; never grow it.
BARE_THREADS_ALLOWED = {
    "nova/runtime.py",                      # the one place that should
    "nova/brain/gpu_signal.py",             # being changed by the child-brain split-out branch (2026-11-22)
    "nova/core/app/childbrain_tab.py",      # same
    "nova/core/app/childbrain_watch.py",    # same
}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(runtime, "stopping", threading.Event())
    monkeypatch.setattr(runtime, "RESTART_PAUSE", (0.01, 0.02))
    yield
    runtime.stopping.set()


def _wait(cond, secs=3):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_spawn_names_the_thread_and_passes_arguments():
    got = []
    th = runtime.spawn("test.args", lambda a, b=0: got.append((a, b, threading.current_thread().name)), args=(1,), kwargs={"b": 2})
    th.join(2)
    assert got == [(1, 2, "test.args")] and th.daemon


def test_a_crash_is_logged_not_lost():
    def boom():
        raise ValueError("kaput")
    runtime.spawn("test.boom", boom).join(2)
    log = store.current().task_log.read_text(encoding="utf-8")
    assert "thread test.boom" in log and "kaput" in log


def test_restart_runs_it_again_after_a_crash():
    runs = []

    def flaky():
        runs.append(1)
        if len(runs) < 3:
            raise RuntimeError("again")
    th = runtime.spawn("test.flaky", flaky, restart=True)
    th.join(3)
    assert len(runs) == 3 and not th.is_alive()


def test_every_repeats_until_stop_all():
    runs = []
    runtime.spawn("test.every", lambda: runs.append(1), every=0.01)
    assert _wait(lambda: len(runs) >= 3)
    assert "test.every" not in runtime.stop_all(wait=2)
    n = len(runs)
    time.sleep(0.05)
    assert len(runs) == n


def test_delay_is_a_timer_and_start_false_waits():
    got = []
    th = runtime.spawn("test.later", got.append, args=("x",), delay=0.05, start=False, daemon=False)
    time.sleep(0.08)
    assert got == [] and not th.daemon
    t0 = time.time()
    th.start()
    th.join(2)
    assert got == ["x"] and time.time() - t0 >= 0.05


def test_threads_lists_what_is_running():
    gate = threading.Event()
    runtime.spawn("test.listed", gate.wait, args=(2,))
    assert _wait(lambda: any(r["name"] == "test.listed" for r in runtime.threads()))
    row = next(r for r in runtime.threads() if r["name"] == "test.listed")
    assert row["alive"] and row["runs"] == 1 and row["last_error"] == ""
    gate.set()
    assert _wait(lambda: all(r["name"] != "test.listed" for r in runtime.threads()))


def test_stop_all_reports_what_did_not_stop():
    gate = threading.Event()
    runtime.spawn("test.stubborn", gate.wait, args=(5,))
    assert _wait(lambda: runtime.threads())
    assert "test.stubborn" in runtime.stop_all(wait=0.05)
    gate.set()


def test_threads_api(monkeypatch):
    from nova.core import app
    monkeypatch.setattr(runtime, "threads", lambda: [{"name": "x"}])
    assert app.api.app.test_client().get("/api/threads").get_json() == {"threads": [{"name": "x"}]}


def test_no_bare_threads_outside_runtime():
    bare = set()
    for path in (ROOT / "nova").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in ("Thread", "Timer") \
                    and isinstance(node.value, ast.Name) and node.value.id == "threading":
                bare.add(path.relative_to(ROOT).as_posix())
    assert bare - BARE_THREADS_ALLOWED == set(), "start threads with runtime.spawn(name, fn, ...)"
    assert BARE_THREADS_ALLOWED - bare == set(), "a file on BARE_THREADS_ALLOWED no longer needs it: remove it"
