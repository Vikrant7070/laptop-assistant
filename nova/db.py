"""Nova's growing data in one SQLite file, <data>/nova.db (roadmap R13).

The episode log, the job history and the event log used to be JSON files rewritten whole on every change
(episodes.jsonl, jobs.json) or a text file other code parsed back (task_log.txt). Now they are tables:

    episodes(seq, id, ts, goal, data)      learner.load() / record() - data is the episode's JSON
    jobs(seq, key, id, started, kind, worker, status, data)        workers.load_jobs() / _save_record()
    events(id, ts, kind, text, data)       log.note() / log.event() / log_exception(); task_log.txt is the
                                           readable copy, nothing reads it back
    tasks(seq, id, started, updated, status, goal, data)    task cards (nova/task_state.py, saved by app.resume)

so "research jobs from Tuesday" is a query (`find_jobs`, `find_events`). The modules keep their old functions.

    with db.connect() as con:          # a connection to the current Store's nova.db, in a transaction
        con.execute(...)

There is one open connection (WAL mode), shared by all threads under a lock. Opening a data folder applies the migrations in MIGRATIONS that it hasn't had yet, then imports the old files once and renames
them `<name>.imported` (they stay as a backup). A new table or column = a new numbered migration, never an edit
of an old one.
"""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from nova import store

MIGRATIONS = {
    1: [
        "CREATE TABLE episodes (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, ts REAL, goal TEXT, data TEXT NOT NULL)",
        "CREATE INDEX episodes_ts ON episodes(ts)",
        "CREATE TABLE jobs (seq INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL, id TEXT, started REAL, "
        "kind TEXT, worker TEXT, status TEXT, data TEXT NOT NULL)",
        "CREATE INDEX jobs_started ON jobs(started)",
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, text TEXT, data TEXT)",
        "CREATE INDEX events_kind_ts ON events(kind, ts)",
    ],
    2: [
        "CREATE TABLE tasks (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, started REAL, updated REAL, "
        "status TEXT, goal TEXT, data TEXT NOT NULL)",
        "CREATE INDEX tasks_updated ON tasks(updated)",
    ],
}
KEEP_EVENTS = 50_000          # the oldest events go once there are more (checked every PRUNE_EVERY inserts)
PRUNE_EVERY = 500

# One open connection, to the current data folder's nova.db, shared by every thread under _lock. Opening and
# closing per use cost ~7 ms a write: the last connection to close a WAL database checkpoints it every time.
# A different folder (tests, NOVA_DATA) closes the old connection first, so only one file is ever held open.
_lock = threading.RLock()
_open_con = {"path": None, "con": None}
_inserts = {"n": 0}


