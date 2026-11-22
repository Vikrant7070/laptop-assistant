"""Every background thread Nova starts goes through `spawn` (roadmap R11).

Before, ~80 places each made their own `threading.Thread(...)`: unnamed, and a crash in one went to stderr, which
nobody sees in the windowed app. Now each thread has a name, a crash is written to the task log (`log_exception`),
a thread can be restarted when it dies, and `threads()` lists what is running (GET /api/threads).

    runtime.spawn("background.suggest_watch", suggest_watch)
    runtime.spawn("phone.poll", self._poll, start=False)        # started later with .start()
    runtime.spawn("app.quit", quit_nova, delay=0.5)               # what threading.Timer did
    runtime.spawn("mail.check", check_mail, every=60)             # call it every minute until quit
    runtime.spawn("mcp.reader", self._read, restart=True)         # a crash -> logged, run again after a pause

`stop_all()` tells `every` loops and restarting threads to stop and waits a little for them. Loops that run their
own `while True` keep running until the process exits; they are daemon threads, as before.
"""
import itertools
import threading
import time

from nova.log import log_exception

RESTART_PAUSE = (1, 60)       # seconds before a restart: doubles after each crash, up to the second number

stopping = threading.Event()
_live = {}                    # id -> Info, while the thread runs
_ids = itertools.count(1)
_lock = threading.Lock()


class Info:
    def __init__(self, name, every, restart):
        self.id, self.name, self.every, self.restart = next(_ids), name, every, restart
        self.started = time.time()
        self.thread = None
        self.runs = 0
        self.last_error = ""

    def row(self):
        return {"name": self.name, "age": round(time.time() - self.started, 1), "alive": bool(self.thread and self.thread.is_alive()),
                "runs": self.runs, "every": self.every, "restart": self.restart, "last_error": self.last_error}


def _call(info, fn, args, kwargs):
    """One run of fn. -> True if it returned, False if it raised (logged)."""
    info.runs += 1
    try:
        fn(*args, **kwargs)
        return True
    except Exception as e:
        info.last_error = f"{type(e).__name__}: {e}"[:300]
        log_exception(f"thread {info.name}")
        return False


def _run(info, fn, args, kwargs, delay):
    with _lock:
        _live[info.id] = info
    try:
        if delay:
            time.sleep(delay)
        pause = RESTART_PAUSE[0]
        while True:
            ok = _call(info, fn, args, kwargs)
            if stopping.is_set():
                return
            if info.every is not None:
                if stopping.wait(info.every):
                    return
            elif not ok and info.restart:
                if stopping.wait(pause):
                    return
                pause = min(pause * 2, RESTART_PAUSE[1])
            else:
                return
    finally:
        with _lock:
            _live.pop(info.id, None)


def spawn(name, fn, args=(), kwargs=None, *, delay=0, every=None, restart=False, daemon=True, start=True):
    """Run fn(*args, **kwargs) in a named background thread. -> the Thread (not yet started when start=False)."""
    info = Info(name, every, restart)
    info.thread = threading.Thread(target=_run, args=(info, fn, tuple(args), dict(kwargs or {}), delay),
                                   name=name, daemon=daemon)
    if start:
        info.thread.start()
    return info.thread


def threads():
    """What spawn started and is still running, oldest first."""
    with _lock:
        rows = [i.row() for i in sorted(_live.values(), key=lambda i: i.id)]
    return rows


def stop_all(wait=1.0):
    """Ask spawned threads to stop (every/restart loops end at their next pause) and wait up to `wait` s in all."""
    stopping.set()
    end = time.time() + wait
    with _lock:
        live = [i.thread for i in _live.values() if i.thread is not threading.current_thread()]
    for th in live:
        left = end - time.time()
        if left <= 0:
            break
        th.join(left)
    return [i.name for i in list(_live.values()) if i.thread and i.thread.is_alive()]
