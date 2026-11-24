"""Watch a task's progress (manager plan 4d): after every step, code asks "did anything new happen? is this a loop?".

Pure code, kept on the task card (or the task, when it has no card) as `progress`:

    after_step(holder, calls, outs, oks) -> {"loop": bool, "stalled": n, "what": "click_at x=10 y=20"} or None
    verdict(res)                         -> "loop" | "no progress" | ""
    escalate(holder)                     -> "replan" the first time, then "ask"
    words(res, why)                      -> "clicked the same thing (click_at …) 3 times with nothing new"

- A step is *new* when this exact step (tool + arguments) hasn't given this exact result before in the task, and it
  didn't fail. A different step that says "ok" again is new: something else was done.
- A *loop*: the same step LOOP_SAME times within the last LOOP_WINDOW steps, the last one bringing nothing new
  (Magentic-One's progress ledger; the old repeat stop skipped clicks, and a click repeated 3 times is the classic).
- *No progress*: STALL steps in a row with nothing new (the plan said 2; 3 keeps "check the time and the laptop a few
  times" from looking stuck).
- Steps where repeating is the point (scrolling, keys, volume) are left out of both.
After a nudge the counting starts again: a second stall or loop - or the very same step again, with nothing new -
and she stops and asks.
"""
import hashlib
import json
import re

from nova import protocol

LOOP_SAME = 3
LOOP_WINDOW = 4
STALL = 3
KEEP_STEPS = 12
KEEP_SEEN = 300
NEUTRAL = {"scroll", "press_keys", "media_control", "video_control", "set_volume", "set_brightness", "react"}


def _h(text):
    return hashlib.md5(text.encode("utf-8", "replace")).hexdigest()[:12]


def _norm(out):
    text = str(out or "").split(protocol.CHECK_NOTE_START)[0]      # the step note changes every step
    return re.sub(r"\s+", " ", text).strip().lower()[:500]


def sig(calls):
    return json.dumps([(c["name"], c.get("args") or {}) for c in calls], sort_keys=True, default=str)


def describe(calls):
    """'click_at x=10 y=20' - the step in a few words, for the user."""
    c = calls[0]
    args = " ".join(f"{k}={v}" for k, v in list((c.get("args") or {}).items())[:3])
    return (c["name"] + (f" {args}" if args else ""))[:80] + (f" (+{len(calls) - 1} more)" if len(calls) > 1 else "")


def _state(holder):
    p = holder.get("progress")
    if not isinstance(p, dict):
        p = holder["progress"] = {"seen": [], "steps": [], "mark": 0, "nudges": 0}
    return p


def after_step(holder, calls, outs, oks=None):
    """Record one step (the calls of one brain turn and what they gave). None for a neutral step."""
    if holder is None or not calls or all(c["name"] in NEUTRAL for c in calls):
        return None
    p, s = _state(holder), sig(calls)
    oks = list(oks) if oks is not None else [True] * len(calls)
    keys = [_h(s + "|" + _norm(o)) for o in (outs or [""])]
    new = any(k not in p["seen"] for k in keys) and all(oks)
    p["seen"] = (p["seen"] + [k for k in keys if k not in p["seen"]])[-KEEP_SEEN:]
    p["steps"].append({"sig": _h(s), "new": new})
    if len(p["steps"]) > KEEP_STEPS:
        cut = len(p["steps"]) - KEEP_STEPS
        del p["steps"][:cut]
        p["mark"] = max(0, p["mark"] - cut)
    recent = p["steps"][p["mark"]:]
    window = recent[-LOOP_WINDOW:]
    again = p.get("stuck_sig") == _h(s)                 # the very step she was told was a loop, once more
    loop = not new and (again or sum(st["sig"] == _h(s) for st in window) >= LOOP_SAME)
    stalled = 0
    for st in reversed(recent):
        if st["new"]:
            break
        stalled += 1
    return {"loop": loop, "stalled": stalled, "new": new, "what": describe(calls),
            "repeats": sum(st["sig"] == _h(s) for st in window)}


def verdict(res):
    if not res:
        return ""
    if res["loop"]:
        return "loop"
    return "no progress" if res["stalled"] >= STALL else ""


def escalate(holder):
    """The first stuck moment gets a new plan, the next one a question. Counting starts again after either."""
    p = _state(holder)
    p["nudges"] += 1
    p["mark"] = len(p["steps"])
    p["stuck_sig"] = p["steps"][-1]["sig"] if p["steps"] else ""
    return "replan" if p["nudges"] == 1 else "ask"


def reset(holder):
    """The user said "try a different way": fresh chances."""
    if holder is not None:
        holder["progress"] = {"seen": [], "steps": [], "mark": 0, "nudges": 0}


def words(res, why):
    if why == "loop":
        return f"did the same step ({res['what']}) {res['repeats']} times with nothing new"
    return f"did {res['stalled']} steps in a row with nothing new (the last: {res['what']})"
