"""Nova goes first on the graphics card: a GPU run waits while Nova's signal file says she needs it.

No torch needed: these drive runcard's live-run state directly. The signal format is Nova's
(nova/brain/gpu_signal.py): {"until": <unix time>, "pid": ...}.
"""

import json
import threading
import time

import pytest

from childbrain import runcard as rc


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A pretend GPU run in tmp_path, with Nova's signal file in tmp_path too."""
    signal = tmp_path / "gpu_wanted"
    monkeypatch.setenv(rc.GPU_SIGNAL_ENV, str(signal))
    monkeypatch.setattr(rc, "GPU_POLL", 0.02)
    folder = tmp_path / "run"
    folder.mkdir()
    monkeypatch.setattr(rc, "_live", folder)
    monkeypatch.setattr(rc, "_device", "cuda")
    monkeypatch.setattr(rc, "_paused", 0.0)
    monkeypatch.setattr(rc, "_last_progress", {})
    return folder, signal


def want(signal, seconds):
    signal.write_text(json.dumps({"until": time.time() + seconds, "pid": 1}), encoding="utf-8")


def test_fresh_and_stale_signal(live):
    _, signal = live
    assert not rc.nova_wants_gpu()                       # no file: Nova isn't asking
    want(signal, 30)
    assert rc.nova_wants_gpu()
    want(signal, -1)
    assert not rc.nova_wants_gpu()                       # an old one has run out (e.g. Nova crashed)
    signal.write_text("{half", encoding="utf-8")
    assert not rc.nova_wants_gpu()                       # a half-written one is ignored


def test_gpu_run_waits_while_nova_needs_the_card_then_carries_on(live):
    folder, signal = live
    rc.report_progress(phase="practising", steps=100)
    want(signal, 0.3)
    seen = {}

    def peek():                                          # while it waits, the Child brain tab says why
        time.sleep(0.1)
        seen.update(json.loads((folder / rc.PROGRESS_FILE).read_text(encoding="utf-8")))
    t = threading.Thread(target=peek)
    t.start()
    t0 = time.perf_counter()
    assert rc.stop_requested() is None                   # waited, then no stop: carry on training
    t.join()
    assert time.perf_counter() - t0 >= 0.25
    assert seen["status"] == "paused for Nova" and seen["steps"] == 100   # the numbers stay on the tab
    assert rc.paused_seconds() >= 0.25
    after = json.loads((folder / rc.PROGRESS_FILE).read_text(encoding="utf-8"))
    assert "status" not in after and after["paused_for_nova_s"] >= 0.2


def test_a_stop_request_ends_the_wait(live):
    folder, signal = live
    want(signal, 60)
    threading.Timer(0.1, lambda: (folder / rc.FINISH_FILE).write_text("now", encoding="utf-8")).start()
    t0 = time.perf_counter()
    assert rc.stop_requested() == "a person asked it to finish"
    assert time.perf_counter() - t0 < 5


def test_cpu_runs_and_no_run_never_wait(live, monkeypatch):
    _, signal = live
    want(signal, 60)
    monkeypatch.setattr(rc, "_device", "cpu")
    assert rc.gpu_wait() == 0.0                          # Nova's card isn't in use by a CPU run
    monkeypatch.setattr(rc, "_device", "cuda")
    monkeypatch.setattr(rc, "_live", None)
    assert rc.gpu_wait() == 0.0 and rc.stop_requested() is None     # outside a run (tests, reruns)
