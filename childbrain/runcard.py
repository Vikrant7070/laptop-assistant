"""Run cards: everything needed to repeat a run and get the same numbers.

Every run gets its own folder in childbrain/runs/ with two files:
  card.json     what was run: experiment, settings, seed, device, code fingerprint, library versions
  results.json  the numbers it produced

`rerun(folder)` runs the card again and checks the new numbers against results.json.

An experiment is a function in the childbrain package, marked with @experiment(defaults={...}).
It gets the full settings and the device, and returns JSON numbers. Seeding happens before it starts.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import platform
import random
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import PACKAGE, ROOT, RUNS_DIR

# Must be set before cuBLAS starts, or GPU matrix maths isn't repeatable.
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

CARD_FILE = "card.json"
RESULTS_FILE = "results.json"
PROGRESS_FILE = "progress.json"   # live numbers while a run is going (the Child brain tab in Nova reads it)
FINISH_FILE = "FINISH"            # a person asked this run to stop practising now, then finish and save
OFF_SWITCH = "STOP"               # the guard's off switch (guard/server.py STOP_NAME): every run stops too
FORMAT = 1

_live: Path | None = None         # the folder of the run going on in this process
_device = "cpu"                   # ...and the device it trains on
_paused = 0.0                     # seconds it has waited for Nova so far (see gpu_wait)
_last_progress: dict = {}         # the numbers last reported, so a pause note keeps them on the Child brain tab

# Nova goes first on the graphics card: while her local brain is working she keeps this file's "until" in the
# future (nova/brain/gpu_signal.py), and a run on the GPU waits. Training at 100% made her answers time out
# (2026-11-21). Read here without importing Nova; NOVA_GPU_SIGNAL moves the file (tests do).
GPU_SIGNAL_ENV = "NOVA_GPU_SIGNAL"
GPU_POLL = 1.0


# ---------- experiments ----------

def experiment(defaults: dict | None = None) -> Callable:
    """Mark a function as a runnable experiment with its default settings."""

    def mark(fn: Callable) -> Callable:
        fn.defaults = dict(defaults or {})
        fn.is_experiment = True
        return fn

    return mark


def resolve(path: str) -> Callable:
    """Load 'childbrain.module:function'. Only marked experiments inside this package are allowed,
    so a card file can't make us import arbitrary code."""
    module_name, _, fn_name = path.partition(":")
    if not (module_name == "childbrain" or module_name.startswith("childbrain.")) or not fn_name:
        raise ValueError(f"experiment must be 'childbrain.<module>:<function>', got {path!r}")
    fn = getattr(importlib.import_module(module_name), fn_name, None)
    if not getattr(fn, "is_experiment", False):
        raise ValueError(f"{path} is not marked with @experiment")
    return fn


# ---------- live progress and stop requests ----------

def report_progress(**numbers: Any) -> None:
    """Write the run's live numbers to progress.json (replaced whole, so a reader never sees half a file).
    Does nothing outside `run()`, so experiments can call it freely (tests, reruns)."""
    if _live is None:
        return
    global _last_progress
    _last_progress = {k: v for k, v in numbers.items() if k != "status"}
    tmp = _live / (PROGRESS_FILE + ".tmp")
    tmp.write_text(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **numbers,
                               "paused_for_nova_s": round(_paused, 1)}), encoding="utf-8")
    os.replace(tmp, _live / PROGRESS_FILE)


def _gpu_signal() -> Path:
    given = os.environ.get(GPU_SIGNAL_ENV)
    if given:
        return Path(given)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Nova" / "gpu_wanted"


