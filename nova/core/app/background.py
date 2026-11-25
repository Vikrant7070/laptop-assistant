"""What runs on its own: suggestions, idle / presence / help watchers, quitting, and the live pulse."""


import json
import os
import queue
import re
import time

from nova import platform
from nova import db, runtime
from flask import Response, jsonify, request, send_from_directory

from nova import prompts as promptfiles
from nova.avatar import avatar_client
from nova.actions import coworker
from nova.brain import llm
from nova.actions import mcp
from nova.actions import tools
from nova.log import log_exception
from nova.core.app import api, eventbus


SUGGEST_EVERY = 600            # at most one suggestion every 10 minutes


# The whole prompt goes to the vision model as written (kind="check"). Asking first WHAT they're doing makes the small
# model's tip specific; with a bare "or answer NONE" escape it answered NONE every time.
SUGGEST_PROMPT = promptfiles.load("suggest")


suggest_state = {"last": 0.0, "title": "", "running": False}




def user_idle_secs():
    """Seconds since the user last touched the keyboard or mouse (nova.platform)."""
    return platform.current().idle_seconds()


NOVA_WINDOWS = ("Nova", "Nova 3D", "Nova ear", "tk")


def _front_title():
    """The user's front-most window (Nova's own windows - her body, chat, ear - don't count)."""
    for _, title, proc in tools._windows():
        if title not in NOVA_WINDOWS and proc.lower() not in ("textinputhost.exe", "shellexperiencehost.exe"):
            return title
    return ""


def suggest_now(force=False):
    """Look at the screen and offer one suggestion. Returns it (or None). The picture stays on this laptop: only the
    local vision model sees it, never a cloud brain."""
    if suggest_state["running"] or not tools._screen_allowed():
        return None
    title = _front_title()
    if not force and (not title or
                      (title == suggest_state["title"] and time.time() - suggest_state["last"] < 1800)):
        return None
    suggest_state.update(running=True, last=time.time(), title=title)
    try:
        img = tools._grab_screen_jpeg()
        text = llm.see(img, SUGGEST_PROMPT, preferred=config.settings()["model"], kind="check").strip()
    finally:
        suggest_state["running"] = False
    line = re.search(r"suggestion\s*:\s*(.+)", text, re.I)
    if line:
        text = line.group(1).strip()
    elif re.search(r"working on\s*:", text, re.I):
        text = ""                                    # only the first line came back: nothing to suggest
    text = re.sub(r"^\W*(suggestion|tip)\s*:\s*", "", text, flags=re.I).strip()
    if not text or re.match(r"^\W*none\b", text, re.I) or len(text) < 8:
        conversation.task_log(f"SUGGEST: nothing for '{title[:60]}'")
        return None
    text = text[:220]
    conversation.task_log(f"SUGGEST for '{title[:60]}': {text}")
    voice_io.push_event({"reply": "💡 " + text, "kind": "suggestion"})
    avatar_client.send("say", text="💡 " + text, secs=min(14, 4 + len(text) / 15), timeout=2)
    return text


def suggest_watch():
    while True:
        time.sleep(30)
        try:
            s = config.settings()
            if (s.get("suggest") and s["screen"] and not voice_io.nap["asleep"] and not session.lock.locked() and not voice_io.mouth.busy()
                    and time.time() - suggest_state["last"] >= SUGGEST_EVERY and user_idle_secs() < 90):
                suggest_now()
        except Exception as e:
            conversation.task_log(f"suggest watch: {type(e).__name__}: {e}")


def busy_now():
    """Is she working on something (a request, or a task in the background)? Her body shows it: at her desk."""
    return session.lock.locked() or voice_io.convo["thinking"] or coworker.running()


def work_watch():
    """Tells 3D Nova when she starts and stops being busy, so she sits at her laptop while working."""
    last = None
    while True:
        try:
            busy = bool(busy_now())
            if busy != last and avatar_client.send("work", on=busy, timeout=2) is not None:
                last = busy
        except Exception as e:
            conversation.task_log(f"work watch: {type(e).__name__}: {e}")
        time.sleep(0.4)


_background = {"started": False}