def _open(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")        # in WAL mode: safe against crashes, much faster
    return con


def close():
    """Close the open connection (quit, or before the data folder's files are moved)."""
    with _lock:
        if _open_con["con"] is not None:
            _open_con["con"].close()
        _open_con.update(path=None, con=None)


@contextmanager
def connect(st=None):
    """The connection to nova.db in the current (or given) Store's folder, as one transaction (holds _lock)."""
    st = st or store.current()
    path = Path(st.db_file)
    with _lock:
        if _open_con["path"] != str(path) or not path.exists():
            close()
            migrate(st)
            _open_con.update(path=str(path), con=_open(path))
        con = _open_con["con"]
        with con:
            yield con


def version(con):
    con.execute("CREATE TABLE IF NOT EXISTS migrations (version INTEGER PRIMARY KEY, at REAL)")
    return con.execute("SELECT COALESCE(MAX(version), 0) FROM migrations").fetchone()[0]


def migrate(st=None):
    """Bring nova.db up to the newest migration and import the old files. -> the version it is at."""
    st = st or store.current()
    path = Path(st.db_file)
    with _lock:
        con = _open(path)
        imported = []
        try:
            with con:
                have = version(con)
                for v in sorted(n for n in MIGRATIONS if n > have):
                    for sql in MIGRATIONS[v]:
                        con.execute(sql)
                    con.execute("INSERT INTO migrations VALUES (?, ?)", (v, time.time()))
                    if v == 1:
                        imported = _import_old_files(con, st)
            now = version(con)
        finally:
            con.close()
        for old in imported:                 # only once the import is committed
            try:
                old.replace(old.with_name(old.name + ".imported"))
            except OSError:
                pass        # intentional: the rows are in; a file we can't rename is imported again only if the db is new
        return now


def _import_old_files(con, st):
    """Migration 1: episodes.jsonl, jobs.json and task_log.txt's lines -> rows. -> files to rename afterwards."""
    done = []
    eps_file = Path(st.episodes)
    if eps_file.exists():
        for line in eps_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ep = json.loads(line)
            except ValueError:
                continue        # intentional: a torn line from a crash mid-write was skipped by the old reader too
            if isinstance(ep, dict):
                _put_episode(con, ep)
        done.append(eps_file)
    jobs_file = Path(st.jobs)
    if jobs_file.exists():
        try:
            recs = json.loads(jobs_file.read_text(encoding="utf-8"))
        except ValueError:
            recs = []
        for rec in recs if isinstance(recs, list) else []:
            if isinstance(rec, dict) and rec.get("key"):
                put_job(con, rec)
        done.append(jobs_file)
    log_file = Path(st.task_log)
    if log_file.exists():                # kept as it is: it stays the readable log
        ts = log_file.stat().st_mtime
        rows = [(ts, "log", line, None) for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip() and not line.startswith(" ")]
        con.executemany("INSERT INTO events (ts, kind, text, data) VALUES (?, ?, ?, ?)", rows)
    return done


# ------------------------------------------------------------------ episodes

def _put_episode(con, ep):
    con.execute("INSERT OR REPLACE INTO episodes (id, ts, goal, data) VALUES (?, ?, ?, ?)",
                (ep.get("id"), float(ep.get("ts") or 0), str(ep.get("goal") or "")[:500], json.dumps(ep, ensure_ascii=False)))


def load_episodes(since=0.0):
    """Episodes with ts >= since, in the order they were written."""
    with connect() as con:
        rows = con.execute("SELECT data FROM episodes WHERE ts >= ? ORDER BY seq", (since,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def replace_episodes(eps):
    """The whole episode log becomes `eps` (in this order), in one transaction."""
    with connect() as con:
        con.execute("DELETE FROM episodes")
        for ep in eps:
            _put_episode(con, ep)


# ------------------------------------------------------------------ jobs

def put_job(con, rec):
    """Add or replace a job record by its key; a replaced one moves to the end (newest), as in jobs.json."""
    con.execute("DELETE FROM jobs WHERE key = ?", (rec["key"],))
    con.execute("INSERT INTO jobs (key, id, started, kind, worker, status, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rec["key"], str(rec.get("id") or ""), float(rec.get("started") or 0), str(rec.get("kind") or ""),
                 str(rec.get("worker") or ""), str(rec.get("status") or ""), json.dumps(rec, ensure_ascii=False)))


def load_jobs():
    with connect() as con:
        rows = con.execute("SELECT data FROM jobs ORDER BY seq").fetchall()
    return [json.loads(r[0]) for r in rows]


def save_job(rec, keep):
    """Add or replace one record and keep only the newest `keep`."""
    with connect() as con:
        put_job(con, rec)
        con.execute("DELETE FROM jobs WHERE seq NOT IN (SELECT seq FROM jobs ORDER BY seq DESC LIMIT ?)", (keep,))


def find_jobs(since=0.0, until=None, **match):
    """Job records started in [since, until), e.g. find_jobs(since=tuesday, until=wednesday, worker="research")."""
    sql, args = "SELECT data FROM jobs WHERE started >= ? AND started < ?", [since, until if until is not None else 1e18]
    for col in ("kind", "worker", "status"):
        if col in match:
            sql += f" AND {col} = ?"
            args.append(match.pop(col))
    if match:
        raise TypeError(f"find_jobs: unknown filter {sorted(match)}")
    with connect() as con:
        return [json.loads(r[0]) for r in con.execute(sql + " ORDER BY seq", args).fetchall()]


# ------------------------------------------------------------------ task cards

def save_task(card):
    """Add or replace one task card (by its id)."""
    with connect() as con:
        con.execute("INSERT INTO tasks (id, started, updated, status, goal, data) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET updated = excluded.updated, status = excluded.status, "
                    "goal = excluded.goal, data = excluded.data",
                    (str(card["id"]), float(card.get("started") or 0), float(card.get("updated") or 0),
                     str(card.get("status") or ""), str(card.get("goal") or "")[:300], json.dumps(card, ensure_ascii=False)))


def load_tasks(statuses=None, since=0.0, limit=50):
    """The newest `limit` task cards changed since `since` (newest first), only these statuses if given."""
    sql, args = "SELECT data FROM tasks WHERE updated >= ?", [since]
    if statuses:
        sql += f" AND status IN ({', '.join('?' * len(statuses))})"
        args += list(statuses)
    with connect() as con:
        rows = con.execute(sql + " ORDER BY updated DESC LIMIT ?", [*args, limit]).fetchall()
    return [json.loads(r[0]) for r in rows]


def prune_tasks(before):
    """Forget task cards last changed before `before`. -> how many went."""
    with connect() as con:
        return con.execute("DELETE FROM tasks WHERE updated < ?", (before,)).rowcount


# ------------------------------------------------------------------ events

def add_event(kind, text="", data=None, ts=None):
    """One event row. Never raises: the log is the last thing allowed to fail."""
    try:
        with connect() as con:
            con.execute("INSERT INTO events (ts, kind, text, data) VALUES (?, ?, ?, ?)",
                        (ts or time.time(), kind, text, json.dumps(data, ensure_ascii=False, default=str) if data else None))
            _inserts["n"] += 1
            if _inserts["n"] % PRUNE_EVERY == 0:
                con.execute("DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ?", (KEEP_EVENTS,))
        return True
    except Exception:
        return False        # intentional: called from the logger itself; a locked or full disk must not raise


def find_events(kind=None, since=0.0, like=None, limit=1000):
    """The newest `limit` events (oldest first) of `kind` since `since`, whose text contains `like` if given."""
    sql, args = "SELECT ts, kind, text, data FROM events WHERE ts >= ?", [since]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if like:
        sql += " AND text LIKE ?"
        args.append(f"%{like}%")
    with connect() as con:
        rows = con.execute(sql + " ORDER BY id DESC LIMIT ?", [*args, limit]).fetchall()
    return [{"ts": ts, "kind": k, "text": t, "data": json.loads(d) if d else None} for ts, k, t, d in reversed(rows)]
