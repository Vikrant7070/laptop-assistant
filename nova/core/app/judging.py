"""Is the task done? Settling the screen after a step, the Judge's checks and its one advisory look."""


import json
import time

from nova.brain import cloud
from nova.brain import llm
from nova.actions import judge
from nova.actions import tools
from nova import protocol, task_state
from nova.actions import readback
from nova.actions import undo


def settle(tool_name, quick=False):
    """Wait until the screen stops changing after an action (a page that is still loading keeps renaming its
    window), so the check looks at the result and not at a half-loaded page. Returns the window list.
    quick: only the window list is needed next (no screenshot), so a shorter wait is enough."""
    time.sleep((1.2 if tool_name in conversation.SLOW_TOOLS else 0.5) if quick else (1.8 if tool_name in conversation.SLOW_TOOLS else 1.0))
    last, same, end = None, 0, time.time() + 5
    while True:
        conversation.check_stop()
        try:
            now = tools.open_windows_summary(3)
        except Exception:
            return last or ""
        same = same + 1 if now == last else 0
        last = now
        if same >= (1 if quick else 2) or time.time() > end:    # unchanged for about half a second / a second
            return now
        time.sleep(0.5 if not quick else 0.3)


def look_check(task, step, result, windows, activity, model):
    """Screenshot + the local vision model's verdict on the task. Sets task['done'] / task['screen'], returns its text."""
    session.think("👀 Looking at the screen to check that worked…")
    img = tools._grab_screen_jpeg()   # in memory only, like every screen look
    seen = conversation.interruptible(llm.see, img, conversation.CHECK_PROMPT.format(goal=task["goal"], step=step, result=result[:200], windows=windows),
                   preferred=model, kind="check")
    session.state["check_image"] = img        # the chat shows the last thing she looked at
    verdict = protocol.parse_verdict(seen)
    task["done"] = verdict["done"]
    task["screen"] = seen
    session.think(("✅ The screen says it's done." if task["done"] else "❌ Not finished yet." if task["done"] is False
           else "🤔 Can't tell from the screen.") + (f" I see: {verdict['screen']}" if verdict["screen"] else ""))
    activity.append("Checked the screen")
    return seen


def _big_brain(model):
    """A cloud brain, or a bigger local one the router switched to: trusted to follow the window list, so the slow
    screen look (which would reload the small vision model every step and push the big model off the GPU) runs only
    once, when it says it has finished."""
    return bool(cloud.parse(model)) or model != config.settings()["model"]


def ask_for_checks(goal, windows, model):
    """One short text-only brain call: which checks.py checks would prove this goal is done (judge.PROPOSE_PROMPT)."""
    prompt = judge.PROPOSE_PROMPT.format(goal=goal, windows=windows or "none")
    return conversation.interruptible(llm.chat, model, [{"role": "user", "content": prompt}], [], fallback=config.settings()["model"],
                         num_predict=80)[1]


def propose_checks(task, windows, model):
    """No code-made check fits this goal: ask the brain once for 1-3 checks from the checks.py list. They're
    validated like skill `expect:` lines (invalid ones dropped, never fixed). Text only, no screenshot, so it's
    quick and doesn't push the brain off the GPU the way the vision model does."""
    task["proposed"] = True
    try:
        text = ask_for_checks(task["goal"], windows, model)
    except conversation.Stopped:
        raise
    except Exception as e:
        conversation.task_log(f"JUDGE couldn't get checks from the brain: {type(e).__name__}: {e}")
        return
    found = judge.parse_proposed(text)
    conversation.task_log("JUDGE CHECKS (brain): " + ("; ".join(judge.describe(c) for c in found) if found else "none fit"))
    if found:
        task["checks"], task["covers"], task["strict"] = found, True, False   # advisory when they fail


def check_step(call, out, activity, model):
    """After an on-screen action: wait for the screen to settle and run the Judge's checks for the goal (quick, no
    screenshot), adding the window list and what the checks saw to the tool result. The slow (~10 s) screen look
    runs at most once, at the end (final_judge), and only when no check covers the goal. That holds for every
    brain, so the small local model no longer looks after every step."""
    task = session.state.get("task")
    if not task or out.startswith("Error"):
        return out
    task["steps"] += 1
    task["did"].append(f"{call['name']} {json.dumps(call['args'], ensure_ascii=False)[:80]}")
    for key in ("into_window", "title_or_app", "window"):
        if call["args"].get(key):
            task["window"] = str(call["args"][key])     # page checks look at the window she worked in
    card = task_state.of(task)
    task_state.finish(card, out, not skillrun._failed(out))      # the tool really ran: the card ticks it before she reads it
    windows = settle(call["name"], quick=True)
    note = protocol.check_note(task["steps"], windows)
    task["done"] = None            # an earlier verdict is out of date after a new step
    if task["steps"] >= conversation.LOOK_FROM_STEP and not task.get("proposed"):
        propose_checks(task, windows, model)
    verdict = None
    if task.get("checks"):
        verdict = judge.decide(judge.run_checks(task["checks"], {"result": out, "window": task.get("window", "")},
                                                timeout=conversation.STEP_CHECK_WAIT), task.get("covers"), strict=task.get("strict", True))
        if verdict["done"]:
            task["done"] = True
            session.think("✅ My checks say it's done: " + verdict["why"])
    about = "\n" + (task_state.render(card) or f"The task: {task['goal']}")
    if task["done"]:
        return (out + note + about + "\nThe task looks finished. Do NOT press or click anything "
                "else - just tell the user in one short sentence.")
    if verdict and verdict["done"] is False:
        return (out + note + about + f"\nNot true yet: {verdict['why']}.\nIf it isn't finished, "
                "do the next step now. If this step didn't work, try another way.")
    return (out + note + about + "\nIf it isn't finished, do the next step now. "
            "If this step didn't work, try another way.")


