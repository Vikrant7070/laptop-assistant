"""Rounds of a coding job (manager plan 4c-3): the brief guard, one send-back with the failing check, limits.

    check_round(...)       one round's checks (jobchecks.coding); files changed outside the job's folder are undone
                           and the job stops; a failed check -> back to the same worker once, unless a limit says no
                           (rounds, time for the whole job, money for Claude Code - jobchecks.limit_hit)
    send_back_words(rec)   what the report says about it
    can_continue(rec)      a job a limit stopped before its send-back: "try job #n again" continues it (workers.retry)
    Quiet(folder)          4d: a run with no output and no file change for QUIET_MIN minutes is "maybe stuck" (the
                           chat is told; /tasks shows it with Stop…); it clears when the worker moves again
    research_round(...)    a research job's checks; a failed one goes back once ("read more pages, cite 2"), same limits
    usage_limited / usage_stop / hand_over
                           a worker at its usage limit (OpenCode 429 after its fallback models, Claude Code's usage
                           limit): the job stops as `limit` and the app asks "Carry on with <other> (cost)" / "Wait";
                           on the user's go the other worker gets the same brief + the files already changed

workers.start runs the rounds; this module is the deciding, kept apart so workers.py stays readable.
"""
import os
import re
import time

from nova.actions import jobchecks
from nova.log import log_exception


def check_round(w, rec, job, root, asked, check, out, conf, sent_back, brief):
    """A round's checks (4c-2) and whether it goes back to the worker (4c-3). -> (checks, send it back?).
    The brief guard: files changed outside the job's folder are undone and the job stops (raises)."""
    from nova.actions import coworker, workers
    if not check["files"]:
        return [], False
    bad = jobchecks.outside(root, asked, check["files"])
    if bad:
        undone = jobchecks.undo(root, bad, workers.store.current().job_logs / f"{rec['key']}-undone")
        kept = [f for f in check["files"] if f not in bad]
        rec.update(files=kept, guard=undone, verdict="failed", checks=[jobchecks.files_inside(root, asked, check["files"])],
                   left=["files changed outside the job's folder (undone): " + ", ".join(bad[:5])])
        job["files"] = kept
        raise RuntimeError(f"{workers.NAMES[w]} changed files outside the job's folder ({', '.join(bad[:4])}"
                           f"{' …' if len(bad) > 4 else ''}). I undid those changes and stopped the job."
                           + (f" Its changes inside the folder are kept, not committed: {', '.join(kept[:4])}."
                              if kept else ""))
    job["step"] = "checking its work (files, tests, lint, build, the page)"
    checks = jobchecks.coding(root, asked, check["files"], workers._guard_env(), coworker.checkpoint,
                                rec.get("job_type") or "")
    rec.update(checks=checks, verdict=jobchecks.verdict(checks), left=jobchecks.left(checks, out["text"]), limit="")
    if rec["verdict"] != "failed" or out.get("denied"):
        return checks, False
    why = jobchecks.limit_hit(conf.get("manager") or {}, w, rec["started"], sent_back, conf, workers.spent_today())
    if why.startswith("rounds"):
        return checks, False                     # already sent back (or turned off): the user is told plainly
    if why:
        rec.update(limit=why, send_back=jobchecks.send_back_brief(brief, checks)[:6000])
        return checks, False
    failed = "; ".join(f"{c['name']}: {c['why']}" for c in checks if c["state"] == "failed")
    coworker._note(f"🔁 Job #{job['id']}: a check failed ({failed[:120]}) - sending it back to {workers.NAMES[w]} once.")
    job["step"] = "sent back to fix a failing check"
    return checks, True


def send_back_words(rec, n):
    """What the report says about the send-back (4c-3)."""
    if rec.get("limit"):
        return (f" I didn't send it back to fix that - a limit was reached ({rec['limit']}). Say 'try job #{n} again'"
                " to continue.")
    if rec.get("sent_back") and rec.get("verdict") == "failed":
        return (" I sent it back once with the failing check and it still fails - over to you: look at it, or try it"
                " with another worker.")
    if rec.get("sent_back"):
        return " (A check failed at first; I sent it back once and it fixed it.)"
    return ""


def can_continue(rec):
    """A coding job a limit stopped before its send-back round, not yet continued."""
    return (rec.get("status") == "limit" and bool(rec.get("send_back")) and not rec.get("continued")
            and rec.get("worker") in ("opencode", "claude"))


# ------------------------------------------------------------------ research: one send-back with the failing check

def research_round(job, rec, wanted, conf, rerun):
    """A finished research round's checks (jobchecks.research). A failed one (too few cited sources, no report) goes
    back once - rerun(focus, max_sources) reads more pages and writes the report again, in the same job - under the
    same send_backs / job_min limits as a coding job. A rerun that breaks keeps the first round's report. -> checks"""
    from nova.actions import coworker
    checks = jobchecks.research(job, wanted)
    if jobchecks.verdict(checks) != "failed":
        return checks
    why = jobchecks.limit_hit(conf.get("manager") or {}, "research", rec["started"], int(rec.get("sent_back") or 0))
    if why:
        if not why.startswith("rounds"):
            rec["limit"] = why
        return checks
    failed = [f"{c['name']}: {c['why']}" for c in checks if c["state"] == "failed"]
    coworker._note(f"🔁 Job #{job['id']}: a check failed ({'; '.join(failed)[:120]}) - sending the research back once "
                   "to read more pages.")
    job["step"] = "sent back to fix a failing check"
    rec["sent_back"] = int(rec.get("sent_back") or 0) + 1
    need = max(1, min(jobchecks.MIN_SOURCES, int(wanted or jobchecks.MIN_SOURCES)))
    focus = (f"{rec.get('focus') or ''}\nNova checked the last report and this check failed: {'; '.join(failed)}. "
             f"Read more pages, and cite at least {need} different sources with their [n] marks.").strip()
    kept = {k: job.get(k) for k in ("path", "headline", "sources", "cited", "result")}
    try:
        rerun(focus, min(5, int(wanted or 3) + 2))
    except coworker.Stopped:
        raise
    except Exception as e:
        log_exception("jobrounds: the research send-back", e)
        job.update(kept)
        return checks
    return jobchecks.research(job, wanted)


