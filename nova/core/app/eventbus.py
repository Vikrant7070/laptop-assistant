"""One event stream for every page (roadmap R9): what happens in Nova is published once on the bus, and each open page
gets it pushed over GET /api/stream (Server-Sent Events, served by background.py) instead of asking every second.

    eventbus.publish("thought", {...})    # -> an event {"id", "kind", "data", "t"} to every subscriber
    q = eventbus.bus.subscribe(after_id)  # a queue per subscriber; events after after_id are replayed first

The bus keeps the last KEEP events, so a page that reconnects (the browser sends Last-Event-ID) misses nothing that
is still kept. A subscriber that falls MAX_QUEUE events behind is dropped; its page reconnects and catches up.
Chat events ("event") also keep the old /api/events promise: one that no page has seen yet is shown to the next page
that opens (`backlog`), but two open pages no longer take each other's events.

Pure: it imports no other app module, so any of them can publish.
"""
import queue
import threading
import time
from collections import deque

KEEP = 500
MAX_QUEUE = 1000
HEARTBEAT = 15            # seconds between keep-alive comments on an idle stream


class Bus:
    def __init__(self, keep=KEEP, max_queue=MAX_QUEUE):
        self._lock = threading.Lock()
        self._log = deque(maxlen=keep)
        self._next = 1
        self._subs = set()
        self.max_queue = max_queue

    @property
    def last_id(self):
        with self._lock:
            return self._next - 1

    def publish(self, kind, data=None):
        """Give the event an id, keep it, and hand it to every subscriber. -> the event."""
        with self._lock:
            ev = {"id": self._next, "kind": kind, "data": data if data is not None else {}, "t": round(time.time(), 2)}
            self._next += 1
            self._log.append(ev)
            for q in list(self._subs):
                try:
                    q.put_nowait(ev)
                except queue.Full:            # too slow: drop it, its page reconnects with Last-Event-ID and catches up
                    q.dropped = True
                    self._subs.discard(q)
        return ev

    def subscribe(self, after_id=None, backlog=()):
        """A queue of the events from now on. after_id: first replay the kept events after it (a reconnect).
        backlog: kinds whose kept events no page has seen yet are replayed too (a page opened for the first time)."""
        q = queue.Queue(maxsize=self.max_queue)
        q.dropped = False
        with self._lock:
            for ev in self._log:
                if (after_id is not None and ev["id"] > after_id) or (
                        after_id is None and ev["kind"] in backlog and not ev.get("seen")):
                    q.put_nowait(ev)
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def since(self, after_id, kinds=None):
        """The kept events after after_id (of these kinds), for the polling fallback."""
        with self._lock:
            return [ev for ev in self._log if ev["id"] > after_id and (kinds is None or ev["kind"] in kinds)]

    def unseen(self, kind):
        """Kept events of this kind that no page has been sent yet; they count as seen from now on."""
        with self._lock:
            out = [ev for ev in self._log if ev["kind"] == kind and not ev.get("seen")]
            for ev in out:
                ev["seen"] = True
            return out

    def subscribers(self):
        with self._lock:
            return len(self._subs)


bus = Bus()


def publish(kind, data=None):
    return bus.publish(kind, data)


class BusyLock:
    """threading.Lock that publishes "busy" when it is taken or let go: session.lock, held while she handles a request,
    so the pages see her start and finish without asking."""

    def __init__(self):
        self._lock = threading.Lock()

    def acquire(self, blocking=True, timeout=-1):
        got = self._lock.acquire(blocking, timeout)
        if got:
            publish("busy", {"busy": True})
        return got

    def release(self):
        self._lock.release()
        publish("busy", {"busy": False})

    def locked(self):
        return self._lock.locked()

    __enter__ = acquire

    def __exit__(self, *exc):
        self.release()