def final_judge(task, text, activity, model):
    """The brain says it has finished: the Judge's verdict, in the plan's order of trust. Deterministic checks come
    first (a failed one means not done, whatever the screen look says). A screen look only happens when no check
    covers the goal, at most once per task, and its answer is advisory. Sets task['done'] / task['screen'] /
    task['verdict_source'] and logs a verdict record."""
    strict = task.get("strict", True)
    results = (judge.run_checks(task["checks"], {"window": task.get("window", "")}, timeout=conversation.FINAL_CHECK_WAIT)
               if task.get("checks") else [])
    vision, seen = None, ""
    want_look = (judge.needs_vision(results, task.get("covers"), strict) and task["steps"] >= conversation.LOOK_FROM_STEP
                 and not task.get("looks"))
    if want_look and not judge.vision_on():
        conversation.task_log(f"FINAL CHECK skipped: {judge.VISION_ENV}=0")
    elif want_look and tools._screen_allowed():
        task["looks"] = task.get("looks", 0) + 1
        try:
            seen = look_check(task, f"(all steps done; the assistant now says: {(text or '')[:150]})", "",
                              tools.open_windows_summary(3), activity, model)
            vision = task["done"]
        except conversation.Stopped:
            raise
        except Exception as e:
            conversation.task_log(f"FINAL CHECK failed: {e}")
    verdict = judge.decide(results, task.get("covers"), vision, strict)
    if verdict["source"] == "checks":
        task["done"] = verdict["done"]
        if verdict["done"] is False:
            task["screen"] = "A check failed: " + verdict["why"]
            task["fail_plain"] = judge.plain_failure(results)
            session.think("❌ My check says it isn't done: " + task["fail_plain"])
        else:
            session.think("✅ My checks say it's done: " + verdict["why"])
        activity.append("Checked it worked")
    if verdict["source"] != "none":
        task["verdict_source"] = verdict["source"]
    task["verdict"] = judge.record(task["goal"], task.get("checks") or [], results, seen, verdict)
    conversation.task_log("VERDICT " + json.dumps(task["verdict"], ensure_ascii=False))


def readback_verdict(task):
    """She says she's finished: a read-back that failed (and wasn't put right later) means it isn't done, whatever
    the brain or the screen look said. Feeds the usual path: one more go with the failing check, then the plain
    "I don't think I finished that - <what>"."""
    card = task_state.of(task)
    bad = task_state.failing(card)
    if not bad or (task.get("done") is False and task.get("verdict_source") == "checks"):
        return                                # nothing failed, or the Judge's own check already said why
    task.update(done=False, verdict_source="checks", screen="Read back by code: " + "; ".join(bad), fail_plain=bad[0])
    conversation.task_log("READ BACK says not done: " + "; ".join(bad))
    if not task.get("nudged"):
        # One fresh plan for the one more go (cloud brain first, else the deep brain), from the step outcomes only.
        if watching.replan(task, f"A check by code failed: {bad[0]}. Plan how to finish the task - fix what that "
                                 "check found, don't repeat a step that already failed.", "READ BACK"):
            task["screen"] += "\nA new plan is on the card below - follow it from the first step not done."


def execute(calls, approved, activity, model):
    outs = []
    card = task_state.of(session.state.get("task"))
    for c in calls:
        session.state.pop("ran_as", None)
        rid = task_state.start(card, c)   # the card: started, not confirmed - until the tool has really run
        snap = undo.before(c)             # 4e: a level-1 step that can be undone keeps what was there
        started = time.time()             # 4c-4: a download must be newer than this
        try:
            out = conversation._execute_one(c, approved, activity, model)
            undo.after(c, out, not skillrun._failed(out), snap, card["id"] if card is not None else "")
            c = session.state.pop("ran_as", None) or c   # the call that really ran (web_research may become a background job)
            task_state.finish(card, out, not skillrun._failed(out), c["name"], rid)   # no-op if check_step did
            if card is not None and not skillrun._failed(out):     # 4c: code reads the result back, never her "done"
                rb = readback.read_back(c["name"], c.get("args") or {}, out, started)
                task_state.set_check(card, rid, rb)
                out += readback.note(rb)
                if not rb or rb["state"] != "failed":
                    readback.found_after_all(card, c["name"], rid)
            elif card is not None:        # L3: an error that says the file isn't there is a failed check too
                task_state.set_check(card, rid, readback.missing(c["name"], c.get("args") or {}, out))
        except conversation.Stopped:      # every call still gets a result, so the history stays valid for the next request
            out = "The user pressed Stop before this step finished. It was not done (or only partly)."
            task_state.leave_open(card, rid)   # half-done: safe resume (4-pre b) checks it before anything redoes it
        conversation.task_log(f"TOOL {c['name']} {json.dumps(c['args'], ensure_ascii=False)[:300]}\n   -> " + out.replace("\n", "\n      "))
        outs.append(out)
        skillrun._trace(c, out)
    session.state["last_outs"] = outs
    resume.save()                         # the card after every batch of steps: a restart loses at most this batch
    return [llm.tool_result_message(model, c, out) for c, out in zip(calls, outs)]


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import config, conversation, resume, session, skillrun, watching  # noqa: E402
