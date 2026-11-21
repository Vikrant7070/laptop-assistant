"""Crash breadcrumbs for the places that must not crash the assistant.

Nova tries never to take a turn down because a screen look, a sound, a window or a notification
failed. That is right, but a bare `except Exception: pass` also makes the failure invisible when the
same thing keeps failing. `log_exception` writes one line plus the traceback to the task log and is
rate-limited per place, so a hot loop (a failing partial transcript, say) cannot flood the file.

Handlers that are genuinely best effort keep a `# intentional: <why>` comment instead; the test
suite enforces that every silent handler has one or the other.
"""
import sys
import threading
import time
import traceback

from nova import store

RATE_SECS = 60          # at most one report per place per minute
_last = {}
_lock = threading.Lock()


def log_exception(where, exc=None):
    """Report `where` failing. Pass exc explicitly when logging outside an except block. -> wrote a line?"""
    now = time.time()
    with _lock:
        if now - _last.get(where, 0.0) < RATE_SECS:
            return False
        _last[where] = now
    if exc is None:
        exc = sys.exc_info()[1]
    if exc is None:
        detail = "no exception"
    else:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
    stamp = time.strftime("%H:%M:%S")
    try:
        store.current().task_log.parent.mkdir(parents=True, exist_ok=True)
        with store.current().task_log.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} ERROR {where}: {type(exc).__name__ if exc else 'unknown'}: {exc}\n"
                    + "".join("      " + line + "\n" for line in detail.splitlines()))
    except OSError:
        return False        # the log is the last thing that may fail; never raise from here
    return True


def note(line):
    """One plain line in the task log (what a tool really did, for fixing it later). Never raises."""
    try:
        store.current().task_log.parent.mkdir(parents=True, exist_ok=True)
        with store.current().task_log.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
    except OSError:
        return False        # the log is the last thing that may fail; never raise from here
    return True


def reset():
    """Forget the rate limit (tests, and a new day's log)."""
    with _lock:
        _last.clear()
