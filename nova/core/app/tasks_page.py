"""The Tasks page (manager plan 4-pre d): one list for Nova's own laptop tasks (the saved task cards) and the worker
jobs (coding, research), newest first - what ran, who did it, how it went and why it ended.

    GET  /tasks                      the page (static/tasks.html)
    GET  /api/tasks?status=&kind=    the list: {"top": [...tasks to carry on...], "rows": [...]}
    GET  /api/tasks/<id>             one row in full: "t:<card id>" (a laptop task) or "j:<job key>" (a worker job)
    GET  /api/tasks/<id>/changes     a coding job's files + short diff (read-only, workers.changes)
    GET  /api/tasks/<id>/report      a research job's report (read-only, workers.report)

Nothing here changes anything, except that the page's Drop it / Continue use resume's /api/task/drop and
/api/task/pick. A commit or a retry is never run from the page: it opens the chat with the words filled in, where her
usual Allow / No asks first. Text from jobs goes through the card's cleaning (task_state.clean), so nothing
secret-looking is shown; a coding job's diff is shown only when asked for, as the "what changed?" tool shows it.
The iPhone app's read-only list (/m/tasks) is `rows(..., limit=30)`.

4f, one record per task: a laptop task's detail reads its card and the episode kept on the same row (db.py) - the
workers it started (their job records, live), rounds, cost, what the user chose and the app's notes. Nothing here
keeps a copy.
"""
import time

from flask import jsonify, request, send_from_directory

from nova import db, task_state
from nova.actions import jobrounds, undo, workers
from nova.core.app import api, resume, session
from nova.log import log_exception

LIMIT = 80

# A job record's status -> the page's words (the same ones as the cards).
JOB_STATUS = {"queued": "running", "running": "running", "checking": "running", "needs-user": "waiting",
              "done": "done", "failed": "failed", "stopped": "stopped",
              "limit": "stopped"}          # 4c-3: a limit stopped it before its send-back round
# The page's filter -> the statuses it shows.
FILTERS = {"running": ("running",), "waiting": ("waiting",), "stopped": ("stopped", "interrupted", "stuck", "dropped"),
           "done": ("done",), "failed": ("failed",)}
KINDS = {"laptop": ("laptop",), "workers": ("coding", "research")}
# A job record's check verdict (4c-2, nova/actions/jobchecks.py) -> the row's ✓ / ✗ (None: not checked / no checks).
JOB_VERDICT = {"passed": True, "failed": False}


def _clean(text, n=200):
    return task_state.clean(text, n) if text else ""


def _cards():
    """Saved cards (newest 200), with the one in memory instead of its saved copy (it may be newer)."""
    try:
        cards = [c for c in (task_state.upgrade(c) for c in db.load_tasks(limit=200)) if c is not None]
    except Exception:
        log_exception("tasks page: reading task cards")
        cards = []
    cur = task_state.of(session.state.get("task"))
    if cur is not None:
        cards = [cur] + [c for c in cards if c["id"] != cur["id"]]
    return cards


def task_row(card):
    done = (sum(st["state"] == "done" for st in card["plan"]) if card["plan"]     # plan steps done, when there is a plan
            else sum(r["state"] == "done" for r in card["runs"]))
    return {"id": "t:" + card["id"], "kind": "laptop", "when": card["started"], "goal": card["goal"],
            "who": card.get("brain") or "Nova", "status": card["status"], "why": card["why"],
            "steps_done": done, "steps_total": len(card["plan"]) or None,
            "secs": round(max(0.0, card["updated"] - card["started"]), 1), "verdict": card.get("verdict"),
            "can_continue": task_state.resumable(card)}


def job_row(rec):
    worker = rec.get("worker") or ""
    started = float(rec.get("started") or 0)
    finished = rec.get("finished")
    return {"id": "j:" + str(rec.get("key")), "kind": "research" if worker == "research" else "coding",
            "when": started, "goal": _clean(rec.get("brief"), 200), "job": rec.get("job"),
            "who": workers.NAMES.get(worker, worker) + (f" ({rec['model']})" if rec.get("model") else ""),
            "status": JOB_STATUS.get(rec.get("status"), rec.get("status") or ""), "why": _clean(("limit reached - " + rec["limit"]) if rec.get("limit") else rec.get("note"), 160),
            "steps_done": None, "steps_total": None, "files": len(rec.get("files") or []),
            "sources": len(rec.get("sources") or []), "cost": rec.get("cost") or 0,
            "secs": round(float(finished) - started, 1) if finished else None,
            "verdict": JOB_VERDICT.get(rec.get("verdict") or ""), "not_checked": rec.get("verdict") == "not checked",
            "can_continue": False, "type": rec.get("job_type") or "", "chain": rec.get("chain_step") or "",
            "maybe_stuck": _maybe_stuck(rec)}


def _maybe_stuck(rec):
    """4d: a running worker that has gone quiet (jobrounds.Quiet marks the live job)."""
    if rec.get("status") not in ("running", "checking"):
        return ""
    from nova.actions import coworker
    job = coworker.find(str(rec.get("job") or ""))
    return _clean(job.get("maybe_stuck"), 120) if job and job.get("status") == "working" else ""


def rows(status=None, kind=None, limit=LIMIT):
    """The list, newest first, filtered by FILTERS / KINDS words (unknown words = no filter)."""
    out = [task_row(c) for c in _cards() if c["runs"] or c["plan"] or c.get("schedule")]  # a chat with no steps isn't a task; a scheduled run is (4g)
    out += [job_row(r) for r in workers.load_jobs()]
    if status in FILTERS:
        out = [r for r in out if r["status"] in FILTERS[status]]
    if kind in KINDS:
        out = [r for r in out if r["kind"] in KINDS[kind]]
    out.sort(key=lambda r: -r["when"])
    return out[:limit]