def start_background():
    """The things that run while Nova lives in the tray: nap clock, suggestions, notifications.
    Started by the running app (desktop.py / __main__), never just by importing this module."""
    if _background["started"]:
        return
    _background["started"] = True
    runtime.spawn("background.idle_watch", voice_io.idle_watch)
    runtime.spawn("background.suggest_watch", suggest_watch)
    integrations.phone.start()                               # only if a bot token is saved
    if config.settings().get("mobile"):
        integrations.mobile.start()                          # the iPhone web app's server (127.0.0.1 only)
    mcp.start()                                 # 🔌 connectors in data/mcp.json, each on its own thread
    runtime.spawn("background.presence_watch", integrations.presence_watch)
    runtime.spawn("background.activity_watch", privacy.activity_watch)   # off unless ticked in ⚙ Privacy
    runtime.spawn("background.work_watch", work_watch)
    runtime.spawn("background.help_watch", integrations.help_watch)
    if config.settings()["notifications"]:
        integrations.notif_watcher.start()
    runtime.spawn("background.sync_phone_banners", integrations.sync_phone_banners)
    runtime.spawn("background.pulse_watch", pulse_watch)
    runtime.spawn("background.resume_offer", resume.offer_after_start)    # chat back + "should I continue?"
    suggesting.start()                                      # Phase 8: "open your morning setup?" at the right moment
    runtime.spawn("background.schedule_watch", scheduling.watch, delay=scheduling.TICK, every=scheduling.TICK)   # 4g


@api.app.get("/ear")
def ear_page():
    return send_from_directory(api.app.static_folder, "ear.html")


@api.app.get("/api/ear")
def ear_poll():
    cmds = voice_io.ear_cmds[:]
    voice_io.ear_cmds.clear()
    return jsonify(cmds=cmds, convo=voice_io.convo["on"], thinking=voice_io.convo["thinking"], speaking=voice_io.mouth.busy())


@api.app.post("/api/ear_said")
def ear_said():
    text = api._text(api._body(), "text")[:2000]
    if text:
        runtime.spawn("background.heard", voice_io.heard, args=(text,))
    return jsonify(ok=True)


@api.app.post("/api/ear_idle")
def ear_idle():
    """The bubble heard nothing for a while."""
    if voice_io.convo["on"] and time.time() - max(voice_io.convo["last_heard"], voice_io.nap["last_task"]) < voice_io.IDLE_NAP:
        voice_io.ear_listen()                      # keep listening
    elif voice_io.convo["on"]:
        voice_io.go_to_sleep("idle")               # 5 minutes without a task: nap
    else:
        voice_io.ear_close()
    return jsonify(ok=True)


@api.app.post("/api/chat")
def chat():
    text = api._text(api._body(), "text")[:4000]
    if not text:
        return jsonify(error="empty message"), 400
    voice_io.mouth.stop()  # don't talk over the user
    if voice_io.wants_sleep(text):
        voice_io.go_to_sleep("asked")
        return jsonify(reply=f"Okay, I'll take a nap in the corner 😴 Message me or say “{prompts.wake_phrase()}” when you need me.",
                       activity=[])
    if integrations.help_first():
        helped = integrations.take_help(text)
        if helped is not None:
            return jsonify(helped)
    said = learning.take_question(text)
    if said is None:
        said = integrations.take_offer(text)
    if said is not None:
        voice_io.announce(said)
        return jsonify(reply=said, activity=[])
    out, status = body.answer(text)
    return jsonify(out), status


@api.app.post("/api/offer")
def offer_answer():
    """The chat page's "Show it" / "Not now" buttons under a finished background job."""
    body = api._body()
    yes = body.get("show") is True
    job_id = body.get("id")
    said = integrations.take_offer("", force=yes, job_id=job_id if isinstance(job_id, int) else None)
    return jsonify(reply=said or "That one isn't waiting any more.", activity=[])


@api.app.post("/api/suggest")
def suggest_api():
    """💡 button: a suggestion about what's on the screen right now."""
    if not tools._screen_allowed():
        return jsonify(reply="I need permission to look at your screen first (⚙ Model → Screen).", activity=[])
    voice_io.note_task()
    try:
        tip = suggest_now(force=True)
    except Exception as e:
        return jsonify(error=f"Couldn't look at the screen: {e}"), 500
    return jsonify(reply=None if tip else "I looked - nothing to suggest right now, you're doing great! 👍",
                   activity=["Looked at your screen"], shown=bool(tip))


