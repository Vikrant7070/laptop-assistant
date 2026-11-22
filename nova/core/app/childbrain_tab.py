"""The Child brain tab (/childbrain): watch the child brain's progress and control it from Nova's window.

Nova and the child brain stay apart (childbrain/__init__.py): nothing here imports `childbrain`. This module only
reads the files its runs leave (card.json, results.json, progress.json, versions/) and starts its own command line
(`python -m childbrain ...`, `python -m childbrain.guard ...`) with the child brain's Python (.venv-childbrain).

One job at a time (training, a rerun, the nightly exam): they all want the graphics card. The job is remembered in
runs/ui_job.json, so the tab still sees - and can stop - a run started before Nova restarted.

Stopping, gentlest first:
  finish     FINISH file in the run's folder: the learner stops practising, takes its exam and saves its results
  kill       end the job's process tree now (nothing is saved for that run)
  off switch the guard's STOP file + end every child brain process on this laptop; nothing starts until cleared
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
from flask import abort, jsonify, send_from_directory

from nova.core.app import api, config

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
VENV = Path(".venv-childbrain") / "Scripts" / "python.exe"
JOB_FILE = "ui_job.json"
LOG_DIR = "ui_logs"
NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RUN_OK = re.compile(r"^[A-Za-z0-9][\w.-]{0,119}$")    # a run folder's name: no slashes, no leading dot
KEY_OK = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")
GPU_BUSY_PCT = 10          # the quiet-GPU gate from WP 0.7: <=10% busy and >=1.5 GB free
GPU_FREE_MB = 1536
EXPERIMENTS = {            # what the start form offers; the code's own list wins when it can be read (info())
    "learn-alone": "childbrain.measure.learn_alone:learn_alone",
    "sanity": "childbrain.measure.sanity:tiny_train",
    "random-play": "childbrain.measure.random_play:random_play",
}

_lock = threading.Lock()
_proc = None               # the Popen of the job this Nova started (for its exit code)
_info_cache = {}           # (home, learner mtime) -> info
_summary_cache = {}        # (folder, results mtime) -> summary


# ---------- where the child brain lives ----------

def _candidates():
    """Folders holding a copy of the child brain: Nova's own folder, then its worktrees (or, when Nova itself
    runs from a worktree, the main folder and the other worktrees)."""
    out = [config.ROOT]
    wt = config.ROOT / ".claude" / "worktrees"
    if config.ROOT.parent.name == "worktrees" and config.ROOT.parent.parent.name == ".claude":
        wt = config.ROOT.parent
        out.append(wt.parent.parent)
    if wt.is_dir():
        out += sorted(p for p in wt.iterdir() if p.is_dir() and p not in out)
    return [p for p in out if (p / "childbrain" / "__init__.py").exists()]


def _describe(folder):
    runs = folder / "childbrain" / "runs"
    n = sum(1 for p in runs.glob("*/card.json")) if runs.is_dir() else 0
    own = (folder / VENV).exists()
    borrowed = None if own else python_exe(folder)          # runs this folder's code with another copy's Python
    return {"path": str(folder), "name": "Nova's folder" if folder == config.ROOT else folder.name,
            "learner": (folder / "childbrain" / "measure" / "learn_alone.py").exists(),
            "python": own or bool(borrowed), "python_from": borrowed.parents[2].name if borrowed else "", "runs": n}


def home():
    """The child brain folder the tab works with: the one picked in the tab, else the best-equipped one
    (has the learner, then its own Python, then saved runs; Nova's folder wins a tie)."""
    cands = _candidates()
    picked = config.settings().get("childbrain_home")
    if picked and Path(picked) in cands:
        return Path(picked)
    if not cands:
        return None

    def rank(p):
        d = _describe(p)
        return d["learner"] * 4 + d["python"] * 2 + bool(d["runs"]), p == config.ROOT

    return max(cands, key=rank)


def python_exe(h=None):
    """The child brain's Python: its folder's .venv-childbrain, else any other copy's (the code comes from cwd)."""
    h = h or home()
    for folder in [h] + [p for p in _candidates() if p != h]:
        if folder and (folder / VENV).exists():
            return folder / VENV
    return None


def runs_dir(h=None):
    h = h or home()
    return h / "childbrain" / "runs" if h else None


def _need_home():
    h = home()
    if not h:
        abort(400, "no child brain folder found (childbrain/ in Nova's folder or a .claude/worktrees folder)")
    return h


def _need_python(h):
    py = python_exe(h)
    if not py:
        abort(400, "the child brain's Python isn't set up: py -3.12 -m venv .venv-childbrain, then "
                   ".venv-childbrain\\Scripts\\python -m pip install -r requirements-childbrain.txt")
    return py


# ---------- the job (one at a time) ----------

def _job_path(h):
    return runs_dir(h) / JOB_FILE


def _read_job(h):
    try:
        return json.loads(_job_path(h).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_job(h, job):
    p = _job_path(h)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, indent=2), encoding="utf-8")
    tmp.replace(p)


