"""Nova's question list (manager plan, section 9 answer 4): what she wants to ask the user while she learns.

Everything lives in one readable file on this laptop: data/questions.json

  - Non-urgent questions (save this as a skill? is this right?) wait in the list and are asked once a day, one at a
    time, each with its reason, at a moment the user is talking to her at the laptop.
  - Urgent questions (a worker is blocked and needs an OK, later phases) are asked as soon as the user is there.
  - A question the user skips comes back on another day; after MAX_ASKS skips it is dropped.

Pure code: no brain calls. app.py decides when the user is "there" and handles the yes/no.
"""
import json
import threading
import time

from nova.log import log_exception
from nova import store

DAILY_SECS = 20 * 3600          # a new day's list may start this long after the last one started
SESSION_SECS = 3 * 3600         # ...and that day's list goes on (next question after each answer) for this long
MAX_ASKS = 3                    # asked this many times with no answer: not asked again
MAX_OPEN = 40                   # a sanity limit: the oldest open questions go first
_lock = threading.RLock()


def _blank():
    return {"version": 1, "questions": [], "next_id": 1, "daily_started": 0.0}


def load():
    with _lock:
        try:
            d = json.loads(store.current().questions.read_text(encoding="utf-8"))
        except FileNotFoundError:
            d = _blank()
        except Exception:
            log_exception("questions read")     # a broken questions file starts blank; the log says it happened
            d = _blank()
        for k, v in _blank().items():
            d.setdefault(k, v)
        return d


def _save(d):
    with _lock:
        store.current().questions.parent.mkdir(parents=True, exist_ok=True)
        tmp = store.current().questions.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(store.current().questions)


def add(kind, text, why, data=None, key="", urgent=False, now=None):
    """Queue a question. key: the same key is never queued twice (open, answered or dropped). Returns its id, or
    None when it was already asked about."""
    now = now or time.time()
    with _lock:
        d = load()
        if key and any(q.get("key") == key for q in d["questions"]):
            return None
        q = {"id": d["next_id"], "kind": kind, "text": str(text)[:300], "why": str(why)[:300], "data": data or {},
             "key": key, "urgent": bool(urgent), "created": now, "asked": 0, "asked_at": 0.0, "status": "open",
             "answer": ""}
        d["next_id"] += 1
        d["questions"].append(q)
        open_ = [x for x in d["questions"] if x["status"] == "open"]
        for old in open_[:max(0, len(open_) - MAX_OPEN)]:
            old["status"] = "dropped"
        _save(d)
        return q["id"]


def open_questions():
    return [q for q in load()["questions"] if q["status"] == "open"]


def get(qid):
    return next((q for q in load()["questions"] if q["id"] == qid), None)


def due(now=None):
    """The question to ask now, or None. Urgent ones first, any time. Non-urgent ones only in the day's list: it
    starts when the last one started DAILY_SECS ago or more, and goes on for SESSION_SECS. A question skipped in
    this day's list isn't asked again until another day."""
    now = now or time.time()
    d = load()
    live = [q for q in d["questions"] if q["status"] == "open" and q["asked"] < MAX_ASKS]
    urgent = [q for q in live if q["urgent"] and now - q["asked_at"] > 600]
    if urgent:
        return urgent[0]
    started = d["daily_started"]
    if now - started >= DAILY_SECS:
        return live[0] if live else None            # a new day's list (start_daily marks it once it's really asked)
    if now - started <= SESSION_SECS:
        return next((q for q in live if q["asked_at"] < started), None)
    return None


def mark_asked(qid, now=None):
    """She has just asked it. Starts the day's list if this is its first question."""
    now = now or time.time()
    with _lock:
        d = load()
        q = next((x for x in d["questions"] if x["id"] == qid), None)
        if not q:
            return
        if not q["urgent"] and now - d["daily_started"] >= DAILY_SECS:
            d["daily_started"] = now
        q["asked"] += 1
        q["asked_at"] = now
        _save(d)


def answer(qid, status, said=""):
    """status: 'yes' / 'no' (answered) or 'dropped'."""
    with _lock:
        d = load()
        q = next((x for x in d["questions"] if x["id"] == qid), None)
        if q:
            q["status"], q["answer"] = status, str(said)[:200]
            _save(d)
        return q


def delete(qid):
    with _lock:
        d = load()
        before = len(d["questions"])
        d["questions"] = [q for q in d["questions"] if q["id"] != qid]
        _save(d)
        return before - len(d["questions"])
