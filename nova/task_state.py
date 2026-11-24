"""The task card: one working memory per task, written by code and shown to the brain on every step (plan 4-pre a).

    TASK: clean my laptop and tell me how much space was freed
    PLAN (deep brain): 1 check disk ✅  2 clean temp files ✅  3 empty recycle bin ⏳  4 report ·
    DONE (really): get_system_status → disk 77%; clean_temp_files → 937 MB freed
    NOW: step 3 - empty recycle bin

The card lives on the task (`task["card"]`). Only code changes it, never the brain:
- `start(card, call)` just before a tool runs: the run (and the plan step it belongs to) is *started, not confirmed*;
- `finish(card, out, ok)` when the tool has really run: done (✅) only when it ran and didn't fail, else failed (❌);
  "Not run" / "declined" results are skipped (⏭). A Stop in between leaves the run *started* - step (b)'s safe
  resume checks such a step before redoing it.
- `mark(card, status, why)`: running / waiting / stopped / interrupted / done / failed / stuck, with why it changed.
What the brain says ("done!", "I opened it") never ticks a step.

Plans come from a skill's own steps or the deep brain's plan text; a step that names a tool Nova doesn't have is
dropped. `render(card)` stays under MAX_CHARS (~250 tokens): the oldest results go first.

The card is plain JSON (a `version` field for later upgrades), so step (b) can save it and the /tasks page show it.
"""
import json
import re
import time

from nova import known

VERSION = 1
MAX_CHARS = 1000            # ~250 tokens: the card is sent after every step, inside the 8K window
MAX_RUNS = 40               # runs kept on the card (the oldest go first)
MAX_PLAN = 12               # the planner is asked for at most 12 steps
STEP_CHARS = 48             # one plan step's text on the card
RESULT_CHARS = 70           # one run's result on the card

ICONS = {"pending": "·", "started": "⏳", "done": "✅", "failed": "❌", "skipped": "⏭"}
STATUSES = ("running", "waiting", "stopped", "interrupted", "done", "failed", "stuck", "dropped")
on_change = None            # on_change(card): set by app.resume - the live task strip hears every change (4-pre c)
SOURCES = ("skill", "deep brain", "cloud", "none")

# A result that says the step never ran: not a failure of the step, it was skipped.
_SKIPPED_RE = re.compile(r"^\W*(not (?:run|done)\b|the user declined|the user pressed stop)", re.I)
# A planner's "tool" slot that looks like a tool name (snake_case) - checked against Nova's real tools.
_TOOLISH_RE = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b")
_NUMBERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.+)$")
# Never on the card: anything that looks like a code, password or key (same words as the task log's filter).
_SECRET_RE = re.compile(r"\b(otp|one[- ]time|passcode|password|pin|cvv|verification|security code|login code|api[ _-]?key|token)\b"
                        r"|\bcode\b\W{0,3}(is\W{1,3})?\d{4,8}\b", re.I)     # "the code is 123456" (plain "code" is a coding word)
_HIDDEN = "(hidden)"


def clean(text, n):
    """One line, at most n characters, nothing secret-looking."""
    line = " ".join(str(text or "").split())
    if _SECRET_RE.search(line):
        return _HIDDEN
    return line if len(line) <= n else line[: n - 1].rstrip() + "…"


def new(goal, steps=(), source="none", tid=None):
    """A fresh card for a task. `steps` are {"text", "tool"} dicts (see steps_from_plan / steps_from_skill)."""
    now = time.time()
    card = {"version": VERSION, "id": str(tid or f"{now:.3f}"), "goal": clean(goal, 300), "status": "running", "why": "",
            "source": "none", "plan": [], "runs": [], "open": [], "started": now, "updated": now, "app": "",
            "unsaid": "", "replanned": False, "known": known.from_goal(goal), "asks": []}   # 4d: what it knows / asks
    set_plan(card, steps, source)
    return card