def nova_wants_gpu(now: float | None = None) -> bool:
    """Is Nova's local brain using the graphics card right now (or was it, within the last minute and a half)?"""
    try:
        until = float(json.loads(_gpu_signal().read_text(encoding="utf-8")).get("until", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return False  # no file, or a half-written one: Nova isn't asking
    return until > (now if now is not None else time.time())


def paused_seconds() -> float:
    """How long this run has waited for Nova so far: take it out of speed and timing numbers."""
    return _paused


def gpu_wait() -> float:
    """A run on the GPU waits here while Nova needs the card, then carries on where it was. A stop or finish
    request still ends the wait. Returns the seconds waited (0 when there was nothing to wait for)."""
    global _paused
    if _live is None or not str(_device).startswith("cuda") or not nova_wants_gpu():
        return 0.0
    start = time.perf_counter()
    print("paused: Nova is using the graphics card", flush=True)
    report_progress(**_last_progress, status="paused for Nova")
    try:
        import torch
        torch.cuda.empty_cache()          # hand back the memory PyTorch keeps cached, so her model fits
    except Exception:
        pass  # intentional: no torch / no CUDA here - there is nothing cached to hand back
    while nova_wants_gpu() and _stop_reason() is None:
        time.sleep(GPU_POLL)
    waited = time.perf_counter() - start
    _paused += waited
    print(f"resumed after {waited:.0f}s", flush=True)
    report_progress(**_last_progress)
    return waited


def stop_requested() -> str | None:
    """Why this run should stop practising now, or None: a FINISH file in its folder, or the guard's off switch.
    Every training loop calls this between steps, so it is also where a GPU run waits while Nova needs the card."""
    gpu_wait()
    return _stop_reason()


def _stop_reason() -> str | None:
    if _live is None:
        return None
    if (_live / FINISH_FILE).exists():
        return "a person asked it to finish"
    if (_live.parent / OFF_SWITCH).exists():
        return "the off switch is on"
    return None


# ---------- seed and versions ----------

def set_seed(seed: int) -> None:
    """Seed every random source and switch PyTorch to repeatable (deterministic) maths."""
    random.seed(seed)
    try:
        import torch
    except ImportError:
        return
    torch.manual_seed(seed)  # also seeds every GPU
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False


def source_fingerprint() -> str:
    """SHA-256 over the child brain's code (all .py files, runs/ excluded) and its requirements."""
    files = sorted(p for p in PACKAGE.rglob("*.py") if RUNS_DIR not in p.parents)
    req = ROOT / "requirements-childbrain.txt"
    if req.exists():
        files.append(req)
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(ROOT).as_posix().encode() + b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def code_version() -> dict:
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                                 timeout=10, check=True)
            return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    status = git("status", "--porcelain", "--", "childbrain", "requirements-childbrain.txt")
    return {
        "commit": git("rev-parse", "HEAD"),
        "uncommitted_changes": bool(status) if status is not None else None,
        "fingerprint": source_fingerprint(),
    }


def environment(device: str) -> dict:
    env = {"python": platform.python_version(), "os": platform.platform()}
    try:
        import torch
    except ImportError:
        return env
    env["torch"] = torch.__version__
    env["cuda"] = torch.version.cuda
    if device.startswith("cuda") and torch.cuda.is_available():
        env["gpu"] = torch.cuda.get_device_name(torch.device(device))
    return env


# ---------- the card ----------

@dataclass
class RunCard:
    experiment: str
    settings: dict
    seed: int
    device: str = "cpu"
    name: str = ""
    created: str = ""
    code: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    format: int = FORMAT

    @classmethod
    def new(cls, experiment: str, settings: dict | None = None, seed: int = 0,
            device: str = "cpu", name: str = "") -> "RunCard":
        fn = resolve(experiment)
        unknown = set(settings or {}) - set(fn.defaults)
        if unknown:
            raise ValueError(f"unknown settings for {experiment}: {sorted(unknown)}")
        full = {**fn.defaults, **(settings or {})}  # the card keeps every setting, not just changes
        return cls(experiment=experiment, settings=full, seed=int(seed), device=device,
                   name=name or experiment.rsplit(":", 1)[-1],
                   created=time.strftime("%Y-%m-%dT%H:%M:%S"),
                   code=code_version(), env=environment(device))

    def save(self, folder: Path) -> None:
        _write_json(folder / CARD_FILE, asdict(self))

    @classmethod
    def load(cls, folder: Path) -> "RunCard":
        data = json.loads((Path(folder) / CARD_FILE).read_text(encoding="utf-8"))
        if data.get("format") != FORMAT:
            raise ValueError(f"unknown run card format {data.get('format')!r}")
        return cls(**data)


def execute(card: RunCard) -> dict:
    """Seed, run the experiment, and return its results as plain JSON values."""
    fn = resolve(card.experiment)
    set_seed(card.seed)
    results = fn(dict(card.settings), card.device)
    if isinstance(results, dict):          # waiting for Nova (gpu_wait) - always 0 outside run(), e.g. on a rerun
        results["clock_paused_for_nova_s"] = round(_paused if _live is not None else 0.0, 1)   # wall-clock: not compared
    return json.loads(json.dumps(results))  # fails early if results aren't JSON; floats round-trip exactly


def run(experiment: str, settings: dict | None = None, seed: int = 0, device: str = "cpu",
        name: str = "", runs_dir: Path | None = None) -> Path:
    """Run an experiment, save its card and results, and return the run folder."""
    card = RunCard.new(experiment, settings, seed, device, name)
    folder = _new_folder(Path(runs_dir or RUNS_DIR), card.name)
    card.save(folder)  # saved first, so even a crashed run can be repeated
    print(f"run folder {folder}", flush=True)
    global _live, _device, _paused, _last_progress
    _live, _device, _paused, _last_progress = folder, card.device, 0.0, {}
    try:
        _write_json(folder / RESULTS_FILE, execute(card))
    finally:
        _live, _device = None, "cpu"
    return folder


@dataclass
class RerunReport:
    folder: Path
    same: bool
    differences: list[str]
    code_changed: bool
    results: dict

    def summary(self) -> str:
        lines = [f"{self.folder.name}: " + ("SAME numbers" if self.same else "DIFFERENT numbers")]
        if self.code_changed:
            lines.append("  note: the code changed since this run (fingerprint differs)")
        lines += [f"  {d}" for d in self.differences[:20]]
        return "\n".join(lines)


def rerun(folder: Path, tolerance: float = 0.0) -> RerunReport:
    """Run a saved card again and compare with its saved results. Doesn't change the run folder."""
    folder = Path(folder)
    card = RunCard.load(folder)
    saved = json.loads((folder / RESULTS_FILE).read_text(encoding="utf-8"))
    new = execute(card)
    diffs = compare(saved, new, tolerance)
    return RerunReport(folder=folder, same=not diffs, differences=diffs,
                       code_changed=card.code.get("fingerprint") != source_fingerprint(), results=new)


def compare(a: Any, b: Any, tolerance: float = 0.0, where: str = "results") -> list[str]:
    """List every place where two JSON values differ (exact by default).
    Keys starting with "clock_" hold wall-clock times, which never repeat exactly: they aren't compared."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = [f"{where}.{k}: only in {'saved' if k in a else 'new'}" for k in sorted(set(a) ^ set(b))
               if not str(k).startswith("clock_")]   # a timing one side never recorded (older runs) isn't a difference
        for k in sorted(set(a) & set(b)):
            if str(k).startswith("clock_"):
                continue
            out += compare(a[k], b[k], tolerance, f"{where}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{where}: length {len(a)} vs {len(b)}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += compare(x, y, tolerance, f"{where}[{i}]")
        return out
    num = (int, float)
    if isinstance(a, num) and isinstance(b, num) and not isinstance(a, bool) and not isinstance(b, bool):
        same = a == b or math.isclose(a, b, rel_tol=tolerance, abs_tol=tolerance)
        return [] if same else [f"{where}: {a!r} vs {b!r}"]
    return [] if a == b else [f"{where}: {a!r} vs {b!r}"]


# ---------- helpers ----------

def _new_folder(runs_dir: Path, name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name) or "run"
    base = f"{time.strftime('%Y%m%d-%H%M%S')}_{safe}"
    for i in range(1000):
        folder = runs_dir / (base if i == 0 else f"{base}-{i}")
        try:
            folder.mkdir(parents=True)
            return folder
        except FileExistsError:
            continue
    raise RuntimeError(f"couldn't make a new run folder in {runs_dir}")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
