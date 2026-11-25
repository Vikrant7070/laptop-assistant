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
import re

from nova import protocol, task_state
from nova.actions import jobrounds, manager, workers
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
    return _go_card(job, "Here's the brief. Start?", "Wrote the brief")


def _go_card(job, ask_text, activity):
    """The brief's card: Go (worker, cost) · Use the other worker instead · Change it, with why this worker."""
    choices = [("go", manager.go_label(job))]
    if manager.switch_label(job):
        choices.append(("other", manager.switch_label(job)))
    choices.append(("change", "Change it"))
    card = asking.make("manager", [asking.question(
        "go", ask_text, "one", choices=choices, why=job.get("reason", "") + " Nothing starts until you say go.")],
        task=job["task"], data={"job": job["id"], "key": "go"})
    out = asking.ask(card, activity)
    out["brief"] = job["brief"]
    if session.state.get("remote"):            # the phone has no folded brief: it goes in the message
        out["reply"] = job["brief"] + "\n\n" + out["reply"]
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
        if a["value"] == "other" and manager.other_worker(job["worker"]):
            job["worker"] = manager.other_worker(job["worker"])
            job["reason"] = f"You picked {workers.NAMES[job['worker']]}."
            return launch(job)
        if a["value"] == "drop":
            session.state.pop("handoff", None)
            _end_task(job, "stopped", "you dropped it")
            return _said("Okay, I dropped it.", "Dropped the job")
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


def _said(line, activity):
    return {"reply": line, "say": line, "activity": [activity], "by_code": True}


def _for_user(said):
    """workers.start / research answer the brain ("... Tell the user in one short sentence ..."): the user gets the
    first part only."""
    return re.split(r"\s+(?:Tell the user|Don't do the job)", str(said).strip())[0].strip()


def _end_task(job, status, why):
    card = resume._find(job["task"]) if job.get("task") else None
    if card is not None:
        task_state.mark(card, status, why)
        resume.save({"card": card})


def launch(job):
    """Start the worker with the brief. Research -> coding starts the research first (the coding brief comes back
    with what it found, after_job). The task that asked for it ends as "handed to the worker"."""
    session.state.pop("handoff", None)
    chain = job["kind"] == "research_coding" and not job.get("found")
    meta = {"job_type": "research" if chain else job["type"], "manager_job": job["id"]}
    if job["kind"] == "research_coding":
        meta["chain_step"] = "1 of 2" if chain else "2 of 2"
    try:
        if chain:
            said = workers.research(manager.research_topic(job["request"]), meta=meta)
            m = re.search(r"#(\d+)", str(said))
            if m:
                session.state.setdefault("chains", {})[int(m.group(1))] = job
        elif job["kind"] == "research":
            focus = (job["answers"].get("focus") or {}).get("value") or ""
            said = workers.research(job["request"], focus=focus, meta=meta)
        else:
            said = workers.start(job["brief"], manager.folder_of(job), job["worker"], job["size"], meta=meta)
    except Exception as e:
        log_exception("manager: starting the worker")
        said = f"Not started: {e}"
    conversation.task_log(f"MANAGER: launched {'research (chain)' if chain else job['worker']}: {str(said)[:200]}")
    started = not str(said).startswith("Not started")
    _end_task(job, "done" if started else "failed", ("handed to the research worker first" if chain else
                                                      "handed to the worker") if started else str(said)[:120])
    line = _for_user(said) + (" When it's done I'll show you the coding brief with what it found." if chain and started else "")
    return _said(line, "Handed the job to a worker")


# ------------------------------------------------------------------ 4b: research -> coding, the second step

FOUND_CHARS = 1500


def after_job(job):
    """integrations.job_done, for every finished background job: the research step of a chain is done -> the coding
    brief with what it found (and its Go card), or what to do when the research failed. Never raises."""
    try:
        mjob = session.state.get("chains", {}).pop(job.get("id"), None)
        if mjob is None:
            return None
        rec = next((r for r in reversed(workers.load_jobs()) if r.get("job") == job.get("id")
                    and r.get("manager_job") == mjob["id"]), None)
        session.state["handoff"] = mjob
        if job.get("status") != "done":
            mjob["reason"] = "The research step didn't work."
            card = asking.make("manager", [asking.question(
                "go", f"The research didn't work ({str(job.get('error') or job.get('status'))[:120]}). "
                "Start the coding without it?", "yesno", choices=[("go", "Start coding without it"), ("drop", "Drop it")])],
                task=mjob["task"], data={"job": mjob["id"], "key": "go"})
            mjob["found"] = "(the research step failed - nothing found)"
            mjob["brief"] = manager.brief(mjob)
            return _deliver(asking.ask(card, "Research failed"))
        found = workers.report(rec=rec) if rec else str(job.get("result") or "")
        sources = [s if isinstance(s, str) else (s.get("url") or "") for s in (job.get("sources") or [])][:5]
        mjob["found"] = (found[:FOUND_CHARS] + ("\nSources: " + ", ".join(s for s in sources if s) if sources else "")).strip()
        mjob["brief"] = manager.brief(mjob)
        if not manager.needs_go(mjob, mode()):
            return _deliver(launch(mjob))
        return _deliver(_go_card(mjob, "Research done. Here's the coding brief with what it found. Start?",
                                 "Research done - wrote the coding brief"))
    except Exception:
        log_exception("manager: the research -> coding chain")
        return None


# ------------------------------------------------------------------ a worker at its usage limit (plan 9, answer 3)

def limit_card(job):
    """integrations.job_done for a job that stopped at its worker's usage limit (jobrounds.usage_stop): "Carry on with
    <other> (cost)" / "Wait". Never switches by itself - the other worker may cost money. Never raises."""
    try:
        h = job["handoff"]
        q = asking.question("handoff", f"{job.get('result') or 'The worker hit its usage limit.'} Carry on with "
                            f"{workers.NAMES[h['to']]}, from where it stopped?", "one",
                            choices=[("go", f"Carry on with {h['label']}"), ("wait", "Wait")],
                            why="Switching workers may cost money, so I only do it when you say.")
        return _deliver(asking.ask(asking.make("limit", [q], data=dict(h)), "A worker hit its usage limit", wait=False))
    except Exception:
        log_exception("manager: the usage-limit card")
        return None


def _limit_answered(qcard, answers):
    h = qcard["data"]
    rec = next((r for r in reversed(workers.load_jobs()) if r.get("key") == h["key"]), None)
    if rec is None:
        return _said("I can't find that job any more.", "Job gone")
    if answers["handoff"]["value"] != "go":
        return _said(f"Okay, I'll wait - nothing switched. Its changes so far are kept, not committed. Say 'try job "
                     f"#{rec['job']} again' when you want {workers.NAMES[h['to']]} to carry on.", "Waiting")
    return _said(_for_user(jobrounds.hand_over(rec)), f"Handed job #{rec['job']} to {workers.NAMES[h['to']]}")


def _deliver(out):
    """A card asked outside a request (a job just finished): the chat, her voice, and the phone when the user is away
    (it is 'blocked, waiting for you' - Telegram gets it with its buttons)."""
    voice_io.push_event({**out, "kind": "job"})
    voice_io.announce(out.get("say") or out.get("reply") or "")
    if integrations.user_away() and out.get("question"):
        try:
            integrations.phone.reply(out)
        except Exception:
            log_exception("manager: sending the card to the phone")
    return out


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import asking, conversation, integrations, resume, session, voice_io  # noqa: E402
asking.handler("manager", _answered, _dropped)
asking.handler("limit", _limit_answered)
manager.HAND_OFF = from_tool
