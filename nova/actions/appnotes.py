"""App notes (manager plan 4f): what Nova has learned about each app from her own finished tasks.

Nothing is stored here. The notes are read from the one record of each task - its episode on the task card's row
(learner.py, db.py) - so /tasks, the Learner and the notes never disagree:

    APP NOTES (notepad, 2 earlier runs, 2 worked, about 25 s) Worked: press_keys ctrl+s; type_text.
    Didn't work: click_text "Save as". Got in the way: cookie banner (1x).

- Which app: the one the request named ("type hello in notepad", tools.named_app), else the first app the task
  opened (open_app's name).
- Controls that worked / didn't: the task's own steps in that app (tool + its short main argument), counted over
  the app's recent runs. Opening the app itself isn't a control. Secret-looking values were hidden in the episode
  and aren't shown.
- What got in the way: words in the steps' results - a sign-in wall, a cookie banner, a slow load (still loading,
  timed out, not responding). Found by `blocked(trace)` when the episode is made.
- How long it took: the episodes' time from start to the last step.

`line(app)` is what goes on a new task's card for that app (task_state.attach, or when the task opens the app) and
into the deep brain's planning request, so an app task's plan starts from what happened last time.
"""
import re

from nova.log import log_exception

MAX_RUNS = 8                 # the app's newest runs that count
LINE_CHARS = 260             # the notes' line on the card (~65 tokens)
OBSTACLES = {
    "sign-in wall": re.compile(r"\b(sign[ -]?in|log[ -]?in|signin|login)\b.{0,40}\b(to continue|required|page|wall|first)\b"
                               r"|\b(please|you must|you need to) (sign|log) ?in\b|\bnot signed in\b", re.I),
    "cookie banner": re.compile(r"\bcookies?\b.{0,60}\b(accept|consent|agree|allow|banner|reject)\b"
                                r"|\b(accept|allow|reject) (all )?cookies\b", re.I),
    "slow load": re.compile(r"\b(still loading|is loading|took too long|timed? ?out|not responding|wasn'?t ready yet"
                            r"|didn'?t (appear|open) in time)\b", re.I),
}
# Steps that are about the app's window, not a control inside it.
_NOT_CONTROLS = {"open_app", "window_action", "list_windows", "close_app", "get_screen_text", "look_at_screen"}
_MAIN_ARG = ("keys", "text", "label", "name", "item", "target", "query", "url", "field")


def app_of(task):
    """The app a task was about: the one its request named, else the first app it opened. "" for none."""
    app = str((task or {}).get("app") or "").strip().lower()
    if app:
        return app
    for st in (task or {}).get("trace") or (task or {}).get("steps") or []:
        if st.get("tool") == "open_app" and st.get("ok", True):
            name = str((st.get("args") or {}).get("name") or "").strip().lower()
            if name:
                return name[:40]
    return ""


def blocked(trace):
    """What got in the way, from the steps' results: ['cookie banner', ...] (each once, in the order met)."""
    out = []
    for st in trace or []:
        text = str(st.get("out") or "")
        for name, rx in OBSTACLES.items():
            if name not in out and rx.search(text):
                out.append(name)
    return out


def control(step):
    """'press_keys ctrl+s' / 'click_text "Save as"' / 'type_text' - one step as a control, or "" when it isn't one."""
    tool = step.get("tool") or ""
    return "" if not tool or tool in _NOT_CONTROLS else step_text(step)


def step_text(step):
    """Any step in a few words: 'open_app notepad', 'press_keys ctrl+s', 'type_text' (typed words aren't kept)."""
    tool = step.get("tool") or ""
    args = step.get("args") or {}
    if tool in ("type_text", "fill_in", "set_clipboard"):      # what was typed is content, not the control
        return tool
    for k in _MAIN_ARG:
        v = args.get(k)
        if isinstance(v, str) and v.strip() and v != "[hidden]":
            v = " ".join(v.split())[:30]
            return f'{tool} "{v}"' if " " in v or tool.startswith("click") else f"{tool} {v}"
    return tool


def notes(app, episodes=None):
    """What the app's recent runs say, or None when there are none: runs, worked, secs, controls that worked,
    controls that didn't, obstacles {name: times}."""
    app = str(app or "").strip().lower()
    if not app:
        return None
    from nova.actions import learner
    eps = episodes if episodes is not None else learner.load()
    mine = [e for e in eps if str(e.get("app") or "").lower() == app][-MAX_RUNS:]
    if not mine:
        return None
    good, bad, got_in_way = {}, {}, {}
    for e in mine:
        for st in e.get("steps") or []:
            c = control(st)
            if c:
                side = good if st.get("ok") else bad
                side[c] = side.get(c, 0) + 1
        for b in e.get("blocked") or []:
            got_in_way[b] = got_in_way.get(b, 0) + 1
    secs = [float(e.get("secs") or 0) for e in mine if e.get("secs")]
    return {"app": app, "runs": len(mine), "worked": sum(1 for e in mine if learner.worked(e)),
            "secs": round(sum(secs) / len(secs)) if secs else None,
            "controls": sorted(good, key=lambda c: -good[c]),
            "failed": sorted((c for c in bad if bad[c] > good.get(c, 0)), key=lambda c: -bad[c]),
            "blocked": got_in_way}


def describe(n):
    """The notes as one line (the card, the planner, /tasks)."""
    if not n:
        return ""
    head = f"APP NOTES ({n['app']}, {n['runs']} earlier run{'s' if n['runs'] != 1 else ''}, {n['worked']} worked"
    head += f", about {n['secs']} s)" if n["secs"] else ")"
    parts = []
    if n["controls"]:
        parts.append("Worked: " + "; ".join(n["controls"][:4]))
    if n["failed"]:
        parts.append("Didn't work: " + "; ".join(n["failed"][:3]))
    if n["blocked"]:
        parts.append("Got in the way: " + ", ".join(f"{b} ({k}x)" for b, k in n["blocked"].items()))
    text = head + (" " + ". ".join(parts) + "." if parts else " Nothing special noted.")
    return text if len(text) <= LINE_CHARS else text[: LINE_CHARS - 1].rstrip() + "…"


def line(app):
    """The app's notes line for a new task, or "". Never raises: a task starts without notes rather than not at all."""
    try:
        return describe(notes(app))
    except Exception:
        log_exception("app notes")
        return ""