@api.app.post("/api/confirm")
def confirm():
    out, status = voice_io.confirm_pending(api._body().get("approved") is True)
    return jsonify(out), status


@api.app.get("/api/events")
def get_events():
    """The polling fallback for chat events (when /api/stream can't be used). after: the last event id this page has;
    without it, the events no page has seen yet. Nothing is cleared, so two open pages each get every event."""
    after = request.args.get("after", type=int)
    if after is not None and after > eventbus.bus.last_id:
        after = None                                  # Nova restarted: her event ids began again
    evs = eventbus.bus.unseen("event") if after is None else eventbus.bus.since(after, {"event"})
    for ev in evs:
        ev["seen"] = True
    return jsonify(events=[ev["data"] for ev in evs], last=eventbus.bus.last_id, **voice_state())


def voice_state():
    """What a page needs to draw her right now; sent first on every stream, then kept up to date by events."""
    return dict(speaking=voice_io.mouth.lips, ears=voice_io.ears.status,
                heard=voice_io.ears.heard[-1:] if config.settings()["wake"] else [], wake_phrase=prompts.wake_phrase(),
                busy=session.lock.locked(), thinking=voice_io.convo["thinking"])


def _sse(kind, data, ev_id=None):
    return (f"id: {ev_id}\n" if ev_id is not None else "") + f"event: {kind}\ndata: {json.dumps(data)}\n\n"


def stream_events(q, first, backlog=()):
    """The body of one /api/stream response: the state now, then each event as it is published, and a comment line
    every HEARTBEAT seconds so a closed page is noticed (the write fails) and its queue let go."""
    try:
        yield "retry: 3000\n\n" + _sse("state", first)
        while True:
            try:
                ev = q.get(timeout=eventbus.HEARTBEAT)
            except queue.Empty:
                if q.dropped:
                    return
                yield ": ping\n\n"
                continue
            if ev["kind"] in backlog:
                ev["seen"] = True            # a page that opens later doesn't get it again
            yield _sse(ev["kind"], ev["data"], ev["id"])
            if q.dropped and q.empty():
                return                      # too slow: the browser reconnects with Last-Event-ID and catches up
    finally:
        eventbus.bus.unsubscribe(q)


