"""Routine suggestions in the app (manager plan Phase 8, the safe part): the watcher, the card, and each answer.

The rules are nova/actions/suggest.py (pure). Here:

    tick(now)        every TICK seconds (watch): prepare-ahead for a routine coming up (level-0 reads only), then
                     at most one card: "Open your morning setup? (Gmail → Calendar → VS Code)"
                     Yes / Not now / Don't suggest this
    context()        is now a good moment? the user at the laptop, no task running or question open, nothing full
                     screen (Windows' own notification state, or the front window covering the monitor), no meeting
    run_routine()    Yes: the routine's words go through body.answer like the user's own request - the normal path
                     plans it, and every step asks by its risk level (4e). Nothing ran before the tap.

Prepared results stay in memory only (mail text is never written to disk here) and are shown to the user in the chat
when they say Yes; the brain doesn't get them.
"""
import datetime as dt
import time

from flask import jsonify

from nova import platform, protocol, runtime
from nova.actions import suggest
from nova.core.app import api
from nova.log import log_exception
from nova.platform import busy

TICK = 30
PRESENT_IDLE = 300          # the keyboard or mouse was touched in the last 5 minutes
OWNER = "routine"
NOVA_WINDOWS = ("Nova", "Nova 3D", "Nova ear", "tk")
prepared = {}               # routine id -> [(tool, result)], today only, in memory only
last = {"why": ""}          # why the last tick offered nothing (for the log, once per change)


# ------------------------------------------------------------------ the moment

def _front():
    """(hwnd, title, proc) of the user's front window (Nova's own don't count), or (0, "", "")."""
    d = platform.current()
    fg = d.foreground()
    for w in d.windows():
        if w.hwnd == fg and w.title not in NOVA_WINDOWS:
            return w.hwnd, w.title, w.proc
    return 0, "", ""


def _fullscreen(hwnd):
    """"full screen" / "a full-screen game" / "presentation mode", or ""."""
    state = busy.notification_state()
    if state and state != "away":
        return state
    if not hwnd:
        return ""
    d = platform.current()
    try:
        if d.is_maximized(hwnd) or d.class_name(hwnd) in ("Progman", "WorkerW"):
            return ""
        r, m = d.window_rect(hwnd), d.monitor_rect(hwnd)
        if r and m and r[0] <= m[0] and r[1] <= m[1] and r[2] >= m[2] and r[3] >= m[3]:
            return "full screen"
    except Exception:
        log_exception("suggesting: full-screen check")
    return ""


def context():
    from nova import task_state
    from nova.core.app import asking, background, integrations, session, voice_io
    hwnd, title, proc = _front()
    d = platform.current()
    present = (not integrations.user_away() and background.user_idle_secs() < PRESENT_IDLE and not d.screen_locked())
    card = task_state.of(session.state.get("task"))
    return {"present": present,
            "busy": bool(background.busy_now()) or bool(card and card.get("status") == "waiting"),
            "asleep": bool(voice_io.nap.get("asleep")),
            "question": asking.current() is not None or bool(session.state.get("pending")),
            "fullscreen": _fullscreen(hwnd), "title": title, "proc": proc}


# ------------------------------------------------------------------ the watcher

def tick(now=None):
    """One look. -> the card's reply dict when a suggestion was made, else None."""
    from nova.core.app import config, conversation
    s = config.settings()
    if not s.get("routine_suggest", True):
        return None
    now = now or dt.datetime.now()
    routines = suggest.found_routines(now)
    if not routines:
        return None
    if not suggest.quiet_now(s, now):
        state = suggest.load(now)
        for r in routines:
            if suggest.prepare_due(r, now, state):
                prepare(r, now)
    from nova.core.app import session
    if not session.starting.acquire(blocking=False):     # a scheduled run (4g) is starting: look again next tick
        return None
    try:
        r, why = suggest.pick(routines, now, context(), s, suggest.load(now))
        if r is None:
            if why != last["why"]:
                last["why"] = why
                conversation.task_log(f"SUGGEST routine: none ({why})")
            return None
        last["why"] = ""
        return offer(r, now)
    finally:
        session.starting.release()


def prepare(routine, now=None):
    """Run the routine's level-0 reads ahead of time (suggest.prepare_calls is the only list). -> [(tool, result)]."""
    from nova.actions import tools
    from nova.core.app import conversation
    out = []
    for tool, args in suggest.prepare_calls(routine):
        try:
            out.append((tool, str(tools.run_tool(tool, args))))
        except Exception:
            log_exception(f"suggesting: preparing {tool}")
    prepared[routine["id"]] = out
    suggest.mark_prepared(routine["id"], now)
    conversation.task_log(f"SUGGEST prepared ahead for {routine['name']!r}: {[t for t, _ in out]}")
    return out