def attach(task, steps=(), source="none"):
    """Give a task dict its card (replacing any old one). -> the card."""
    task["card"] = new(task.get("goal", ""), steps, source, task.get("id"))
    task["card"]["app"] = clean(task.get("app") or "", 60)
    return task["card"]


# ------------------------------------------------------------------ saved cards (step b)

RESUMABLE = ("stopped", "interrupted")
_DEFAULTS = {"id": "", "goal": "", "status": "stopped", "why": "", "source": "none", "plan": [], "runs": [], "open": [],
             "started": 0.0, "updated": 0.0, "app": "", "unsaid": "", "replanned": False}


def upgrade(card):
    """A card loaded from disk, made safe to use with this code. -> the card, or None if it isn't a card at all.

    Older (or version-less) cards get the fields added since; a card from a NEWER Nova is kept read-only (shown,
    never resumed), so a code update - or going back to an older one - never crashes on a saved card."""
    if not isinstance(card, dict) or not card.get("goal"):
        return None
    try:
        v = int(card.get("version") or 0)
    except (TypeError, ValueError):
        v = 0
    for k, default in _DEFAULTS.items():
        card.setdefault(k, list(default) if isinstance(default, list) else default)
    if v > VERSION:
        card["read_only"] = True
        return card
    for i, run in enumerate(card["runs"]):
        run.setdefault("id", i + 1)
        run.setdefault("step", None)
        run.setdefault("hint", "")
        run.setdefault("result", "")
    card["next"] = max([r["id"] for r in card["runs"]] + [card.get("next") or 0])
    card["version"] = VERSION
    return card


def resumable(card):
    """Stopped or interrupted, with something done or planned, and not from a newer Nova."""
    return (card is not None and card.get("status") in RESUMABLE and not card.get("read_only")
            and bool(card.get("runs") or card.get("plan")))


def unconfirmed(card):
    """Runs that started but never finished (a Stop or a restart in the middle of the step)."""
    return [r for r in (card or {}).get("runs", []) if r["state"] == "started" and r["id"] not in card["open"]]


def interrupt(card, why):
    """A restart found this card still running or waiting: every open run is now *started, not confirmed*."""
    card["open"] = []
    mark(card, "interrupted", why)


def outcomes(card):
    """One line per plan step (or per run, with no plan): its words and how it went, never a raw result. This is
    all a re-planner is sent (plan rule 7)."""
    if card["plan"]:
        return [f"step {i + 1} {st['text']}: {st['state']}" for i, st in enumerate(card["plan"])]
    return [f"{r['tool']}: {r['state']}" for r in card["runs"][-8:]]


def of(task):
    """The task's card, or None (a task made before cards, or no task)."""
    card = (task or {}).get("card")
    return card if isinstance(card, dict) else None


# ------------------------------------------------------------------ plans

def steps_from_plan(text, known):
    """The deep brain's numbered plan ("1. step - tool - how to tell it worked") -> [{"text", "tool"}].

    A step whose tool slot names a tool Nova doesn't have (a snake_case word not in `known`) is dropped; an app or
    plain words in that slot are fine (tool ""). RISKS / DONE WHEN lines are not steps."""
    out = []
    for line in str(text or "").splitlines():
        m = _NUMBERED_RE.match(line)
        if not m:
            continue
        parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", m.group(2))]
        what = parts[0].strip(" *")
        slot = parts[1] if len(parts) > 1 else ""
        named = _TOOLISH_RE.findall(slot) or [w for w in _TOOLISH_RE.findall(what) if w in known]
        if any(n not in known for n in named):
            continue                    # the plan names a tool Nova doesn't have: never shown to the brain as a step
        if what:
            out.append({"text": clean(what, STEP_CHARS), "tool": named[0] if named else ""})
    return out[:MAX_PLAN]


