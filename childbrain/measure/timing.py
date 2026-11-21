"""How fast is a brain on this laptop's GPU? A timing method that gives the same answer twice.

  1. Conditions first: plugged in, which Windows power mode, battery saver, GPU clock / temperature / how busy
     the GPU already is (Nova shares it). These are only READ, never changed, and saved with every timing,
     so two timings are compared only when their conditions match.
  2. Warm-up (3 s): run the brain until the GPU has woken up (clocks ramp up from idle) and the kernels are
     cached. 1.5 s wasn't enough: the first timing in a fresh program then often came out up to 28% slow.
  3. Measure with CUDA events (the GPU's own clock): one sample = `inner` decisions in a row, so tiny
     kernels aren't drowned by timer noise. Take many samples and report the MEDIAN (one hiccup, e.g. Nova
     using the GPU for a moment, doesn't move it), plus the spread (10th-90th percentile).
  4. Repeatable means: three separate timings (each with its own warm-up) land within ±10% of their middle one.
  5. Only on a QUIET GPU. Nova shares this 6 GB GPU (its language model and Whisper use ~5.5 GB). Measured
     2026-11-20: when Nova got busy mid-test, timings went 5-7x slower (0.35 ms -> 2.5 ms) and the GPU was
     nearly full, which can also slow Nova down badly. So before timing, the GPU is watched for a moment; if it
     is busy or short of free memory, the answer is "GPU not quiet" instead of a wrong number. A timing whose
     samples are spread too wide (something interrupted it) is thrown away and done again.

    .venv-childbrain\\Scripts\\python -m childbrain.measure.timing          time the fixed reference brain
"""

from __future__ import annotations

import ctypes
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import torch

from .. import RUNS_DIR

OVERLAYS = {  # Windows 11 "power mode" (Settings > System > Power)
    "961cc777-2547-4f9d-8174-7d86181b8a7a": "best power efficiency",
    "00000000-0000-0000-0000-000000000000": "balanced",
    "3af9b8d9-7c97-431d-ad78-34a8bfea439f": "better performance",
    "ded574b5-45a0-4f42-8737-46345c09c238": "best performance",
}
REFERENCE = {"kind": "mlp", "hidden": 256}  # the fixed brain timed in WP 0.7 and in later speed scores


# ---------- conditions (read only) ----------

class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


class _Guid(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    def __str__(self):
        d = bytes(self.d).hex()
        return f"{self.a:08x}-{self.b:04x}-{self.c:04x}-{d[:4]}-{d[4:]}"


def power() -> dict:
    out = {"plugged_in": None, "battery_saver": None, "battery_percent": None, "power_mode": "unknown"}
    if sys.platform != "win32":
        return out
    status = _PowerStatus()
    if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        out["plugged_in"] = {0: False, 1: True}.get(status.ACLineStatus)
        out["battery_saver"] = bool(status.SystemStatusFlag & 1)
        out["battery_percent"] = None if status.BatteryLifePercent == 255 else status.BatteryLifePercent
    try:
        guid = _Guid()
        if ctypes.windll.powrprof.PowerGetEffectiveOverlayScheme(ctypes.byref(guid)) == 0:
            out["power_mode"] = OVERLAYS.get(str(guid), str(guid))
    except (AttributeError, OSError):
        pass
    return out


def gpu_state() -> dict:
    fields = "pstate,clocks.sm,clocks.mem,temperature.gpu,power.draw,utilization.gpu,memory.used,memory.total"
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, check=True,
                             creationflags=0x08000000 if sys.platform == "win32" else 0).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    vals = [v.strip() for v in out.splitlines()[0].split(",")]
    keys = ["pstate", "sm_mhz", "mem_mhz", "temp_c", "watts", "busy_percent", "mem_used_mb", "mem_total_mb"]
    state = dict(zip(keys, vals))
    for k in keys[1:]:
        try:
            state[k] = float(state[k])
        except (KeyError, ValueError):
            state[k] = None
    return state


