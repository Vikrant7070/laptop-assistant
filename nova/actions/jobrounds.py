"""Rounds of a coding job (manager plan 4c-3): the brief guard, one send-back with the failing check, limits.

    check_round(...)       one round's checks (jobchecks.coding); files changed outside the job's folder are undone
                           and the job stops; a failed check -> back to the same worker once, unless a limit says no
                           (rounds, time for the whole job, money for Claude Code - jobchecks.limit_hit)
    send_back_words(rec)   what the report says about it
    can_continue(rec)      a job a limit stopped before its send-back: "try job #n again" continues it (workers.retry)

workers.start runs the rounds; this module is the deciding, kept apart so workers.py stays readable.
"""
from nova.actions import jobchecks


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
    job["step"] = "checking its work (files, tests)"
    checks = jobchecks.coding(root, asked, check["files"], workers._guard_env(), coworker.checkpoint)
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
