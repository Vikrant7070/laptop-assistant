"""Scheduled and repeating jobs in the app (manager plan 4g). The rules and the record are nova/schedules.py.

    route(text)     body.answer, after the task starts: "every morning at 9 check my Gmail and tell me what's
                    important" -> a question card with the plan, the times and each step's risk level; nothing is
                    saved until the user says Save it. Also "pause / resume / delete schedule #3" and "what are my
                    schedules" (the Tasks page's buttons fill those words into the chat). -> a reply, or None
    tick(now)       every TICK seconds while Nova runs (background.start_background): the first schedule that is
                    due runs, once - unless another task is going on (one laptop task at a time): then it waits.
    run(rec, now)   one run, through body.answer like a request, marked with protocol.scheduled(...)

Only the user's own words make a schedule: route() reads the request the user typed, said, or sent from the paired
phone (Telegram from an unknown chat never gets here; text inside an email or a page is a tool's result, and no tool
makes a schedule). Level-2 steps still ask when they run - in the chat, or on Telegram with Yes / No when the user is
away. Runs happen only while Nova is on; a run missed while the laptop was off or asleep happens once when she is
back, and says it is late. Results: when the user is away, Telegram gets "done" or "blocked" (needs you / couldn't
finish) only; everything is on /tasks (GET /api/schedules) and in the chat.
"""
import time

from flask import jsonify

from nova import protocol, schedules, task_state
from nova.core.app import api
from nova.log import log_exception

TICK = 20                   # seconds between looks at the schedules
LATE_SECS = 120             # a run this long after its time says it is late
OWNER = "schedule"


def _said(line, activity):
    return {"reply": line, "say": line, "activity": [activity], "by_code": True}


def _level_line(step):
    return f"{step['text']} - {schedules.LEVEL_WORDS[step['level']]}"


def plan_text(plan, now=None):
    """The plan as the card's folded brief: when, first run, each step with its risk level."""
    now = time.time() if now is None else now
    steps = schedules.steps(plan["request"])
    lines = [f"⏰ {plan['request']}", f"When: {schedules.describe(plan['every'])}"
             + (" (no time given - say another time if 9:00 is wrong)" if plan.get("guessed_time") else ""),
             f"First run: {schedules.when_text(schedules.next_after(plan['every'], now))}", "Steps:"]
    lines += [f"  {i}. {_level_line(st)}" for i, st in enumerate(steps, 1)]
    lines.append("Runs only while the laptop is on; a run missed while it was off happens once when it's back, late. "
                 "Level-2 steps ask you each time (on Telegram when you're away).")
    return "\n".join(lines)


# ------------------------------------------------------------------ the user's words

def route(text):
    """A schedule in the user's own words -> the save card; a schedule command -> its answer. Else None."""
    if str(text or "").startswith(protocol.SCHEDULED_START):
        return None                                   # a scheduled run never makes or changes schedules
    words = protocol.unspoken(text).strip()
    cmd = schedules.command(words)
    if cmd:
        return command(*cmd)
    plan = schedules.parse(words)
    if not plan:
        return None
    conversation.task_log(f"SCHEDULE asked: {schedules.describe(plan['every'])} - {plan['request'][:120]}")
    return ask_save(plan, words)


def ask_save(plan, words):
    steps = schedules.steps(plan["request"])
    top = max(st["level"] for st in steps)
    why = ("Nothing is saved until you say so. "
           + ("A level-2 step will ask you every time it runs." if top == 2 else
              "Every step is level 0 or 1, so it runs without asking." if top == 1 else "It only reads."))
    first = schedules.when_text(schedules.next_after(plan["every"], time.time()))
    card = asking.make(OWNER, [asking.question(
        "save", f"Save this schedule? {schedules.describe(plan['every'])}, first run {first}.", "yesno",
        choices=[("save", "Save it"), ("no", "Don't save")], why=why)],
        title=f"⏰ {plan['request']}", data={"plan": plan, "words": words,
                                            "source": "phone" if session.state.get("remote") else "laptop"})
    out = asking.ask(card, "Planned a schedule", wait=False)
    out["brief"] = plan_text(plan)
    if session.state.get("remote"):                   # the phone has no folded brief: it goes in the message
        out["reply"] = out["brief"] + "\n\n" + out["reply"]
    return out


def _answered(qcard, answers):
    if answers["save"]["value"] != "save":
        return _said("Okay, I didn't save it.", "Schedule not saved")
    d = qcard["data"]
    rec = schedules.add(d["plan"], d["words"], d.get("source", "laptop"))
    conversation.task_log(f"SCHEDULE #{rec['id']} saved: {schedules.describe(rec['every'])} - {rec['request'][:120]}")
    eventbus.publish("schedule", {"id": rec["id"]})
    return _said(f"Saved ⏰ schedule #{rec['id']}: {schedules.describe(rec['every'])} - {rec['request']}. Next run: "
                 f"{schedules.when_text(rec['next'])}. It's on the Tasks page, with Pause and Delete.",
                 f"Saved schedule #{rec['id']}")


def command(verb, n):
    if verb == "list":
        recs = schedules.load()
        if not recs:
            return _said("You have no schedules. Say something like “every morning at 9 check my Gmail”.",
                         "Checked schedules")
        lines = [f"#{r['id']} {schedules.describe(r['every'])}: {r['request']} - "
                 + ("paused" if r.get("paused") else "next " + schedules.when_text(r["next"])) for r in recs]
        return _said("Your schedules:\n" + "\n".join(lines), "Checked schedules")
    if schedules.get(n) is None:
        return _said(f"There's no schedule #{n}.", "No such schedule")
    if verb == "delete":
        schedules.delete(n)
        line, act = f"Deleted schedule #{n}. It won't run again.", f"Deleted schedule #{n}"
    else:
        rec = schedules.set_paused(n, verb == "pause")
        line, act = ((f"Paused schedule #{n}. It won't run until you say “resume schedule #{n}”.", f"Paused schedule #{n}")
                     if verb == "pause" else
                     (f"Resumed schedule #{n}. Next run: {schedules.when_text(rec['next'])}.", f"Resumed schedule #{n}"))
    conversation.task_log(f"SCHEDULE #{n}: {verb}")
    eventbus.publish("schedule", {"id": n})
    return _said(line, act)