QUIET_BUSY_PERCENT = 10      # average GPU use by others while we watch
QUIET_FREE_MB = 1500         # free GPU memory needed
CLEAN_SPREAD = 1.5           # a clean timing has p90 <= 1.5 x p10
TRIES = 3


def quiet_gpu(seconds: float = 2.0, looks: int = 5) -> dict:
    """Watch the GPU for a moment: is anything else using it?"""
    states = []
    for _ in range(looks):
        states.append(gpu_state())
        time.sleep(seconds / looks)
    busy = [s["busy_percent"] for s in states if s.get("busy_percent") is not None]
    free = [s["mem_total_mb"] - s["mem_used_mb"] for s in states if s.get("mem_used_mb") is not None]
    if not busy or not free:
        return {"quiet": False, "why": ["can't read the GPU's state (nvidia-smi)"]}
    out = {"busy_avg": sum(busy) / len(busy), "free_mb": min(free), "why": []}
    if out["busy_avg"] > QUIET_BUSY_PERCENT:
        out["why"].append(f"the GPU is {out['busy_avg']:.0f}% busy with other work")
    if out["free_mb"] < QUIET_FREE_MB:
        out["why"].append(f"only {out['free_mb']:.0f} MB of GPU memory free (Nova's models?)")
    out["quiet"] = not out["why"]
    return out


def conditions(device: str) -> dict:
    c = {"device": device, "torch": torch.__version__, **power()}
    if device.startswith("cuda") and torch.cuda.is_available():
        c["gpu"] = torch.cuda.get_device_name(torch.device(device))
    return c


def warnings_for(cond: dict, before: dict) -> list[str]:
    w = []
    if cond.get("plugged_in") is False:
        w.append("on battery: plug in for timings that compare")
    if cond.get("battery_saver"):
        w.append("battery saver is on")
    if (before.get("busy_percent") or 0) > 20:
        w.append(f"the GPU was already {before['busy_percent']:.0f}% busy (Nova?)")
    return w


def same_conditions(a: dict, b: dict) -> bool:
    keys = ("device", "gpu", "torch", "plugged_in", "power_mode", "battery_saver")
    return all(a.get("conditions", {}).get(k) == b.get("conditions", {}).get(k) for k in keys)


# ---------- the measurement ----------

def reference_brain(device: str, seed: int = 0):
    from ..brain.net import build

    torch.manual_seed(seed)
    return build(REFERENCE).to(device).eval()


def sample_inputs(copies: int, device: str):
    from ..worlds import make

    world = make("snake", n=copies, seed=0, device=device)
    return world.observe(), world.allowed()


def time_brain(net, obs, allowed, warmup_s: float = 3.0, samples: int = 60, inner: int = 20) -> dict:
    """Median milliseconds per batch decision (all copies at once).

    Times the decision itself (`choose`), which never makes the CPU wait for the GPU; the exam's
    "are the scores real numbers?" check does wait, so it isn't part of the brain's speed."""
    from ..brain.net import choose as act

    device = obs.device
    cuda = device.type == "cuda"
    end = time.perf_counter() + warmup_s
    while time.perf_counter() < end:  # warm-up: wake the GPU clocks, cache the kernels
        act(net, obs, allowed)
        if cuda:
            torch.cuda.synchronize()
    times = []
    with torch.no_grad():
        for _ in range(samples):
            if cuda:
                start_ev, end_ev = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
                start_ev.record()
                for _ in range(inner):
                    act(net, obs, allowed)
                end_ev.record()
                torch.cuda.synchronize()
                times.append(start_ev.elapsed_time(end_ev) / inner)
            else:
                t = time.perf_counter()
                for _ in range(inner):
                    act(net, obs, allowed)
                times.append((time.perf_counter() - t) * 1000 / inner)
    times.sort()
    q = statistics.quantiles(times, n=10)
    return {"median_ms": statistics.median(times), "p10_ms": q[0], "p90_ms": q[-1], "samples": samples, "inner": inner}


