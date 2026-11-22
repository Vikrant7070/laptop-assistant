"""R13: episodes, jobs and events live in <data>/nova.db; the old files are imported once."""
import json
import threading
import time

import pytest

from nova import db, log, store
from nova.actions import learner, workers


@pytest.fixture
def folder(tmp_path):
    """A fresh data folder as the current Store (and the connection to the old one closed)."""
    db.close()
    st = store.use(store.Store(tmp_path / "data"))
    st.root.mkdir(exist_ok=True)
    yield st
    db.close()


def test_old_files_are_imported_once_and_kept_as_backup(folder):
    eps = [{"id": "a", "ts": time.time() - 100, "goal": "open notepad", "steps": []},
           {"id": "b", "ts": time.time() - 50, "goal": "dark mode", "steps": []}]
    folder.episodes.write_text("\n".join(json.dumps(e) for e in eps[:1]) + "\n{torn line\n" + json.dumps(eps[1]) + "\n",
                               encoding="utf-8")
    folder.jobs.write_text(json.dumps([{"key": "k1", "id": 3, "started": 10, "worker": "research", "status": "done"}]),
                           encoding="utf-8")
    folder.task_log.write_text("10:00:00 SKILL MATCH open-notepad\n      a traceback line\n10:00:01 QUIT (asked)\n",
                               encoding="utf-8")
    assert [e["id"] for e in learner.load()] == ["a", "b"]               # the torn line is skipped, as before
    assert workers.load_jobs()[0]["key"] == "k1"
    assert not folder.episodes.exists() and (folder.root / "episodes.jsonl.imported").exists()
    assert not folder.jobs.exists() and (folder.root / "jobs.json.imported").exists()
    assert folder.task_log.exists()                                      # still the readable log
    assert [e["text"] for e in db.find_events(kind="log")] == ["10:00:00 SKILL MATCH open-notepad", "10:00:01 QUIT (asked)"]
    with folder.db() as con:
        assert con.execute("SELECT MAX(version) FROM migrations").fetchone()[0] == max(db.MIGRATIONS)
    db.close()
    assert db.migrate(folder) == max(db.MIGRATIONS) and len(learner.load()) == 2      # no second import


def test_a_new_folder_starts_empty(folder):
    assert learner.load() == [] and workers.load_jobs() == [] and db.find_events() == []


def test_jobs_keep_the_jobs_json_order_and_limit(folder):
    for i in range(5):
        workers._save_record({"key": f"k{i}", "id": i, "started": 100 + i, "worker": "opencode"})
    workers._save_record({"key": "k1", "id": 1, "started": 101, "worker": "opencode", "status": "done"})   # moves last
    assert [r["key"] for r in workers.load_jobs()] == ["k0", "k2", "k3", "k4", "k1"]
    db.save_job({"key": "k9", "started": 200}, keep=3)
    assert [r["key"] for r in workers.load_jobs()] == ["k4", "k1", "k9"]


def test_research_from_tuesday_is_a_query(folder):
    tue = time.mktime((2026, 11, 17, 0, 0, 0, 0, 0, -1))
    workers._save_record({"key": "a", "started": tue + 3600, "worker": "research", "status": "done"})
    workers._save_record({"key": "b", "started": tue + 7200, "worker": "opencode", "status": "done"})
    workers._save_record({"key": "c", "started": tue + 86400 + 60, "worker": "research", "status": "done"})
    assert [r["key"] for r in db.find_jobs(since=tue, until=tue + 86400, worker="research")] == ["a"]
    with pytest.raises(TypeError):
        db.find_jobs(colour="red")


def test_log_lines_and_errors_become_events(folder):
    log.note("SKILL NO MATCH 'play music'")
    try:
        raise KeyError("nope")
    except KeyError:
        log.reset()
        log.log_exception("test place")
    errors = db.find_events(kind="error")
    assert db.find_events(kind="log", like="NO MATCH")[0]["text"] == "SKILL NO MATCH 'play music'"
    assert errors[0]["data"]["where"] == "test place" and "KeyError" in errors[0]["data"]["detail"]


def test_events_are_pruned(folder, monkeypatch):
    monkeypatch.setattr(db, "KEEP_EVENTS", 10)
    monkeypatch.setattr(db, "PRUNE_EVERY", 5)
    for i in range(30):
        db.add_event("log", f"e{i}")
    texts = [e["text"] for e in db.find_events(kind="log")]
    assert len(texts) <= 15 and texts[-1] == "e29"


def test_two_threads_writing_at_once(folder):
    def write(tag):
        for i in range(100):
            db.add_event("log", f"{tag}{i}")
            workers._save_record({"key": f"{tag}{i}", "started": i})
    ts = [threading.Thread(target=write, args=(t,)) for t in "xy"]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert len(db.find_events(kind="log", limit=10_000)) == 200
    assert len(workers.load_jobs()) == min(200, workers.KEEP_JOBS)


def test_each_data_folder_has_its_own_db(folder, tmp_path):
    db.add_event("log", "in the first folder")
    other = store.use(store.Store(tmp_path / "other"))
    assert db.find_events() == []
    db.add_event("log", "in the second folder")
    assert (other.root / "nova.db").exists()
    store.use(folder)
    assert [e["text"] for e in db.find_events()] == ["in the first folder"]


def test_the_logger_never_raises_when_the_db_cannot_open(folder, monkeypatch):
    def boom(st=None):
        raise OSError("disk gone")
    monkeypatch.setattr(db, "connect", boom)
    assert db.add_event("log", "x") is False
    assert log.note("still written to the text log")