def _alive(job):
    """Is the job's process still the one we started? (A pid can be reused after it ends.)"""
    try:
        proc = psutil.Process(job["pid"])
        return proc.is_running() and abs(proc.create_time() - job["started"]) < 5 and proc.status() != "zombie"
    except (psutil.Error, KeyError, TypeError):
        return False


def _tail(path, lines=40):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16000))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _run_folder(h, job):
    """The run folder of a training/rerun job: printed as 'run folder ...' when it starts."""
    if job.get("folder"):
        return Path(job["folder"])
    m = re.search(r"^run folder (.+)$", _tail(job.get("log", ""), 400), re.M)
    if m and Path(m.group(1).strip()).is_dir():
        job["folder"] = m.group(1).strip()
        _write_job(h, job)
        return Path(job["folder"])
    return None


def current_job(h=None):
    """The latest job with its state: running, finished (exit code when known) or stopped."""
    h = h or home()
    job = _read_job(h) if h else None
    if not job:
        return None
    running = _alive(job)
    if not running and job.get("state") == "running":
        code = _proc.poll() if _proc is not None and _proc.pid == job["pid"] else None
        job.update(state="finished", ended=time.time(), exit=code)
        _write_job(h, job)
    job = dict(job, running=running, log_tail=_tail(job.get("log", "")))
    folder = _run_folder(h, job) if job["kind"] in ("train", "rerun") else None
    if folder:
        job["run"] = folder.name
        try:
            job["progress"] = json.loads((folder / "progress.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        job["finish_asked"] = (folder / "FINISH").exists()
    return job


def _start(h, kind, label, args):
    """Start `python <args>` in the child brain folder, logging to runs/ui_logs/. One job at a time."""
    global _proc
    py = _need_python(h)
    with _lock:
        job = current_job(h)
        if job and job["running"]:
            abort(409, f"{job['label']} is still running - stop it first")
        if (runs_dir(h) / "STOP").exists() and kind != "check":
            abort(409, "the off switch is on - clear it first")
        logs = runs_dir(h) / LOG_DIR
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"{time.strftime('%Y%m%d-%H%M%S')}_{kind}.log"
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        env.pop("PYTHONPATH", None)
        with open(log, "wb") as out:
            out.write(f"$ {py.name} {' '.join(args)}\n(in {h})\n\n".encode())
            out.flush()
            _proc = subprocess.Popen([str(py), *args], cwd=h, stdout=out, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, env=env, creationflags=DETACHED)
        started = psutil.Process(_proc.pid).create_time()
        job = {"kind": kind, "label": label, "pid": _proc.pid, "started": started, "args": args,
               "log": str(log), "state": "running", "home": str(h)}
        _write_job(h, job)
    return job


# ---------- keep the laptop awake while it trains (the Training switch) ----------
# A sleeping laptop freezes the run (on battery it sleeps after a few minutes). While a training job runs, a small
# thread holds Windows' "system required" flag; the screen may still turn off, and closing the lid still sleeps.

ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
AWAKE_CHECK = 30
_awake = {"thread": None, "on": False}


def _set_awake(on):
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


def _training(h):
    job = current_job(h)
    return bool(job and job["running"] and job["kind"] == "train")


def _hold_awake(h):
    _set_awake(True)                     # the flag belongs to this thread: set and cleared here
    _awake["on"] = True
    try:
        while _training(h):
            time.sleep(AWAKE_CHECK)
    finally:
        _set_awake(False)
        _awake.update(on=False, thread=None)


def keep_awake(h):
    """While a training job is running, the laptop doesn't go to sleep. Safe to call often."""
    with _lock:
        if _awake["thread"] or not _training(h):
            return
        _awake["thread"] = threading.Thread(target=_hold_awake, args=(h,), daemon=True)
        _awake["thread"].start()


def _kill_tree(pid):
    try:
        root = psutil.Process(pid)
        procs = root.children(recursive=True) + [root]
    except psutil.Error:
        return 0
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=5)
    return len(procs)


def _childbrain_processes():
    """Every child brain process on this laptop (runs started here, in a terminal, guarded brains, exams)."""
    me = os.getpid()
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
        except (psutil.Error, TypeError):
            continue
        if p.info["pid"] != me and "python" in (p.info["name"] or "").lower() and \
                re.search(r"-m\s+childbrain(\.|\s|$)|childbrain[\\/.]brainhost|childbrain\.guard", cmd):
            out.append(p)
    pids = {p.pid for p in out}
    top = []
    for p in out:                 # a venv's python.exe starts the real Python as its child: count the run once
        try:
            if p.ppid() not in pids:
                top.append(p)
        except psutil.Error:
            pass
    return top


def _others(job):
    """Child brain processes this tab didn't start (a terminal, another session): what each one is doing."""
    mine = set()
    if job and job.get("running"):
        try:
            root = psutil.Process(job["pid"])
            mine = {root.pid} | {c.pid for c in root.children(recursive=True)}
        except psutil.Error:
            pass
    out = []
    for p in _childbrain_processes():
        if p.pid in mine:
            continue
        try:
            cmd = p.cmdline()
            what = " ".join(cmd[cmd.index("-m") + 1:]) if "-m" in cmd else " ".join(cmd[1:])
            out.append({"pid": p.pid, "what": what[:200], "started": p.create_time(), "folder": Path(p.cwd()).name})
        except (psutil.Error, ValueError):
            continue
    return out


# ---------- the graphics card ----------

def gpu():
    """The graphics card right now (nvidia-smi), and whether it's free for training."""
    try:
        q = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8,
                           creationflags=NO_WINDOW)
        name, util, used, total, temp = [x.strip() for x in q.stdout.strip().splitlines()[0].split(",")]
        apps = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8,
                              creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {"ok": False, "why": "nvidia-smi didn't answer"}
    util, used, total = int(util), int(used), int(total)
    holders = []
    for line in apps.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) == 3 and not parts[1].startswith("["):   # "[Insufficient Permissions]": not ours to name
            holders.append({"pid": parts[0], "name": Path(parts[1]).name, "mb": parts[2]})
    free = util <= GPU_BUSY_PCT and total - used >= GPU_FREE_MB
    return {"ok": True, "name": name, "util": util, "used_mb": used, "total_mb": total, "temp": temp,
            "apps": holders, "free": free,
            "why": "" if free else f"{util}% busy, {total - used} MB free (training wants <=10% busy and 1.5 GB free)"}