def steps_from_skill(skill):
    """A routine skill's own steps -> [{"text", "tool"}] (steps without a tool - say / ask lines - are left out)."""
    out = []
    for st in (skill or {}).get("steps") or []:
        tool = st.get("tool") if isinstance(st, dict) else None
        if not tool:
            continue
        args = st.get("args") or {}
        hint = next((str(v) for v in args.values() if isinstance(v, (str, int, float)) and str(v).strip()), "")
        out.append({"text": clean(f"{tool} {hint}".strip(), STEP_CHARS), "tool": tool})
    return out[:MAX_PLAN]


def set_plan(card, steps, source):
    """Use these plan steps from here on. Runs that already happened tick the steps they match."""
    if card is None:
        return
    card["plan"] = [{"text": s["text"], "tool": s.get("tool", ""), "state": "pending", "result": ""}
                    for s in steps or [] if s.get("text")][:MAX_PLAN]
    card["source"] = source if card["plan"] and source in SOURCES else "none"
    for run in card["runs"]:
        run["step"] = _step_for(card, run["tool"]) if run["state"] != "skipped" else None
        if run["step"] is not None:
            _set_step(card, run)
    _touch(card)


def _step_for(card, tool):
    """The plan step a call of `tool` belongs to: the first unfinished step naming that tool; else the current
    step when it names no tool (plain words, an app); else none (an extra step not in the plan)."""
    for i, st in enumerate(card["plan"]):
        if st["tool"] == tool and st["state"] in ("pending", "started", "failed"):
            return i
    i = current(card)
    if i is not None and not card["plan"][i]["tool"]:
        return i
    return None


def current(card):
    """Index of the first plan step not done or skipped, or None (no plan, or all done)."""
    for i, st in enumerate((card or {}).get("plan") or []):
        if st["state"] not in ("done", "skipped"):
            return i
    return None


def _set_step(card, run):
    st = card["plan"][run["step"]]
    if st["state"] == "done" and run["state"] != "done":
        return                          # a later failed retry doesn't undo a step that really worked
    st["state"], st["result"] = run["state"], run.get("result", "")


# ------------------------------------------------------------------ runs (only code calls these)

def start(card, call):
    """A tool is about to run: record it as started, not confirmed. -> the run's index (or None without a card)."""
    if card is None:
        return None
    tool = str(call.get("name") or "")
    args = call.get("args") or {}
    hint = next((str(v) for v in args.values() if isinstance(v, (str, int, float)) and str(v).strip()), "") \
        if isinstance(args, dict) else ""
    run = {"tool": tool, "hint": clean(hint, 40), "state": "started", "result": "", "t": time.time(), "step": None}
    run["step"] = _step_for(card, tool)
    _add(card, run)
    card["open"].append(run["id"])
    if run["step"] is not None:
        _set_step(card, run)
    if card["status"] != "running":
        mark(card, "running")
    _touch(card)
    return run["id"]


def _add(card, run):
    run["id"] = card["next"] = card.get("next", 0) + 1
    card["runs"].append(run)
    if len(card["runs"]) > MAX_RUNS:
        del card["runs"][: len(card["runs"]) - MAX_RUNS]


def _run(card, rid):
    return next((r for r in card["runs"] if r["id"] == rid), None)


def finish(card, out, ok, tool=None, run=None):
    """An open run has really finished with result `out` (run = the id start() gave; None = the newest open run).
    ok=False -> failed; a "Not run" / "declined" result -> skipped. Does nothing when that run isn't open any more
    (already finished, or Stop left it unconfirmed). Runs nest: a skill's brain step runs inside run_skill."""
    if card is None or not card["open"]:
        return
    rid = card["open"][-1] if run is None else run
    if rid not in card["open"]:
        return
    card["open"].remove(rid)
    run = _run(card, rid)
    if run is None:
        return
    if tool and tool != run["tool"]:                # web_research ran as research_in_background: show what really ran
        run["tool"] = tool
    text = str(out or "")
    run["state"] = "skipped" if _SKIPPED_RE.search(text) else "done" if ok else "failed"
    run["result"] = clean((text.strip().splitlines() or [""])[0], RESULT_CHARS)
    if run["step"] is not None:
        if run["state"] == "skipped" and card["plan"][run["step"]]["state"] == "started":
            card["plan"][run["step"]]["state"] = "pending"        # it never ran: the step is still to do
        elif run["state"] != "skipped":
            _set_step(card, run)
    _touch(card)