def research_words(rec, n):
    """What a research report says about its send-back."""
    if rec.get("limit"):
        return f" I didn't send it back to fix that - a limit was reached ({rec['limit']}). Say 'try job #{n} again'."
    if rec.get("sent_back") and rec.get("verdict") == "failed":
        return (" I sent it back once to read more pages and it still fails - over to you: look at the report, or "
                "ask me to research it again another way.")
    if rec.get("sent_back"):
        return " (A check failed at first; I sent it back once to read more pages and it fixed it.)"
    return ""


# ------------------------------------------------------------------ a worker at its usage limit: ask, never switch

USAGE_LIMIT = re.compile(r"usage limit|limit reached|hit your limit|quota|rate.?limit|\b429\b|too many requests|"
                         r"out of (?:extra )?usage|credit balance is too low", re.I)


def usage_limited(w, out):
    """OpenCode: every model it may use (the big-pickle fallback too) said 429 / free limit. Claude Code: its error
    says usage limit / quota (only its error - a summary that talks about rate limits is not one)."""
    if out.get("limit"):
        return True
    return w == "claude" and bool(USAGE_LIMIT.search(str(out.get("error") or "")))


def usage_stop(w, rec, job, check):
    """The job stops as `limit`; the app asks "Carry on with <other> (cost)" / "Wait" (managing.limit_card, from
    job["handoff"]). Nothing switches by itself. -> the report line."""
    from nova.actions import manager, workers
    other = manager.other_worker(w)
    files = list(check["files"])
    rec.update(status="limit", limit=f"usage: {workers.NAMES[w]} hit its usage limit", files=files,
               note=check["note"], handoff=other)
    job.update(files=files, handoff={"key": rec["key"], "job": job["id"], "from": w, "to": other,
                                     "label": manager.worker_label(other)})
    did = (f" after changing {len(files)} file{'s' if len(files) != 1 else ''}: {', '.join(files[:4])}"
           + (" …" if len(files) > 4 else "") + " (kept, not committed)") if files else " before changing anything"
    return f"{workers.NAMES[w]} hit its usage limit, so job #{job['id']} stopped{did}. I haven't switched workers."


def can_hand_over(rec):
    return rec.get("status") == "limit" and bool(rec.get("handoff")) and not rec.get("handed_over")


def hand_over(rec):
    """The user said carry on: the other worker gets the same brief plus the files already changed, in the same
    folder as it is now (those files count as the new job's own). -> the sentence from workers.start."""
    from nova.actions import workers
    if not can_hand_over(rec):
        return f"Job #{rec.get('job')} isn't waiting to be handed over any more."
    files = list(rec.get("files") or [])
    brief = str(rec["brief"]) + (
        f"\n\nAnother worker started this job and stopped at its usage limit. It already changed these files (they "
        f"are in the folder as it left them, not committed): {', '.join(files[:20])}. Look at them first and carry on "
        "from there - don't start over." if files else
        "\n\nAnother worker started this job and stopped at its usage limit before changing anything.")
    meta = {k: rec[k] for k in ("job_type", "manager_job") if rec.get(k)}
    said = workers.start(brief, rec.get("asked") or rec["folder"], worker=rec["handoff"],
                         meta={**meta, "continues": rec["job"], "carry_files": files})
    if str(said).startswith(("Started", "Queued")):
        rec["handed_over"] = True
        workers._save_record(rec)
    return said


# ------------------------------------------------------------------ 4d: a worker that has gone quiet

QUIET_MIN = 10               # no output and no file change for this long -> "maybe stuck"
QUIET_EVERY = 30             # seconds between looks (a git status each time)


class Quiet:
    """Watches one worker run: run_once calls heard() for every output line and check(job) while it waits."""

    def __init__(self, folder):
        self.folder, self.last, self.looked, self.files = folder, time.time(), 0.0, None

    def heard(self):
        self.last = time.time()

    def _files(self):
        from nova.actions import workers
        names = sorted(workers._changed(self.folder))
        stamps = []
        for n in names[:200]:
            try:
                stamps.append(os.path.getmtime(os.path.join(self.folder, n)))
            except OSError:
                stamps.append(0.0)
        return names, stamps

    def check(self, job, now=None):
        now = now or time.time()
        if now - self.looked < QUIET_EVERY:
            return
        self.looked = now
        if now - self.last < QUIET_MIN * 60:
            job.pop("maybe_stuck", None)
            return
        try:
            files = self._files()
        except Exception as e:
            log_exception("jobrounds: looking at a quiet worker's files", e)
            return
        if files != self.files:                 # the files moved on: it's working, just quietly
            first, self.files = self.files is None, files
            if not first:
                self.last = now
                job.pop("maybe_stuck", None)
                return
        if not job.get("maybe_stuck"):
            from nova.actions import coworker
            mins = max(1, round((now - self.last) / 60))
            job["maybe_stuck"] = f"no output and no file change for {mins} min"
            coworker._note(f"⚠️ Job #{job['id']} may be stuck: {job['maybe_stuck']}. Say 'stop job #{job['id']}' "
                           "to stop it, or let it carry on.")