def timing(device: str | None = None, copies: int = 4096, **kw) -> dict:
    """One full timing of the reference brain, with its conditions. A disturbed timing is done again."""
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    cuda = device.startswith("cuda")
    if cuda:
        time.sleep(1.0)  # the GPU's "busy" number is an average over the last moment: let our own last run fade
    before = gpu_state() if cuda else {}  # before we touch the GPU ourselves
    net = reference_brain(device)
    obs, allowed = sample_inputs(copies, device)
    tries = []
    for _ in range(TRIES):
        tries.append(time_brain(net, obs, allowed, **kw))
        if tries[-1]["p90_ms"] <= CLEAN_SPREAD * tries[-1]["p10_ms"]:
            break
    result = min(tries, key=lambda r: r["p90_ms"] / r["p10_ms"])
    result["clean"] = result["p90_ms"] <= CLEAN_SPREAD * result["p10_ms"]
    result["tries"] = len(tries)
    after = gpu_state() if cuda else {}
    cond = conditions(device)
    result.update(copies=copies, us_per_decision=result["median_ms"] * 1000 / copies,
                  conditions=cond, gpu_before=before, gpu_after=after, warnings=warnings_for(cond, before))
    if not result["clean"]:
        result["warnings"].append("every try was disturbed (samples spread too wide)")
    return result


def judge(medians: list[float], tolerance: float = 0.10) -> tuple[float, bool]:
    """(how far the worst run is from the middle one, as a fraction; within tolerance?)"""
    middle = statistics.median(medians)
    worst = max(abs(m - middle) / middle for m in medians)
    return worst, worst <= tolerance


def repeat_check(runs: int = 3, tolerance: float = 0.10, device: str | None = None, **kw) -> dict:
    """Time `runs` times; repeatable if every run is within ±tolerance of their median.
    On a GPU that isn't quiet, nothing is timed: the answer would be about Nova, not the brain."""
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    quiet = quiet_gpu() if device.startswith("cuda") else {"quiet": True, "why": []}
    if not quiet["quiet"]:
        return {"runs": [], "quiet": quiet, "repeatable": None, "tolerance": tolerance,
                "verdict": "GPU not quiet: " + "; ".join(quiet["why"])}
    results = [timing(device=device, **kw) for _ in range(runs)]
    medians = [r["median_ms"] for r in results]
    worst, ok = judge(medians, tolerance)
    clean = all(r["clean"] for r in results)
    verdict = "REPEATABLE" if ok else "NOT repeatable"
    if not clean:
        verdict += " (but some timings were disturbed)"
    return {"runs": results, "quiet": quiet, "medians_ms": medians, "worst_off": worst, "repeatable": ok,
            "clean": clean, "tolerance": tolerance, "verdict": verdict}


def save(report: dict, folder: Path = RUNS_DIR / "timing") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    rep = repeat_check()
    if not rep["runs"]:
        print(rep["verdict"])
        print("Close Nova (or turn off its GPU features), plug in, then try again.")
        sys.exit(2)
    first = rep["runs"][0]
    c = first["conditions"]
    print(f"brain {REFERENCE} on {c.get('gpu', c['device'])}, {first['copies']} copies per decision")
    print(f"power: plugged in {c['plugged_in']}, mode '{c['power_mode']}', battery saver {c['battery_saver']}")
    for i, r in enumerate(rep["runs"], 1):
        print(f"  run {i}: median {r['median_ms']:.3f} ms per batch ({r['us_per_decision']:.3f} µs per copy), "
              f"p10-p90 {r['p10_ms']:.3f}-{r['p90_ms']:.3f} ms, GPU {r['gpu_after'].get('sm_mhz')} MHz "
              f"{r['gpu_after'].get('temp_c')} °C")
    print(f"worst run off the middle by {rep['worst_off'] * 100:.1f}% -> {rep['verdict']} "
          f"(limit ±{rep['tolerance'] * 100:.0f}%)")
    for w in sorted({w for r in rep["runs"] for w in r["warnings"]}):
        print(f"  warning: {w}")
    print(f"saved {save(rep)}")
