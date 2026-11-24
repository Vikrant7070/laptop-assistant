"""Stuck and looping tasks (manager plan 4d): after every step of a laptop task, code checks for progress.

    after_step(task, calls) -> None, {"note": words for the brain}, or {"stop": the reply - a question card}
    stuck(task, why)        -> the "Stuck at: … - try a different way, or will you do this step?" card

nova/progress.py decides (a loop: the same step 3 times with nothing new; no progress: 3 steps in a row with nothing
new). The first time she's stuck the deep brain makes one new plan from the step outcomes only (plan rule 7); the
next time she stops and asks. The card goes to the chat, the strip, /tasks and - when the request came from the
phone - Telegram, like every question card. Answers: try a different way (the task carries on, with fresh chances),
"I'll do this step" (it waits, stopped, for "continue"), or stop.
"""
from nova import progress, task_state
from nova.log import log_exception


def make_plan(goal):
    """The deep brain's plan text, or (None, why). Its own function so tests can say what it gives."""
    return router.make_plan(config.settings(), goal)


def _holder(task):
    card = task_state.of(task)
    return card if card is not None else task


def _replan(task, words):
    """The one new plan: goal + one-line step outcomes + why it's stuck. -> the card's rendered plan, or ""."""
    card = task_state.of(task)
    if card is None:
        return ""
    goal = (card["goal"] + "\nAlready tried, step by step:\n" + "\n".join(task_state.outcomes(card))
            + f"\nStuck: it {words}. Plan a different way to reach the goal - not that step again.")
    try:
        plan, m = make_plan(goal)
    except Exception:
        log_exception("watching: the new plan")
        return ""
    steps = task_state.steps_from_plan(plan, {t["name"] for t in tools.TOOLS}) if plan else []
    if not steps:
        conversation.task_log(f"STUCK re-plan gave nothing ({m})")
        return ""
    task_state.set_plan(card, steps, "deep brain")
    conversation.task_log(f"STUCK re-planned with {m}: {len(steps)} steps")
    return task_state.render(card)


def after_step(task, calls):
    """Call after a batch of steps ran (session.state["last_outs"] holds their results)."""
    if not task:
        return None
    outs = session.state.get("last_outs") or []
    holder = _holder(task)
    res = progress.after_step(holder, calls, outs, [not skillrun._failed(o) for o in outs])
    why = progress.verdict(res)
    if not why:
        return None
    words = progress.words(res, why)
    conversation.task_log(f"STUCK ({why}): {words}")
    if progress.escalate(holder) == "ask":
        return {"stop": stuck(task, words)}
    session.think("🔁 I'm going round in circles - working out a different way.")
    plan = _replan(task, words)
    return {"note": f"You are stuck: you {words}. Don't do that step again. "
            + (f"Here is a new plan, made from what happened so far - follow it:\n{plan}" if plan else
               "Reach the goal a different way, or tell the user in one sentence what is blocking you.")}


def fresh(task):
    """Another brain took the task over (local first -> cloud, nova/core/app/brains.py): its progress counts afresh."""
    if task is not None:
        progress.reset(_holder(task))


def stuck(task, words):
    """Stop and ask. -> the reply dict with the question card."""
    card = task_state.of(task)
    where = task_state.where(card) if card is not None else f"“{str((task or {}).get('goal') or 'the task')[:80]}”"
    if card is not None:
        task_state.mark(card, "stuck", words)
        resume.save({"card": card})
    q = asking.question("stuck", f"Stuck at {where}: I {words}. Should I try a different way, or will you do this "
                        "step?", "one", choices=[("try", "Try a different way"), ("me", "I'll do this step"),
                                                 ("stop", "Stop the task")],
                        why="Doing the same thing again won't get anywhere.")
    qcard = asking.make("stuck", [q], task=card["id"] if card is not None else None, data={"words": words})
    session.think("🔁 I'm stuck - asking you what to do.")
    return asking.ask(qcard, "Got stuck - asked you", wait=card is not None)


def _answered(qcard, answers):
    a = next(iter(answers.values()))["value"]
    card = resume._find(qcard["task"]) if qcard["task"] else None
    if card is None:
        return resume._out("I can't find that task any more." if a == "try" else "Okay.", "Task gone")
    if a == "stop":
        task_state.mark(card, "dropped", "you stopped it when it got stuck")
        resume.save({"card": card})
        return resume._out("Okay, I've stopped that task.", "Stopped the task")
    if a == "me":
        task_state.mark(card, "stopped", "you're doing the step it was stuck on")
        resume.save({"card": card})
        return resume._out("Okay, that step is yours. Say “continue” when it's done and I'll carry on from there.",
                           "Left the step to you")
    progress.reset(card)
    task_state.mark(card, "stopped", "trying a different way")
    out = resume.prepare(card)
    nxt = session.state.get("resume_next")
    if out is None and nxt is not None:
        nxt.setdefault("extra", []).append(f"You got stuck before: you {qcard['data'].get('words', '')}. The user "
                                           "asked you to try a DIFFERENT way - don't repeat that step.")
    return out


def _dropped(qcard):
    card = resume._find(qcard["task"]) if qcard["task"] else None
    if card is not None and card["status"] in ("waiting", "stuck"):
        task_state.mark(card, "stopped", "you moved on")
        resume.save({"card": card})


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.actions import tools  # noqa: E402
from nova.brain import router  # noqa: E402
from nova.core.app import asking, config, conversation, resume, session, skillrun  # noqa: E402
asking.handler("stuck", _answered, _dropped)
