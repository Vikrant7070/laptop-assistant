"""The manager in the app (plan Phase 4, step 4a): a request that is a job for a worker becomes questions (one card at
a time), a brief, and - after the user's go - a worker job. The rules and the template are nova/actions/manager.py.

    route(text)                 body.answer, after the task starts: a clear hand-off ("make a website for my Nova
                                project") skips the brain entirely, like an exact skill match. -> a reply, or None
    from_tool(request, kind)    the brain's hand_off_job tool, for requests the word rules can't tell. The card it
                                opens ends the brain's turn (conversation: session.state["hand_off"])

Each question is a card (app.asking, owner "manager"). Answers with a fact key are saved as unconfirmed facts. Then
the brief card: "Go (OpenCode, free)" / "Go (Claude Code, paid, up to $1.00)" · "Change it" ("what should I
change?" adds a line to the brief, and the brief comes back). The go-ahead setting (⚙ Workers → Manager:
always / paid / never) decides whether the brief waits for Go; the default is always. A dropped card ends the job.
"""
from nova import protocol, task_state
from nova.actions import manager, workers
from nova.log import log_exception

def mode():
    """The go-ahead setting (⚙ Workers → Manager, workers.json): always (the default) / paid / never."""
    return workers.config().get("manager", {}).get("go_ahead", "always")


def _task_card():
    return task_state.of(session.state.get("task"))


def route(text):
    """A clear hand-off by the word rules: start the manager without a brain turn. -> a reply or None."""
    words = protocol.unspoken(text).strip()
    kind = manager.clear_hand_off(words)
    if not kind:
        return None
    conversation.task_log(f"MANAGER: hand-off by word rules ({kind}): {words[:120]}")
    return start(words, kind)


def from_tool(request, kind=""):
    """hand_off_job (the brain): the same flow. The card ends the brain's turn. -> words for the brain."""
    request = str(request or "").strip() or (session.state.get("task") or {}).get("goal", "")
    if kind == "laptop" or not request:
        return "Not handed off: that's a laptop task - do it yourself with your tools."
    out = start(request, kind or manager.triage(request) or "coding")
    session.state["hand_off"] = out
    return "Handed to the manager: the user now has a question card to answer. Don't call any tool; stop here."


def start(request, kind):
    job = manager.plan(request, kind)
    card = _task_card()
    job["task"] = card["id"] if card else ""
    session.state["handoff"] = job
    session.think(f"🧑‍💼 That's a job for a worker ({manager.WORKER_LABEL.get(job['worker'], job['worker'])}) - "
                  f"{len(job['queue'])} question{'s' if len(job['queue']) != 1 else ''} first.", cloud_too=False)
    return next_step(job)


def next_step(job):
    """The next question card, or the brief (with its Go card, if the setting asks), or the worker started."""
    if job["queue"]:
        q = job["queue"].pop(0)
        job["asked"] += 1
        card = asking.make("manager", [asking.question(**q)], task=job["task"], data={"job": job["id"], "key": q["key"]})
        return asking.ask(card, "Asked about the job")
    text = manager.brief(job)
    job["brief"] = text
    if not manager.needs_go(job, mode()):
        return launch(job)
    card = asking.make("manager", [asking.question(
        "go", "Here's the brief. Start?", "yesno", choices=[("go", manager.go_label(job)), ("change", "Change it")],
        why="Nothing starts until you say go.")], task=job["task"], data={"job": job["id"], "key": "go"})
    out = asking.ask(card, "Wrote the brief")
    out["brief"] = text
    if session.state.get("remote"):            # the phone has no folded brief: it goes in the message
        out["reply"] = text + "\n\n" + out["reply"]
    return out


def _answered(qcard, answers):
    job = session.state.get("handoff")
    if not job or job["id"] != qcard["data"].get("job"):
        return {"reply": "That job isn't waiting any more.", "say": "That job isn't waiting any more.",
                "activity": ["Job gone"], "by_code": True}
    key = qcard["data"]["key"]
    a = answers[key]
    if key == "go":
        if a["value"] == "go":
            return launch(job)
        job["queue"].insert(0, {"key": "change", "text": "What should I change in the brief?", "kind": "text",
                                "why": "I'll add it to the brief and show it again."})
        return next_step(job)
    if key == "change":
        if a["value"]:
            job["changes"].append(task_state.clean(a["value"], 300))
        return next_step(job)
    job["answers"][key] = a
    q = next((x for x in qcard["questions"] if x["key"] == key), {})
    if q.get("fact") and a.get("how") != "skip":
        manager.remember(q["fact"], a["value"])
    return next_step(job)


def _dropped(qcard):
    job = session.state.pop("handoff", None)
    if job and (card := resume._find(job["task"])) is not None and card["status"] == "waiting":
        task_state.mark(card, "stopped", "you moved on before the job started")
        resume.save({"card": card})


def launch(job):
    """Start the worker with the brief. The task that asked for it ends as "handed to the worker"."""
    session.state.pop("handoff", None)
    try:
        if job["kind"] == "research":
            focus = (job["answers"].get("focus") or {}).get("value") or ""
            said = workers.research(job["request"], focus=focus)
        else:
            said = workers.start(job["brief"], manager.folder_of(job), job["worker"], job["size"])
    except Exception as e:
        log_exception("manager: starting the worker")
        said = f"Not started: {e}"
    conversation.task_log(f"MANAGER: launched {job['worker']}: {str(said)[:200]}")
    card = resume._find(job["task"]) if job["task"] else None
    if card is not None:
        started = not str(said).startswith("Not started")
        task_state.mark(card, "done" if started else "failed", "handed to the worker" if started else str(said)[:120])
        resume.save({"card": card})
    line = str(said).strip()
    return {"reply": line, "say": line, "activity": ["Handed the job to a worker"], "by_code": True}


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import asking, conversation, resume, session  # noqa: E402
asking.handler("manager", _answered, _dropped)
manager.HAND_OFF = from_tool