def leave_open(card, run=None):
    """Stop pressed while a tool ran: the run stays *started, not confirmed* and is no longer open."""
    if card is not None and card["open"]:
        rid = card["open"][-1] if run is None else run
        if rid in card["open"]:
            card["open"].remove(rid)


def record(card, call, out, ok):
    """A step that started and finished in one go (a skill's own step, run by code): add it, done or failed."""
    if card is None:
        return
    rid = start(card, call)
    finish(card, out, ok, run=rid)


def mark(card, status, why=""):
    """The task's status changed (running / waiting / stopped / interrupted / done / failed / stuck)."""
    if card is None or status not in STATUSES:
        return
    card["status"], card["why"] = status, clean(why, 120) if why else ""
    _touch(card)


def _touch(card):
    card["updated"] = time.time()
    if on_change is not None:
        try:
            on_change(card)
        except Exception:
            pass        # intentional: showing the card must never break the task; app.resume logs its own errors


ACTIONS = {"running": ("stop",), "stopped": ("continue", "drop"), "interrupted": ("continue", "drop"),
           "stuck": ("drop",)}


def strip(card, runs=6):
    """What the live task strip shows (plain JSON, cleaned like the card): the one line, and the card behind it."""
    if card is None:
        return {}
    i = current(card)
    open_run = next((r for r in card["runs"] if r["id"] in card["open"]), None) if card["open"] else None
    now = (f"{open_run['tool']} {open_run['hint']}".strip() if open_run
           else card["plan"][i]["text"] if i is not None else "")
    acts = ACTIONS.get(card["status"], ())
    if "continue" in acts and not resumable(card):
        acts = ("drop",)
    return {"id": card["id"], "goal": card["goal"], "status": card["status"], "why": card["why"],
            "step": (i + 1) if i is not None else None, "steps": len(card["plan"]), "now": now,
            "source": card["source"], "started": card["started"], "updated": card["updated"],
            "done": sum(r["state"] == "done" for r in card["runs"]), "actions": list(acts),
            "plan": [{"text": st["text"], "state": st["state"], "result": st["result"]} for st in card["plan"]],
            "runs": [{"tool": r["tool"], "hint": r["hint"], "state": r["state"], "result": r["result"],
                      "check": r.get("check")} for r in card["runs"][-runs:]],
            "left": left(card), "not_checked": not_checked(card) and card["status"] == "done",
            "read_only": bool(card.get("read_only")),
            "known": [{"key": k["key"], "value": k["value"], "how": k["how"]} for k in card.get("known") or []][-10:],
            "asks": [{"text": a["text"], "state": a["state"]} for a in card.get("asks") or []
                     if a["state"] in ("open", "asked")]}


# ------------------------------------------------------------------ read-back checks (4c)

def set_check(card, rid, rb):
    """The read-back of run `rid` (nova/actions/readback.py): passed / failed / skipped, why, what it checked."""
    if card is None or rb is None:
        return
    run = next((r for r in card["runs"] if r.get("id") == rid), None)
    if run is not None:
        run["check"] = {"state": rb["state"], "why": clean(rb["why"], 120), "target": clean(rb.get("target"), 200)}
        if rb["state"] == "failed" and run.get("step") is not None:     # the plan step isn't done either
            card["plan"][run["step"]].update(state="failed", result=run["check"]["why"])
        _touch(card)


def failing(card):
    """What is still not true: the newest read-back per thing checked, where that one failed (a later step that put
    it right replaces an earlier failure)."""
    last = {}
    for r in (card or {}).get("runs", []):
        c = r.get("check")
        if c:
            last[c.get("target") or f"{r['tool']}#{r['id']}"] = c
    return [c["why"] for c in last.values() if c["state"] == "failed"]