def top():
    """Tasks that can be carried on (a restart or a Stop cut them short), for the top of the page."""
    try:
        return [task_row(c) for c in resume._candidates()]
    except Exception:
        log_exception("tasks page: tasks to carry on")
        return []


def _job(key):
    return next((r for r in workers.load_jobs() if str(r.get("key")) == key), None)


def detail(rid):
    """One row in full, or None."""
    if rid.startswith("t:"):
        card = resume._find(rid[2:])
        if card is None:
            return None
        return {**task_row(card), "card": task_state.strip(card, runs=task_state.MAX_RUNS),
                "verdict_source": card.get("verdict_source", ""), "checks": card.get("checks") or [],
                "undo": [undo.describe(r) for r in undo.records(card["id"])][-10:], "record": record(card)}
    if rid.startswith("j:"):
        rec = _job(rid[2:])
        if rec is None:
            return None
        row = job_row(rec)
        n, worker, status = rec.get("job"), rec.get("worker"), rec.get("status")
        actions = []
        if worker in workers.CODERS and rec.get("files") and not rec.get("committed"):
            actions += [{"do": "changes", "label": "What changed?"},
                        {"do": "chat", "label": "Commit…", "say": f"commit coding job #{n}"}]
        if worker == "research" and status == "done":
            actions.append({"do": "report", "label": "Report"})
        if status in ("running", "checking", "queued"):
            actions.append({"do": "chat", "label": "■ Stop…", "say": f"stop job #{n}"})
        if jobrounds.can_continue(rec):
            actions.append({"do": "chat", "label": "▶ Continue…", "say": f"try job #{n} again"})
        elif status in ("failed", "stopped", "needs-user"):
            actions.append({"do": "chat", "label": "Try again…",
                            "say": f"try research #{n} again" if worker == "research" else f"try job #{n} with Claude Code"})
        return {**row, "brief": _clean(rec.get("brief"), 2000), "folder": rec.get("folder") or "",
                "result": _clean(rec.get("result"), 2000), "files_list": [str(f) for f in rec.get("files") or []][:50],
                "source_list": [_clean(s if isinstance(s, str) else s.get("url") or s.get("title"), 200)
                                for s in rec.get("sources") or []][:10],
                "committed": bool(rec.get("committed")), "finished": rec.get("finished"), "actions": actions,
                "checks": [{"name": _clean(c.get("name"), 20), "state": c.get("state"), "why": _clean(c.get("why"), 200)}
                           for c in rec.get("checks") or [] if isinstance(c, dict)][:10],
                "left": [_clean(x, 200) for x in rec.get("left") or []][:10],
                "sent_back": int(rec.get("sent_back") or 0), "guard": [_clean(x, 200) for x in rec.get("guard") or []][:10]}
    return None


def record(card):
    """4f: the rest of the task's one record - the episode on its row and the jobs it started (read now)."""
    try:
        ep = db.task_episode(card["id"]) or {}
    except Exception:
        log_exception("tasks page: a task's episode")
        ep = {}
    ids = {j.get("id") for j in ep.get("jobs") or []}
    started = float(card.get("started") or 0)
    jobs = [r for r in workers.load_jobs() if ids and r.get("job") in ids and float(r.get("started") or 0) >= started - 1]
    return {"app": ep.get("app") or card.get("notes_app") or card.get("app") or "",
            "blocked": list(ep.get("blocked") or []), "brain": ep.get("brain") or "",
            "workers": [{"id": job_row(r)["id"], "who": job_row(r)["who"], "status": job_row(r)["status"]} for r in jobs],
            "rounds": 1 + bool(card.get("replanned")) + sum(int(r.get("sent_back") or 0) for r in jobs),
            "cost": round(sum(float(r.get("cost") or 0) for r in jobs), 2),
            "learned": bool(ep.get("learned")), "worked": bool(ep) and _worked(ep)}


def _worked(ep):
    from nova.actions import learner
    return learner.worked(ep)


# ------------------------------------------------------------------ routes

@api.app.get("/tasks")
def tasks_page():
    return send_from_directory(api.app.static_folder, "tasks.html")


@api.app.get("/api/tasks")
def tasks_list():
    return jsonify(top=top(), rows=rows(request.args.get("status"), request.args.get("kind")), now=time.time())


@api.app.get("/api/tasks/<path:rid>/changes")
def tasks_changes(rid):
    rec = _job(rid[2:]) if rid.startswith("j:") else None
    if rec is None or rec.get("worker") not in workers.CODERS:
        return jsonify(error="No such coding job."), 404
    try:
        return jsonify(text=workers.changes(rec=rec))
    except Exception:
        log_exception("tasks page: a job's changes")
        return jsonify(error="I couldn't read the changes (is git there, and the folder?)."), 500


@api.app.get("/api/tasks/<path:rid>/report")
def tasks_report(rid):
    rec = _job(rid[2:]) if rid.startswith("j:") else None
    if rec is None or rec.get("worker") != "research":
        return jsonify(error="No such research job."), 404
    try:
        return jsonify(text=workers.report(rec=rec))
    except Exception:
        log_exception("tasks page: a research report")
        return jsonify(error="I couldn't read the report."), 500


@api.app.get("/api/tasks/<path:rid>")
def tasks_one(rid):
    d = detail(rid)
    return jsonify(d) if d is not None else (jsonify(error="That task isn't there any more."), 404)
