"""Nova goes first on the graphics card: while her local brain works, child-brain training steps aside.

Nova and child-brain training share one 6 GB card, and Windows can't make a CUDA program "lower priority". With
training at 100%, Ollama answers took 10-20 min and then failed ("I can't reach Ollama", 2026-11-21). So the two
cooperate through one small file:

    %LOCALAPPDATA%\\Nova\\gpu_wanted      {"until": <unix time>, "pid": <Nova's process>}

While a request to the local Ollama model is in flight, `until` is kept 60 s ahead (refreshed every 20 s by a small
thread, so a crashed Nova frees the card within a minute). When the last request ends, `until` is set LINGER seconds
ahead, so training doesn't jump back in between the steps of one task. Training (the separate child brain project,
`C:\\Users\\vikra\\OneDrive\\Desktop\\child-brain`, childbrain/runcard.py `nova_wants_gpu`) pauses while `until` is in the
future. Cloud brains never touch the file.

The path can be moved with the NOVA_GPU_SIGNAL environment variable (tests do). Both sides read it the same way;
the child brain never imports Nova, so the few lines that read the file live there too.
"""
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from nova.log import log_exception

ENV = "NOVA_GPU_SIGNAL"
IN_FLIGHT = 60          # seconds ahead while a request runs (refreshed every REFRESH)
REFRESH = 20
LINGER = 90             # seconds ahead after the last request: a multi-step task keeps the card between its steps
_lock = threading.Lock()
_state = {"active": 0, "thread": None}


def path():
    """Where the signal lives: NOVA_GPU_SIGNAL, else %LOCALAPPDATA%\\Nova\\gpu_wanted."""
    given = os.environ.get(ENV)
    if given:
        return Path(given)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Nova" / "gpu_wanted"


def _write(seconds_ahead):
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"until": time.time() + seconds_ahead, "pid": os.getpid()}), encoding="utf-8")
        os.replace(tmp, p)                     # whole file at once: a reader never sees half of it
    except Exception as e:
        log_exception("gpu signal: write", e)  # never let the signal stop her answering


def _keep_fresh():
    while True:
        time.sleep(REFRESH)
        with _lock:
            if not _state["active"]:
                _state["thread"] = None
                return
        _write(IN_FLIGHT)


@contextmanager
def busy():
    """Around one request to the local model: the card is Nova's until LINGER seconds after the last one ends."""
    with _lock:
        _state["active"] += 1
        if not _state["thread"]:
            _state["thread"] = threading.Thread(target=_keep_fresh, daemon=True)
            _state["thread"].start()
    _write(IN_FLIGHT)
    try:
        yield
    finally:
        with _lock:
            _state["active"] -= 1
            last = not _state["active"]
        if last:
            _write(LINGER)


def wanted(now=None):
    """Is the card Nova's right now? (The same test the child brain makes.)"""
    try:
        return float(json.loads(path().read_text(encoding="utf-8")).get("until", 0)) > (now or time.time())
    except (OSError, ValueError, TypeError, AttributeError):
        return False  # intentional: no file / a half-written or odd one = Nova isn't asking for the card