# ---------- what the code offers (games, presets) ----------

INFO_SCRIPT = r"""
import json
from childbrain.__main__ import EXPERIMENTS
from childbrain.runcard import resolve
out = {"experiments": {}, "games": {}, "presets": {}}
for k, v in EXPERIMENTS.items():
    try:
        out["experiments"][k] = {"target": v, "defaults": resolve(v).defaults}
    except Exception as e:
        out["experiments"][k] = {"target": v, "defaults": {}, "error": str(e)}
try:
    from childbrain.measure import learn_alone as la
    out["games"] = {g: {"target": t[0], "floor": t[1], "best": t[2]} for g, t in la.TARGETS.items()}
    out["presets"] = {"passed": getattr(la, "PASSED_WITH", {}), "best_try": getattr(la, "BEST_TRY", {})}
except Exception as e:
    out["games_error"] = str(e)
print(json.dumps(out))
"""


_info_busy = set()


def _ask_code(h, key):
    py = python_exe(h)
    data = {}
    try:
        r = subprocess.run([str(py), "-c", INFO_SCRIPT], cwd=h, capture_output=True, text=True, timeout=180,
                           creationflags=NO_WINDOW)
        data = json.loads(r.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
        data = {"error": f"couldn't ask the child brain's code: {e}"}
    finally:
        _info_cache[key] = data
        _info_busy.discard(key)


def info(h):
    """Experiments with their default settings, games with targets, and the settings that worked. Asking the
    code means importing PyTorch (up to half a minute while training uses the card), so it's asked in the
    background once per change of the learner; until then this returns the built-in list with loading=True."""
    learner = h / "childbrain" / "measure" / "learn_alone.py"
    key = (str(h), learner.stat().st_mtime if learner.exists() else 0)
    if key in _info_cache and "error" not in _info_cache[key]:
        return _info_cache[key]
    fallback = {"experiments": {k: {"target": v, "defaults": {}} for k, v in EXPERIMENTS.items()},
                "games": {}, "presets": {}}
    if not python_exe(h):
        return fallback
    if key in _info_cache:                                  # it failed: say why, and try again next time
        fallback["error"] = _info_cache.pop(key)["error"]
        return fallback
    with _lock:
        if key not in _info_busy:
            _info_busy.add(key)
            threading.Thread(target=_ask_code, args=(h, key), daemon=True).start()
    return dict(fallback, loading=True)


# ---------- runs ----------

def _down(xs, ys, n=300):
    """At most n points of a curve (keeps the last one)."""
    pts = [(x, y) for x, y in zip(xs or [], ys or []) if y is not None]
    if len(pts) > n:
        step = len(pts) / n
        pts = [pts[int(i * step)] for i in range(n)] + [pts[-1]]
    return [[x, y] for x, y in pts]


def _summary(folder):
    card_p, res_p = folder / "card.json", folder / "results.json"
    mt = res_p.stat().st_mtime if res_p.exists() else 0
    key = (str(folder), mt, (folder / "progress.json").exists())
    if key in _summary_cache:
        return _summary_cache[key]
    try:
        card = json.loads(card_p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    s = card.get("settings", {})
    out = {"run": folder.name, "name": card.get("name"), "created": card.get("created"),
           "experiment": card.get("experiment", "").rsplit(":", 1)[-1], "device": card.get("device"),
           "seed": card.get("seed"), "game": s.get("game"), "done": bool(mt)}
    if mt:
        try:
            r = json.loads(res_p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            r = {}
        out.update(passed=r.get("passed"), exam=r.get("exam_score_greedy"), target=r.get("target"),
                   floor=r.get("random_floor"), steps=r.get("steps_practised"), seconds=r.get("clock_seconds"),
                   stopped_early=r.get("stopped_early"), game=r.get("game", out["game"]),
                   watchable="weights" in r and "genes" in r,
                   checks=_down(r.get("checks_steps"), r.get("checks_score"), 120))
    _summary_cache[key] = out
    return out


CACHE_FILE = "ui_summaries.json"   # results.json can be 75 MB (it keeps the weights): read each one once


def list_runs(h):
    rd = runs_dir(h)
    if not rd or not rd.is_dir():
        return []
    try:
        disk = json.loads((rd / CACHE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        disk = {}
    rows, changed = [], False
    for c in rd.glob("*/card.json"):
        res = c.parent / "results.json"
        stamp = res.stat().st_mtime if res.exists() else 0
        hit = disk.get(c.parent.name)
        if stamp and hit and hit.get("stamp") == stamp and "watchable" in hit["row"]:
            rows.append(hit["row"])
            continue
        row = _summary(c.parent)
        if row:
            rows.append(row)
            if stamp:
                disk[c.parent.name] = {"stamp": stamp, "row": row}
                changed = True
    if changed:
        tmp = rd / (CACHE_FILE + ".tmp")
        tmp.write_text(json.dumps(disk), encoding="utf-8")
        tmp.replace(rd / CACHE_FILE)
    return sorted(rows, key=lambda r: r["run"], reverse=True)


def scoreboard(runs, games):
    """Per practice game: its target, and the best exam any finished run got."""
    board = {g: {"game": g, **v, "best_exam": None, "passed": False, "runs": 0, "best_run": None}
             for g, v in games.items()}
    for r in runs:
        g = r.get("game")
        if r.get("experiment") != "learn_alone" or not r.get("done") or g is None or r.get("exam") is None:
            continue
        b = board.setdefault(g, {"game": g, "target": r.get("target"), "floor": r.get("floor"), "best": "",
                                 "best_exam": None, "passed": False, "runs": 0, "best_run": None})
        b["runs"] += 1
        if b["best_exam"] is None or r["exam"] > b["best_exam"]:
            b.update(best_exam=r["exam"], best_run=r["run"])
        b["passed"] = b["passed"] or bool(r.get("passed"))
    return list(board.values())


def _safe_run(h, name):
    if not RUN_OK.match(name or ""):
        abort(400, "bad run name")
    folder = runs_dir(h) / name
    if folder.parent != runs_dir(h) or not (folder / "card.json").exists():
        abort(404, "no such run")
    return folder


# ---------- versions (the guard's saved brains) ----------

def versions(h):
    root = runs_dir(h) / "versions"
    try:
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    rows = []
    for d in sorted(root.glob("[0-9]*")) if root.is_dir() else []:
        try:
            card = json.loads((d / "card.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows.append({"id": d.name, "status": card.get("status"), "exam": (card.get("exam") or {}).get("total"),
                     "why": card.get("why", ""), "source": card.get("source", ""), "note": card.get("note", ""),
                     "in_use": d.name == state.get("current")})
    report = _tail(root / "report.txt", 8)
    return {"state": state, "versions": rows, "report": report}


def _quick(h, args, timeout=120):
    """Run a short child brain command and return what it printed."""
    py = _need_python(h)
    try:
        r = subprocess.run([str(py), *args], cwd=h, capture_output=True, text=True, timeout=timeout,
                           creationflags=NO_WINDOW, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    except subprocess.TimeoutExpired:
        return {"ok": False, "out": f"took longer than {timeout} s"}
    return {"ok": r.returncode == 0, "out": (r.stdout + r.stderr).strip()[-6000:]}


# ---------- routes ----------

@api.app.get("/childbrain")
def childbrain_page():
    return send_from_directory(api.app.static_folder, "childbrain.html")


@api.app.get("/api/childbrain/status")
def childbrain_status():
    h = home()
    if not h:
        return jsonify(found=False, folders=[])
    rd = runs_dir(h)
    stop = rd / "STOP"
    job = current_job(h)
    others = _others(job)
    keep_awake(h)                        # also picks up a run that was started before Nova restarted
    return jsonify(found=True, home=str(h), folders=[_describe(p) for p in _candidates()], awake=_awake["on"],
                   python=str(python_exe(h) or ""), off_switch=stop.exists(),
                   off_since=stop.read_text(encoding="utf-8").strip() if stop.exists() else "",
                   job=job, others=others, processes=len(others) + bool(job and job["running"]), runs_dir=str(rd))


@api.app.get("/api/childbrain/overview")
def childbrain_overview():
    h = _need_home()
    inf = info(h)
    runs = list_runs(h)
    return jsonify(info=inf, runs=runs, board=scoreboard(runs, inf.get("games", {})), versions=versions(h))


@api.app.get("/api/childbrain/gpu")
def childbrain_gpu():
    return jsonify(gpu())


@api.app.get("/api/childbrain/run/<name>")
def childbrain_run(name):
    h = _need_home()
    folder = _safe_run(h, name)
    card = json.loads((folder / "card.json").read_text(encoding="utf-8"))
    out = {"run": name, "card": {k: card.get(k) for k in ("experiment", "name", "created", "seed", "device",
                                                         "settings", "code", "env")}, "folder": str(folder)}
    try:
        r = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        out["results"] = {k: v for k, v in r.items() if not isinstance(v, (list, dict))}
        out["genes"] = r.get("genes")
        out["checks"] = _down(r.get("checks_steps"), r.get("checks_score"))
        out["practice"] = _down(r.get("practice_steps"), r.get("practice_score"))
    except (OSError, ValueError):
        pass
    try:
        out["progress"] = json.loads((folder / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return jsonify(out)


@api.app.post("/api/childbrain/home")
def childbrain_set_home():
    path = api._text(api._body(), "path")
    if path and Path(path) not in _candidates():
        abort(400, "not a child brain folder")
    s = config.settings()
    s["childbrain_home"] = path
    config.save_settings(s)
    return jsonify(ok=True, home=str(home() or ""))


def _settings_from(body):
    """--set values from the form: {key: value}, keys checked, values as JSON."""
    out = []
    raw = body.get("settings") or {}
    if not isinstance(raw, dict):
        abort(400, "'settings' must be an object")
    for k, v in raw.items():
        if not KEY_OK.match(str(k)):
            abort(400, f"bad setting name {k!r}")
        if v is None or v == "":
            continue
        out.append(f"{k}={json.dumps(v)}")
    return out


@api.app.post("/api/childbrain/train")
def childbrain_train():
    h = _need_home()
    b = api._body()
    exp = api._text(b, "experiment", "learn-alone")
    known = info(h).get("experiments") or EXPERIMENTS
    if exp not in known:
        abort(400, f"unknown experiment {exp!r}")
    device = api._text(b, "device", "cuda")
    if device not in ("cuda", "cpu"):
        abort(400, "device must be cuda or cpu")
    try:
        seed = int(b.get("seed", 0))
    except (TypeError, ValueError):
        abort(400, "seed must be a whole number")
    name = api._text(b, "name")
    if name and not NAME_OK.match(name):
        abort(400, "name: letters, numbers, . _ - only")
    if device == "cuda" and not b.get("force"):
        g = gpu()
        if g.get("ok") and not g["free"]:
            return jsonify(error=f"the graphics card is busy: {g['why']}", gpu_busy=True, gpu=g), 409
    args = ["-m", "childbrain", "run", exp, "--device", device, "--seed", str(seed)]
    if name:
        args += ["--name", name]
    sets = _settings_from(b)
    if sets:
        args += ["--set", *sets]
    game = (b.get("settings") or {}).get("game")
    label = f"training {game or exp}" + (f" ({name})" if name else "")
    job = _start(h, "train", label, args)
    keep_awake(h)
    return jsonify(ok=True, job=job)


@api.app.post("/api/childbrain/rerun")
def childbrain_rerun():
    h = _need_home()
    folder = _safe_run(h, api._text(api._body(), "run"))
    return jsonify(ok=True, job=_start(h, "rerun", f"re-checking {folder.name}",
                                       ["-m", "childbrain", "rerun", str(folder)]))


@api.app.post("/api/childbrain/nightly")
def childbrain_nightly():
    h = _need_home()
    return jsonify(ok=True, job=_start(h, "nightly", "the nightly exam", ["-m", "childbrain.guard", "nightly"]))


@api.app.post("/api/childbrain/finish")
def childbrain_finish():
    """Gentle stop: the run stops practising, takes its exam, saves its results."""
    h = _need_home()
    job = current_job(h)
    if not job or not job["running"]:
        abort(409, "nothing is running")
    folder = _run_folder(h, job)
    if job["kind"] != "train" or not folder:
        abort(409, "only a training run can finish early - use Kill")
    (folder / "FINISH").write_text(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n", encoding="utf-8")
    return jsonify(ok=True, msg="asked it to finish: it stops after this round, takes its exam and saves")


@api.app.post("/api/childbrain/kill")
def childbrain_kill():
    h = _need_home()
    job = current_job(h)
    if not job or not job["running"]:
        abort(409, "nothing is running")
    n = _kill_tree(job["pid"])
    raw = _read_job(h) or job
    raw.update(state="killed", ended=time.time())
    _write_job(h, raw)
    return jsonify(ok=True, msg=f"stopped {job['label']} ({n} process{'es' if n != 1 else ''})")


@api.app.post("/api/childbrain/off")
def childbrain_off():
    """The off switch: the guard's STOP file (guarded brains stop within a second, learners after their round),
    then end whatever child brain process is still there. Nothing starts again until a person clears it."""
    h = _need_home()
    stop = runs_dir(h) / "STOP"
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n", encoding="utf-8")
    time.sleep(1.5)
    killed = 0
    job = current_job(h)
    if job and job["running"]:
        killed += _kill_tree(job["pid"])
        raw = _read_job(h) or job
        raw.update(state="stopped by the off switch", ended=time.time())
        _write_job(h, raw)
    for p in _childbrain_processes():
        killed += _kill_tree(p.pid)
    return jsonify(ok=True, msg=f"off switch ON. Ended {killed} child brain process{'es' if killed != 1 else ''}.")


@api.app.post("/api/childbrain/clear")
def childbrain_clear():
    h = _need_home()
    stop = runs_dir(h) / "STOP"
    if stop.exists():
        stop.unlink()
        return jsonify(ok=True, msg="off switch cleared - the child brain may run again")
    return jsonify(ok=True, msg="the off switch was already off")


@api.app.post("/api/childbrain/check")
def childbrain_check():
    return jsonify(_quick(_need_home(), ["-m", "childbrain.guard", "check"]))


@api.app.post("/api/childbrain/rollback")
def childbrain_rollback():
    h = _need_home()
    vid = api._text(api._body(), "version")
    if not re.match(r"^\d{1,6}$", vid):
        abort(400, "bad version")
    return jsonify(_quick(h, ["-m", "childbrain.guard", "rollback", vid]))


@api.app.post("/api/childbrain/open")
def childbrain_open():
    """Show a run's folder (or the runs folder) in Explorer."""
    h = _need_home()
    name = api._text(api._body(), "run")
    folder = _safe_run(h, name) if name else runs_dir(h)
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(folder)  # noqa: S606 - a folder we checked, opened in Explorer
    return jsonify(ok=True)


@api.app.get("/api/childbrain/log")
def childbrain_log():
    h = _need_home()
    job = _read_job(h)
    return jsonify(log=_tail(job["log"], 400) if job else "")