def not_checked(card):
    """Steps changed things, and not one of them could be read back."""
    cs = [r["check"] for r in (card or {}).get("runs", []) if r.get("check")]
    return bool(cs) and all(c["state"] == "skipped" for c in cs)


def left(card):
    """The "couldn't finish" list: plan steps that never got done, and what the read-backs say is still not true."""
    steps = [(i, st) for i, st in enumerate((card or {}).get("plan", [])) if st["state"] not in ("done", "skipped")]
    out = [f"step {i + 1}: {st['text']}" + (f" ({st['result']})" if st["state"] == "failed" and st["result"] else "")
           for i, st in steps]
    said = {st["result"] for _, st in steps}
    return out + [w for w in failing(card) if w not in said]             # each problem once


# ------------------------------------------------------------------ what the brain sees

def _run_line(run):
    return f"{run['tool']} → {run['result']}" if run["result"] else run["tool"]


def render(card, limit=MAX_CHARS):
    """The card as the brain sees it, under `limit` characters. Empty without a card."""
    if card is None:
        return ""
    head = [f"TASK: {clean(card['goal'], 200)}"]
    if card["plan"]:
        steps = "  ".join(f"{i + 1} {st['text']} {ICONS[st['state']]}" for i, st in enumerate(card["plan"]))
        head.append(f"PLAN ({card['source']}): {steps}")
    unconfirmed = [r for r in card["runs"] if r["state"] == "started" and r["id"] not in card["open"]]
    failed = [r for r in card["runs"] if r["state"] == "failed"][-2:]
    tail = []
    if unconfirmed:
        tail.append("STARTED, NOT CONFIRMED: " + "; ".join(f"{r['tool']} {r['hint']}".strip() for r in unconfirmed[-2:]))
    if failed:
        tail.append("FAILED: " + "; ".join(_run_line(r) for r in failed))
    if failing(card):
        tail.append("NOT TRUE YET (read back by code): " + "; ".join(failing(card)[-3:]))
    i = current(card)
    if i is not None:
        tail.append(f"NOW: step {i + 1} - {card['plan'][i]['text']}")
    elif card["plan"]:
        tail.append("NOW: every plan step has really run - check the goal is reached, then tell the user.")
    tail += known.render(card)
    if card["status"] != "running":
        tail.append(f"STATUS: {card['status']}" + (f" ({card['why']})" if card["why"] else ""))
    done = [_run_line(r) for r in card["runs"] if r["state"] == "done"]
    fixed = len("\n".join(head + tail)) + 1
    shown, cut = list(done), 0
    while shown and fixed + len("DONE (really): " + "; ".join(shown)) + 16 > limit:
        shown.pop(0)                    # the oldest results go first
        cut += 1
    line = "DONE (really): " + ("; ".join(shown) if shown else "nothing yet")
    if cut:
        line += f" (+{cut} earlier)"
    text = "\n".join(head + [line] + tail)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def where(card):
    """'step 3 of 5 of <goal>' / '<goal>' - how she names a task out loud."""
    i = current(card)
    goal = card["goal"][:80]
    return f"step {i + 1} of {len(card['plan'])} of “{goal}”" if i is not None and card["plan"] else f"“{goal}”"


def summary(card):
    """A short dict for logs and (later) the strip: status, step N of M, the current step."""
    if card is None:
        return {}
    i = current(card)
    return {"goal": card["goal"], "status": card["status"], "why": card["why"], "source": card["source"],
            "steps": len(card["plan"]), "step": (i + 1) if i is not None else None,
            "now": card["plan"][i]["text"] if i is not None else "",
            "done": sum(r["state"] == "done" for r in card["runs"]),
            "secs": round(card["updated"] - card["started"], 1)}


def to_json(card):
    return json.dumps(card, ensure_ascii=False)
