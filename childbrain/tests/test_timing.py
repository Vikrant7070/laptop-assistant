"""WP 0.7: a GPU timing method that repeats.

Done when: timing a fixed brain repeats within ±10% across three runs (on a quiet GPU: Nova shares it).
"""

import pytest

torch = pytest.importorskip("torch")  # skipped under Nova's Python

from childbrain.measure import timing as tm


def test_judge():
    assert tm.judge([1.00, 1.05, 0.97]) == (pytest.approx(0.05), True)  # middle 1.00; worst 1.05 is 5% off
    assert tm.judge([1.0, 1.0, 1.3])[1] is False


def test_conditions_are_read_not_changed():
    p = tm.power()
    assert set(p) == {"plugged_in", "battery_saver", "battery_percent", "power_mode"}
    c = tm.conditions("cpu")
    assert c["torch"] == torch.__version__ and "power_mode" in c
    assert tm.warnings_for({"plugged_in": False}, {}) == ["on battery: plug in for timings that compare"]
    assert tm.same_conditions({"conditions": c}, {"conditions": dict(c)})
    assert not tm.same_conditions({"conditions": c}, {"conditions": {**c, "plugged_in": not c["plugged_in"]}})


def test_cpu_timing_works():
    r = tm.timing(device="cpu", copies=256, warmup_s=0.2, samples=20, inner=5)
    assert 0 < r["p10_ms"] <= r["median_ms"] <= r["p90_ms"] and r["copies"] == 256
    assert r["tries"] >= 1 and "clean" in r


def test_busy_gpu_gives_no_number(monkeypatch):
    monkeypatch.setattr(tm, "quiet_gpu", lambda: {"quiet": False, "why": ["the GPU is 80% busy with other work"]})
    rep = tm.repeat_check(device="cuda")
    assert rep["runs"] == [] and rep["repeatable"] is None and rep["verdict"].startswith("GPU not quiet")


@pytest.mark.skipif(not torch.cuda.is_available(), reason="no GPU")
def test_three_gpu_timings_agree_within_10_percent():
    quiet = tm.quiet_gpu()
    if not quiet["quiet"]:
        pytest.skip("GPU not quiet (" + "; ".join(quiet["why"]) + "): timings only count with Nova's GPU features off")
    rep = tm.repeat_check()
    print(f"medians {[round(m, 3) for m in rep['medians_ms']]} ms, worst {rep['worst_off'] * 100:.1f}% off")
    assert rep["repeatable"], rep["verdict"]