def _ready_text(rid):
    lines = []
    for tool, res in prepared.pop(rid, []):
        if res.startswith(f"Error in {tool}"):
            continue
        blocks = protocol.untrusted_blocks(res)
        text = blocks[0][1] if blocks else res
        lines.append(f"📋 Ready ahead ({tool.replace('_', ' ')}):\n{text.strip()[:600]}")
    return "\n\n".join(lines)


def offer(routine, now=None):
    """Ask once: the question card (chat, strip, /tasks) and her voice."""
    from nova.core.app import asking, voice_io
    register()
    q = asking.question("go", suggest.question_text(routine), "one", choices=suggest.CHOICES, why=suggest.why(routine))
    now = now or dt.datetime.now()
    card = asking.make(OWNER, [q], data={"routine": routine["id"], "name": routine["name"],
                                         "request": suggest.request(routine), "at": now.timestamp()})
    suggest.mark_shown(routine["id"], now)
    out = asking.ask(card, "Suggested your routine", wait=False)
    out["say"] = f"{suggest.question_text(routine).split(' (')[0]} Yes, not now, or don't suggest this?"
    voice_io.push_event({**out, "kind": "suggestion"})
    voice_io.announce(out["say"])
    return out


def watch():
    while True:
        time.sleep(TICK)
        try:
            tick()
        except Exception:
            log_exception("suggesting: routine watch")


def start():
    register()
    runtime.spawn("suggesting.watch", watch)


# ------------------------------------------------------------------ the answers

def _out(line, activity):
    return {"reply": line, "say": line, "activity": [activity], "by_code": True}


def _answered(qcard, answers):
    a, d = answers["go"]["value"], qcard["data"]
    from nova.core.app import conversation
    suggest.answer(d["routine"], a, d.get("name", ""))
    conversation.task_log(f"SUGGEST routine {d['routine']!r}: {a}")
    if a == "yes":
        runtime.spawn("suggesting.run_routine", run_routine, args=(d["request"], d["routine"]))
        return _out(f"Okay: {d['request']}. Each step asks as usual.", "Starting your routine")
    if a == "later":
        return _out(f"Okay, not now. No suggestions for the next {suggest.SNOOZE_MIN} minutes.", "Not now")
    return _out("Okay, I won't suggest that one again. You can allow it again in ⚙ Senses.", "Won't suggest it")


def _dropped(qcard):
    """Expired unanswered: counted on the day it was offered (it may expire after midnight)."""
    at = qcard["data"].get("at")
    suggest.answer(qcard["data"]["routine"], "dropped", now=dt.datetime.fromtimestamp(at) if at else None)


def run_routine(request, rid=""):
    """Yes: the routine as the user's own request (waits for any request in progress to finish first)."""
    from nova.core.app import body, voice_io
    ready = _ready_text(rid)
    if ready:
        voice_io.push_event({"reply": ready, "kind": "suggestion"})
    voice_io.push_event({"me": request})
    out, _status = body.answer(request)
    voice_io.push_event(dict(out))
    return out


def register():
    from nova.core.app import asking
    asking.handler(OWNER, _answered, _dropped)


# ------------------------------------------------------------------ ⚙ Senses

@api.app.get("/api/suggestions")
def suggestions_get():
    from nova.core.app import config
    s = config.settings()
    return jsonify({**suggest.summary(), "on": bool(s.get("routine_suggest", True)),
                    "max": int(s.get("routine_suggest_max", 2)), "quiet": s.get("routine_quiet", ""),
                    "routines": [{"id": r["id"], "name": r["name"], "chain": suggest.chain(r),
                                  "time": f"{r['minute'] // 60}:{r['minute'] % 60:02d}"}
                                 for r in suggest.found_routines()]})


@api.app.post("/api/suggestions")
def suggestions_post():
    """⚙ Senses: on / max / quiet ("22:00-07:00" or ""), snooze (minutes), allow (an id, or "all")."""
    from nova.core.app import config
    b = api._body()
    try:
        s = config.settings()
        if "on" in b:
            s["routine_suggest"] = b["on"] is True
        if "max" in b:
            s["routine_suggest_max"] = min(max(int(b["max"]), 0), 10)
        if "quiet" in b:
            q = str(b["quiet"] or "").strip()
            if q and not suggest.valid_quiet(q):
                return jsonify({"error": "Quiet hours look like 22:00-07:00 (or leave it empty)."}), 400
            s["routine_quiet"] = q
        if {"on", "max", "quiet"} & set(b):
            config.save_settings(s)
        if "snooze" in b:
            suggest.snooze(int(b["snooze"]))
    except (TypeError, ValueError):
        return jsonify({"error": "max and snooze take whole numbers"}), 400
    if "allow" in b:
        suggest.allow_again(None if b["allow"] == "all" else str(b["allow"]))
    return suggestions_get()