# ------------------------------------------------------------------ running them

def busy():
    """Another task is going on (one laptop task at a time): a request, an Allow / No or a card waiting."""
    return bool(session.lock.locked() or session.state.get("pending") or asking.current()
                or voice_io.convo.get("thinking"))


def tick(now=None):
    """Run the first due schedule, or mark it waiting while another task is going on. -> the run, or None."""
    now = time.time() if now is None else now
    due = schedules.due(now)
    if not due:
        return None
    rec = due[0]
    if busy():
        if not rec.get("waiting"):
            schedules.change(rec["id"], lambda r: r.update(waiting=now))
            conversation.task_log(f"SCHEDULE #{rec['id']} due - waiting: another task is going on")
            eventbus.publish("schedule", {"id": rec["id"]})
        return None
    return run(rec, now)


def _result(out, status, card):
    if status >= 400 or out.get("error"):
        return "failed"
    if out.get("pending") or out.get("question"):
        return "blocked"
    if card is not None and card["status"] in ("failed", "stuck"):
        return "failed"
    if card is not None and card["status"] == "waiting":
        return "blocked"
    return "done"


def run(rec, now=None):
    """One run of a schedule. The next time is set first, from now: runs missed meanwhile happen once, not each."""
    now = time.time() if now is None else now
    late = ""
    if schedules.is_late(rec, now, LATE_SECS):
        late = f"it was due {schedules.when_text(rec['next'])}; the laptop was off or asleep"
    due = rec["next"]
    schedules.change(rec["id"], lambda r: r.update(next=schedules.next_after(r["every"], now), waiting=None))
    away = integrations.user_away()
    when = schedules.describe(rec["every"])
    conversation.task_log(f"SCHEDULE #{rec['id']} runs ({when}){' LATE' if late else ''}: {rec['request'][:120]}")
    try:
        out, status = body.answer(protocol.scheduled(rec["id"], when, late) + " " + rec["request"], quiet=away)
    except Exception as e:
        log_exception("schedules: a run")
        out, status = {"error": f"{type(e).__name__}: {e}"}, 500
    card = task_state.of(session.state.get("task"))
    result = _result(out, status, card)
    head = f"⏰ Schedule #{rec['id']} ({rec['request']})"
    if late:
        head += f" - late: {late}"
    said = task_state.clean(out.get("reply") or out.get("error") or "", 300)
    entry = {"due": due, "at": now, "late": bool(late), "status": result, "task": card["id"] if card else "",
             "said": said}
    schedules.change(rec["id"], lambda r: r.update(runs=(r.get("runs") or [])[-(schedules.MAX_RUNS - 1):] + [entry]))
    eventbus.publish("schedule", {"id": rec["id"]})
    shown = {**{k: v for k, v in out.items() if k != "images"}, "kind": "job"}
    shown["reply"] = head + ":\n" + (out.get("reply") or out.get("error") or "")
    voice_io.push_event(shown)
    if away:
        _tell_phone(shown, result)
    return entry


def _tell_phone(out, result):
    """Away: Telegram gets done or blocked only (plan section 9, answer 6). Blocked keeps its Yes / No buttons."""
    try:
        if result == "blocked":
            integrations.phone.reply(out)
        elif result == "failed":
            integrations.phone.notify(out["reply"] + "\n(Blocked - it needs you. It's on the Tasks page.)")
        else:
            integrations.phone.notify(out["reply"])
    except Exception:
        log_exception("schedules: telling the phone")


def watch():
    """runtime.spawn(..., every=TICK): one look at the schedules."""
    tick()


# ------------------------------------------------------------------ the Tasks page

def page_row(rec, now=None):
    now = time.time() if now is None else now
    runs = rec.get("runs") or []
    return {"id": rec["id"], "request": task_state.clean(rec["request"], 200), "when": schedules.describe(rec["every"]),
            "paused": bool(rec.get("paused")), "next": None if rec.get("paused") else rec["next"],
            "next_text": "paused" if rec.get("paused") else schedules.when_text(rec["next"]),
            "waiting": bool(rec.get("waiting")) and not rec.get("paused") and rec["next"] <= now,
            "steps": [{"text": task_state.clean(s["text"], 200), "level": s["level"]} for s in rec.get("steps") or []],
            "source": rec.get("source", ""), "made": rec.get("made"),
            "runs": [{**r, "said": task_state.clean(r.get("said"), 300)} for r in runs[-schedules.MAX_RUNS:]][::-1],
            "actions": [{"label": "▶ Resume…", "say": f"resume schedule #{rec['id']}"} if rec.get("paused") else
                        {"label": "⏸ Pause…", "say": f"pause schedule #{rec['id']}"},
                        {"label": "🗑 Delete…", "say": f"delete schedule #{rec['id']}"}]}


@api.app.get("/api/schedules")
def schedules_list():
    """Read-only: Pause / Resume / Delete open the chat with the words filled in (/?draft=…)."""
    return jsonify(schedules=[page_row(r) for r in schedules.load()], now=time.time())


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import asking, body, conversation, eventbus, integrations, session, voice_io  # noqa: E402
asking.handler(OWNER, _answered)
