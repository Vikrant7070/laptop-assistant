"""Watch it play: the Child brain tab asks the child brain to record a saved brain playing one game, then plays
the recording back move by move (board, button chances, score guess, neurons, focus).

Like the rest of the tab, nothing here imports `childbrain`: it starts `python -m childbrain watch ...` with the
child brain's own Python, on the CPU (it never needs the graphics card, so it isn't a "job" and can run next to
training). Recordings are kept in runs/ui_watch/, so watching the same thing again is instant.
"""

import re
import subprocess
import threading
import time

from flask import abort, jsonify, send_file

from nova.core.app import api, config
from nova.core.app.childbrain_tab import (NO_WINDOW, _candidates, _need_home, _need_python, _safe_run, list_runs, runs_dir,
                                          versions)

WATCH_DIR = "ui_watch"
GAME_OK = re.compile(r"^[a-z0-9-]{1,32}$")
_busy = {}        # recording file name -> {"started", "error"} while (or after failing) being recorded
_lock = threading.Lock()


def _source(h, src):
    """A run folder name, or v:NNNN for a guard version. Returns its folder."""
    if src.startswith("v:"):
        vid = src[2:]
        folder = runs_dir(h) / "versions" / vid
        if not re.match(r"^\d{1,6}$", vid) or not (folder / "genes.json").exists():
            abort(404, "no such version")
        return folder
    return _safe_run(h, src)


def _recorder_home(h):
    """A copy of the child brain that has the recorder: the brain's own folder may hold older code, and the
    recorder only reads saved weights, so Nova's own copy can record a brain from any folder."""
    for folder in (h, config.ROOT, *_candidates()):
        if (folder / "childbrain" / "watch.py").exists():
            return folder
    abort(400, "no copy of the child brain has the recorder (childbrain/watch.py) yet")


def _record(h, py, folder, args, out, name):
    try:
        r = subprocess.run([str(py), "-m", "childbrain", "watch", str(folder), *args, "--out", str(out)],
                           cwd=_recorder_home(h),
                           capture_output=True, text=True, timeout=600, creationflags=NO_WINDOW)
        if r.returncode != 0 or not out.exists():
            err = (r.stderr or r.stdout).strip().splitlines()
            _busy[name]["error"] = err[-1] if err else f"the recorder stopped (exit {r.returncode})"
            return
    except (OSError, subprocess.SubprocessError) as e:
        _busy[name]["error"] = str(e)
        return
    _busy.pop(name, None)


@api.app.get("/api/childbrain/brains")
def childbrain_brains():
    """Brains that can be watched: runs that kept their weights, and the guard's saved versions."""
    h = _need_home()
    runs = [{"src": r["run"], "label": f"{r['name']} · {r.get('game') or ''}"
             + (" ✓" if r.get("passed") else "") + f" · {(r.get('created') or '')[5:16].replace('T', ' ')}",
             "game": r.get("game"), "passed": r.get("passed"), "exam": r.get("exam")}
            for r in list_runs(h) if r.get("watchable")]
    vers = [{"src": "v:" + v["id"], "label": f"version {v['id']} ({v['status']})" + (" · in use" if v["in_use"] else ""),
             "game": None} for v in versions(h)["versions"]]
    return jsonify(brains=runs + vers)


@api.app.post("/api/childbrain/watch")
def childbrain_watch():
    """Start (or reuse) a recording. Answers {ready, name} or {working, name}."""
    h = _need_home()
    b = api._body()
    src = api._text(b, "src")
    folder = _source(h, src)
    game = api._text(b, "game") or ""
    if game and not GAME_OK.match(game):
        abort(400, "bad game name")
    board = api._text(b, "board", "exam")
    if board not in ("exam", "new"):
        abort(400, "board must be exam or new")
    try:
        seed, steps = int(b.get("seed", 0)), max(10, min(int(b.get("steps", 300)), 2000))
    except (TypeError, ValueError):
        abort(400, "seed and steps must be whole numbers")
    if (runs_dir(h) / "STOP").exists():
        abort(409, "the off switch is on - clear it first")
    name = f"{src.replace(':', '_')}__{game or 'own'}_{board}_{seed}_{steps}.json"
    out = runs_dir(h) / WATCH_DIR / name
    if out.exists():
        return jsonify(ready=True, name=name)
    py = _need_python(h)
    _recorder_home(h)
    with _lock:
        if name in _busy and not _busy[name].get("error"):
            return jsonify(working=True, name=name)
        out.parent.mkdir(parents=True, exist_ok=True)
        _busy[name] = {"started": time.time()}
        args = ["--board", board, "--seed", str(seed), "--steps", str(steps)] + (["--game", game] if game else [])
        threading.Thread(target=_record, args=(h, py, folder, args, out, name), daemon=True).start()
    return jsonify(working=True, name=name)


@api.app.get("/api/childbrain/watch/<name>")
def childbrain_watch_get(name):
    """The recording when it's ready; otherwise how it's going."""
    h = _need_home()
    if not re.match(r"^[\w.:-]{1,200}\.json$", name) or name.startswith("."):
        abort(400, "bad name")
    out = runs_dir(h) / WATCH_DIR / name
    if out.exists():
        return send_file(out, mimetype="application/json", max_age=0)
    job = _busy.get(name)
    if not job:
        abort(404, "no such recording")
    if job.get("error"):
        return jsonify(error=job["error"]), 500
    return jsonify(working=True, seconds=round(time.time() - job["started"], 1)), 202