@api.app.get("/api/stream")
def stream():
    """Server-Sent Events: everything the pages show, pushed as it happens (roadmap R9). The browser's own reconnect
    sends Last-Event-ID, and the events after it are replayed. backlog=event,ear: events of those kinds that no page
    asking for them has seen yet (the chat's events and the ear bubble's commands used to wait in a queue)."""
    after = request.headers.get("Last-Event-ID", type=int)
    if after is None:
        after = request.args.get("after", type=int)
    if after is not None and after > eventbus.bus.last_id:
        after = None                                  # Nova restarted: her event ids began again
    backlog = tuple(k for k in request.args.get("backlog", "").split(",") if k)
    q = eventbus.bus.subscribe(after, backlog=backlog)
    return Response(stream_events(q, voice_state(), backlog), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


quit_hook = None          # set by desktop.py: closes the windows, tray, 3D Nova and the server


def quit_nova():
    """Shut Nova down completely (not the laptop): ears, notifications, phone, connectors, 3D Nova, then the process."""
    conversation.task_log("QUIT (asked)")
    for f in (conversation.stop_now, voice_io.mouth.stop, voice_io.ears.stop, integrations.notif_watcher.stop, integrations.mobile.stop, voice_io.ear_close):
        try:
            f()
        except Exception:
            pass    # intentional: quit continues past a part that is already stopped
    avatar_client.send("quit", timeout=2)
    if quit_hook:
        try:
            quit_hook()
        except Exception:
            pass    # intentional: window/tray teardown is best effort
    runtime.spawn("background.exit_soon", lambda: os._exit(0), delay=3, daemon=False)   # whatever is still running (phone, connectors) stops with us
    left = runtime.stop_all(wait=1.0)                  # loops started with every= / restart= end here
    if left:
        conversation.task_log(f"QUIT: still running after 1 s: {', '.join(left[:12])}")
    db.close()                                         # a clean checkpoint of nova.db before we go


@api.app.get("/api/threads")
def threads_api():
    """Debug: Nova's background threads (name, age in s, runs, last error), oldest first."""
    return jsonify(threads=runtime.threads())


@api.app.get("/api/voice_machine")
def voice_machine_api():
    """Debug (R10 shadow week): the voice state machine now, its last transitions, and every time it disagreed with
    the old flags or got an event its table doesn't allow. Empty `mismatches` and `illegal` = ready to switch."""
    m = voice_io.machine
    return jsonify(state=m.state, mode=m.mode, ears=m.ears,
                   history=[{"at": t, "event": e, "from": a, "to": b} for t, e, a, b in m.history[-20:]],
                   mismatches=db.find_events(kind="voice_mismatch", limit=50),
                   illegal=db.find_events(kind="voice_illegal", limit=50))


@api.app.post("/api/quit")
def quit_api():
    """The chat's ⏻ Quit button."""
    runtime.spawn("background.quit_nova", quit_nova, delay=0.5, daemon=False)            # answer the page first
    return jsonify(ok=True)


@api.app.post("/api/stop")
def stop():
    """The Stop switch: ends the task she's working on (and her voice). Safe to press any time."""
    return jsonify(ok=True, was_busy=conversation.stop_now())


@api.app.get("/api/thoughts")
def get_thoughts():
    """Her thoughts after id `after` (the chat's live "💭 thinking" log). Without `after`: just where the list ends,
    so a freshly opened chat starts from now instead of replaying old requests."""
    with session._thought_lock:
        last = session.thoughts["next"] - 1
        after = request.args.get("after", type=int)
        new = [] if after is None else [t for t in session.thoughts["list"] if t["id"] > after]
    return jsonify(thoughts=new, last=last, busy=session.lock.locked())


@api.app.post("/api/voice_stop")
def voice_stop():
    voice_io.mouth.stop()
    return jsonify(ok=True)


@api.app.post("/api/reset")
def reset():
    with session.lock:
        session.state["messages"].clear()
        session.state["pending"] = None
    resume.clear_chat()
    return jsonify(ok=True)


_alerts = {"battery": False, "cpu_since": None}


PULSE_EVERY = 15


def pulse_watch():
    """The pulse, pushed to the pages every PULSE_EVERY seconds (they used to ask for it), so reminders and the
    battery / CPU warnings come on time even with no page open."""
    while True:
        time.sleep(PULSE_EVERY)
        try:
            eventbus.publish("pulse", pulse_now())
        except Exception:
            log_exception("pulse")


@api.app.get("/api/pulse")
def pulse():
    """Live stats + things Nova should say on its own (the page asks once when it opens; then the stream)."""
    return jsonify(pulse_now())


def pulse_now():
    """Live stats + things Nova should say on its own; the lines are said here, and shown by the pages."""
    s = tools.stats()
    say = [f"Hey! Reminder: {r}" for r in tools.due_reminders()]
    if s["battery"] is not None and s["battery"] <= 20 and not s["plugged"]:
        if not _alerts["battery"]:
            say.append(f"I'm getting sleepy - battery is at {s['battery']}%. Could you plug me in?")
            _alerts["battery"] = True
    elif s["plugged"]:
        _alerts["battery"] = False
    if s["cpu"] > 90:
        _alerts["cpu_since"] = _alerts["cpu_since"] or time.time()
        if time.time() - _alerts["cpu_since"] > 60:
            say.append("I'm working really hard - CPU has been above 90% for a minute. Want me to find what's hogging it?")
            _alerts["cpu_since"] = time.time() + 600  # don't nag for 10 min
    else:
        _alerts["cpu_since"] = None
    for line in say:
        voice_io.announce(line)
        integrations.tell_away(line)
    return {"stats": s, "say": say}


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import body, config, conversation, integrations, learning, privacy, prompts, resume, scheduling, session, suggesting, voice_io  # noqa: E402
