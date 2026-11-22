"""Co-worker jobs, notifications, the phone (Telegram and the iPhone app), Gmail, presence, the error-box watcher and connectors."""


import json
import re
import time

from nova import runtime
from flask import jsonify, request

from nova.brain import cloud
from nova.actions import coworker
from nova.actions import knowledge
from nova.brain import llm
from nova.actions import mcp
from nova.actions import gmail
from nova.actions import notifications
from nova.actions import phone as phone_link
from nova.core import mobile as mobile_mod
from nova.actions import presence
from nova.actions import watcher
from nova.audio import sfx
from nova.actions import learner
from nova.actions import workers
from nova.brain import toolpick
from nova.actions import tools
from nova import intents
from nova import protocol
from nova.core.app import api


offer = {"job": None, "until": 0.0}      # the "want to see it?" she asked last - a bare "yes" answers this one


offers_waiting = []                      # earlier finished jobs whose offer wasn't answered yet: (job, until)


OFFER_SECS = 900


def write_text(prompt, max_tokens=700, local=False):
    """Her brain as a writer (emails, documents) - no tools, a longer answer than a spoken reply.
    local: the laptop's own brain only (⚙ Workers: research reports may not go to the cloud brain)."""
    s = config.settings()
    msgs = [{"role": "system", "content": "You are a skilled writer helping the user. Follow the requested format exactly."},
            {"role": "user", "content": prompt}]
    model = s["model"] if local else config.brain(s)
    _, text, _ = llm.chat(model, msgs, [], fallback=None if local else s["model"], num_predict=max_tokens)
    return text


def job_done(job):
    voice_io.nap["last_task"] = time.time()
    try:
        learner.job_finished(job)            # the episode that started it, and the sites research really used
    except Exception as e:
        conversation.task_log(f"LEARNER job note failed: {type(e).__name__}: {e}")
    if job["status"] == "stopped":                  # the user stopped it - stop_background_job already said so
        return
    if job["status"] != "done":
        line = f"Sorry, the {job['kind'].replace('_', ' ')} didn't work: {job['error']}"
        voice_io.push_event({"reply": line, "kind": "job"})
        voice_io.announce(line)
        tell_away(line + _phone_hint(job, failed=True) + TASKS_LINE)
        return
    voice_io.wake_body()
    if offer["job"] and offer["job"] is not job and time.time() < offer["until"]:
        offers_waiting.append((offer["job"], offer["until"]))     # two jobs done close together: keep both
    offer.update(job=job, until=time.time() + OFFER_SECS)
    line = job["result"] + " Want to see it?"
    voice_io.push_event({"reply": "✅ " + line, "kind": "job", "offer": {"id": job["id"], "label": "Show it"}})
    voice_io.announce("✅ " + line, spoken=line)
    tell_away("✅ " + job["result"] + (_phone_hint(job) or " It's on the laptop for when you're back.") + TASKS_LINE)
    body.react_later("happy")
    if config.settings()["wake"] and not voice_io.convo["on"] and voice_io.silent_ok():     # listen for the "yes"
        voice_io.listen_state["single"] = True
        runtime.spawn("integrations.silent_loop", voice_io.silent_loop)


TASKS_LINE = "\nSee it on the Tasks page (📋 in Nova's window or the iPhone app)."


def _phone_hint(job, failed=False):
    """What the user can reply from Telegram about a coding or research job (coding replies still ask Yes/No)."""
    if job.get("kind") == "research":
        return ("\nReply \"try again\" to run the research again." if failed
                else "\nReply \"send the report\" to get the findings and sources here.")
    if job.get("kind") != "coding":
        return ""
    if failed:
        return ("\nReply \"try Claude Code\" to give it to Claude Code (paid - I'll ask first)."
                if job.get("worker") != "claude" else "")
    if job.get("files"):
        return ("\nReply \"what changed?\" to see the diff, or \"commit it\" to commit (I'll ask first, "
                "nothing is pushed).")
    return ""


def _next_offer():
    """After one offer is answered, ask about the next finished job still waiting. Returns the question, or ""."""
    now = time.time()
    offers_waiting[:] = [(j, u) for j, u in offers_waiting if u > now and j is not offer["job"]]
    if offer["job"] and offer["until"] > now:
        return ""
    if not offers_waiting:
        offer.update(job=None, until=0.0)
        return ""
    job, until = offers_waiting.pop()
    offer.update(job=job, until=until)
    return f" Your {job['kind'].replace('_', ' ')} '{job['title']}' is ready too - want to see it?"


def take_offer(text, force=None, job_id=None):
    """'yes' / 'no' right after "want to see it?". Returns her answer, or None if this isn't about the offer.
    job_id: the chat page's button names its own job (several can be waiting)."""
    now = time.time()
    if job_id is not None:
        live = ([(offer["job"], offer["until"])] if offer["job"] else []) + offers_waiting
        job = next((j for j, u in live if j.get("id") == job_id and u > now), None)
    else:
        job = offer["job"] if offer["until"] > now else None
    if not job:
        return None
    clean = config._unspoken(text).strip()
    short = len(clean.split()) <= 8
    yes = force is True or (force is None and short and intents.SHOW.search(clean))
    no = force is False or (force is None and short and intents.NO.search(clean) and not yes)
    if not (yes or no):
        return None
    if offer["job"] is job:
        offer.update(job=None, until=0.0)
    offers_waiting[:] = [(j, u) for j, u in offers_waiting if j is not job]
    voice_io.note_task()
    if yes:
        session.think(f"🪟 Bringing '{job['title']}' to the front.")
        return coworker.show(job) + _next_offer()
    return "Okay! It's waiting for you whenever you want it." + _next_offer()


coworker.configure(write=write_text, on_done=job_done, on_note=lambda t: session.think(t, cloud_too=False),
                   write_local=lambda prompt, n: write_text(prompt, n, local=True),
                   sites=lambda topic: learner.sites_for(topic)[:2])


SECRETY = re.compile(r"\b(otp|one[- ]time|code|password|passcode|pin|verification|cvv)\b", re.I)


def _hide_codes(line):
    if SECRETY.search(line):
        line = re.sub(r"\b[\dA-Z]{4,8}\b(?=[^\w]|$)", "(hidden)", line)
    return line


def _speakable(n):
    """A notification as she says it: one-time codes and passwords are never read out."""
    return _hide_codes(notifications.describe(n, 200))


# The iPhone's notifications arrive through Phone Link (notifications.py). Her BRAIN decides what each one is (a ringing
# call, a missed call, a message) and how to say it - "Rahul is calling you" / "a call from an unknown number" - so
# there are no names or phrases in code. Only the local brain sees them unless ⚙ allows the cloud one.
PHONE_NOTE_SECS = 15       # how long the brain gets before the plain text is used (a cold 4B model took >8 s live;


PHONE_NOTE_MAX = 4         # at most this many per burst go to the brain; the rest are read as they are


PHONE_CALL_WAIT = 2        # a ringing call waits at most this long for her own voice to finish


phone_seen = {}            # notification id -> when: the same toast is not announced twice


def sync_phone_banners():
    """⚙ "Hide Phone Link pop-ups": while she reads notifications, Phone Link's own banners are switched off so only
    she announces them. Turned back on when either switch goes off - but only if she was the one who hid them."""
    s = config.settings()
    hide = bool(s["notifications"] and s.get("phone_quiet", True))
    if not hide and not s.get("phone_banners_hidden"):
        return
    try:
        n = notifications.phone_banners(show=not hide)
    except Exception as e:
        conversation.task_log(f"PHONE BANNERS: couldn't change ({type(e).__name__}: {e})")
        return
    if n:
        conversation.task_log(f"PHONE BANNERS: {'hidden' if hide else 'shown again'} for {n} Phone Link source(s)")
    if bool(s.get("phone_banners_hidden")) != hide:
        s["phone_banners_hidden"] = hide
        config.save_settings(s)


def say_only(line):
    """Out loud only - no chat line and no bubble (iPhone notifications)."""
    if config.settings()["voice"] and line:
        voice_io.mouth.say(line, show=False)


def ring_cue():
    """An instant ding when something from the iPhone arrives - the brain and the voice need a few seconds."""
    try:
        if sfx.play("happy"):
            voice_io.mouth.hold(sfx.length("happy") + 0.3)         # keep the mic from hearing it
    except Exception:
        pass        # intentional: a missing reaction sound must not affect the answer


def phone_note(n):
    """(kind, sentence) for one iPhone notification: "call", "missed_call", "message" or "other"."""
    s = config.settings()
    fallback = _speakable(n)
    model = config.brain(s) if s.get("phone_notes_cloud") else s["model"]
    msgs = [{"role": "system", "content": notifications.PHONE_PROMPT},
            {"role": "user", "content": notifications.phone_prompt(n)}]
    box = {}

    def work():
        try:
            box["text"] = llm.chat(model, msgs, [], fallback=s["model"], num_predict=60)[1]
        except Exception as e:
            conversation.task_log(f"PHONE NOTE: brain failed ({type(e).__name__}: {e})")

    t0 = time.time()
    th = runtime.spawn("integrations.work", work, start=False)
    th.start()
    th.join(PHONE_NOTE_SECS)
    conversation.task_log(f"PHONE NOTE: arrived {max(0, t0 - (n.get('time') or t0)):.1f}s before the brain started, brain took {time.time() - t0:.1f}s")
    if th.is_alive():
        conversation.task_log(f"PHONE NOTE: brain took longer than {PHONE_NOTE_SECS} s - reading the plain text")
    kind, say = notifications.parse_phone_note(box.get("text"), fallback)
    return kind, _hide_codes(say)


def on_notifications(items):
    s = config.settings()
    if not s["notifications"] or not items:
        return
    items = [n for n in items if not notifications.is_muted(n, s.get("notif_muted"))]   # "don't read Claude's"
    if not items:
        return
    now = time.time()
    if any(notifications.is_phone(n) and n.get("id") not in phone_seen for n in items) and s.get("phone_calls", True) and (
            not voice_io.nap["asleep"] or s.get("notif_sleep", False) or voice_io.nap.get("reason") != "asked"):
        ring_cue()          # the moment it arrives: a sound now, her words follow once the brain and the voice are ready
    if any(notifications.is_phone(n) for n in items) and s.get("phone_quiet", True) and s.get("phone_banners_hidden"):
        runtime.spawn("integrations.phone_banners", notifications.phone_banners, args=(False,))   # a new iPhone app gets a new Windows key
    for k in [k for k, t in phone_seen.items() if now - t > 60]:
        phone_seen.pop(k, None)
    lines, calls, phone_lines, missed, brained = [], [], [], [], 0
    for n in items:
        if notifications.is_phone(n):
            if n.get("id") in phone_seen:
                continue
            phone_seen[n.get("id")] = now
            if brained < PHONE_NOTE_MAX:
                kind, say = phone_note(n)
                brained += 1
            else:
                kind, say = "other", _speakable(n)
            if kind in ("call", "missed_call") and not s.get("phone_calls", True):
                continue                                  # ⚙ "tell me about phone calls" is off
            conversation.task_log(f"NOTIFICATION from {n['app']} ({kind})")
            (calls if kind == "call" else phone_lines).append(say)
            if kind == "missed_call":
                missed.append(say)
        else:
            conversation.task_log(f"NOTIFICATION from {n['app']}")
            lines.append(_speakable(n))
    for line in lines:      # iPhone ones are voice only: not in the chat or her bubble (Phone Link's pop-up is hidden too)
        voice_io.push_event({"reply": "🔔 " + line, "kind": "notification"})
    quiet = voice_io.nap["asleep"] and not config.settings().get("notif_sleep", False)
    if quiet and not (calls and voice_io.nap.get("reason") != "asked"):
        return              # napping: they're in the chat, but she only reads them out if ⚙ "…while I'm asleep" is on
    if calls:               # a ringing phone can't wait: not queued behind long answers, and not sent to the phone itself
        end = time.time() + PHONE_CALL_WAIT          # (it is said even in an idle nap - only "go to sleep" silences it)
        while (session.lock.locked() or voice_io.mouth.busy()) and time.time() < end:
            time.sleep(0.2)
        for line in calls:
            say_only(line)
    if quiet:
        return              # the ringing call was the exception; everything else waits for her to wake
    for line in missed:
        tell_away(line)     # a missed call is worth knowing about when they are away from the laptop
    if phone_lines:
        end = time.time() + 30
        while (session.lock.locked() or voice_io.mouth.busy()) and time.time() < end:
            time.sleep(0.5)
        for line in phone_lines:
            say_only(line)
    if not lines:
        return
    if len(lines) == 1:
        line = "🔔 " + lines[0]
    else:
        line = f"🔔 {len(lines)} new notifications. " + " … ".join(lines[:3])
        if len(lines) > 3:
            line += f" … and {len(lines) - 3} more."
    end = time.time() + 30
    while (session.lock.locked() or voice_io.mouth.busy()) and time.time() < end:    # don't talk over her own answer
        time.sleep(0.5)
    voice_io.announce(line, spoken=line.replace("🔔 ", "", 1))


notif_watcher = notifications.Watcher(on_notifications, every=1, on_note=lambda s: conversation.task_log(s))   # a ringing call can't wait 4 s


PHONE_TAG = protocol.PHONE_TAG


AWAY_IDLE = 300          # no keyboard / mouse for 5 minutes = the user is probably away (presence.py knows better)


def user_away():
    if presence.state["away"]:
        return True
    try:
        return background.user_idle_secs() >= AWAY_IDLE
    except Exception:
        return False


def tell_away(line):
    """Something she just said out loud that the user would want to know even if they've left the laptop: sent to
    their phone (if paired and forwarding is on), and kept for the 'welcome back' summary."""
    if not user_away():
        return
    presence.missed(line)
    try:
        if phone.notify("🔔 " + line):
            conversation.task_log(f"PHONE forwarded: {line[:120]}")
    except Exception as e:
        conversation.task_log(f"PHONE forward failed: {type(e).__name__}: {e}")


def phone_message(text):
    conversation.task_log(f"PHONE in: {text[:200]}")
    if session.state["pending"] and len(text.split()) <= 4 and (intents.YES.search(text) or intents.NO.search(text)):
        out, _ = voice_io.confirm_pending(bool(intents.YES.search(text)) and not intents.NO.search(text), quiet=True, remote=True)
    else:
        out, _ = body.answer(f"{PHONE_TAG} {text}", quiet=True, remote=True)
    voice_io.push_event({"me": "📱 " + text, **{k: v for k, v in out.items() if k != "images"}})
    conversation.task_log(f"PHONE out: {(out.get('reply') or out.get('error') or '')[:200]}")
    return out


def phone_confirm(approved):
    out, _ = voice_io.confirm_pending(approved, quiet=True, remote=True)
    if not (out.get("reply") or out.get("pending") or out.get("activity") or out.get("error")):
        out["reply"] = "That question isn't waiting any more."
    voice_io.push_event({"me": "📱 " + ("Yes" if approved else "No"), **{k: v for k, v in out.items() if k != "images"}})
    return out


def phone_status():
    s = tools.stats()
    bat = "no battery" if s["battery"] is None else f"battery {s['battery']}% ({'charging' if s['plugged'] else 'on battery'})"
    jobs = [j for j in coworker.jobs if j.get("status") == "working"]     # coworker's word for a running job
    return (f"💻 Laptop: CPU {s['cpu']:.0f}%, memory {s['ram']:.0f}%, disk {s['disk']:.0f}%, {bat}, on for {s['uptime_h']} h."
            + (f"\n🛠 Working on: {', '.join(j['title'] for j in jobs)}" if jobs else "")
            + ("\n😴 I'm napping." if voice_io.nap["asleep"] else "")
            + ("\n🔒 Screen is locked." if presence.screen_locked() else ""))


phone = phone_link.Phone(on_message=phone_message, on_confirm=phone_confirm, on_stop=lambda: conversation.stop_now(),
                         status_text=phone_status)


MOBILE_TAG = protocol.MOBILE_TAG


def mobile_message(text):
    conversation.task_log(f"MOBILE in: {text[:200]}")
    if session.state["pending"] and len(text.split()) <= 4 and (intents.YES.search(text) or intents.NO.search(text)):
        out, _ = voice_io.confirm_pending(bool(intents.YES.search(text)) and not intents.NO.search(text), quiet=True, remote=True)
    else:
        out, _ = body.answer(f"{MOBILE_TAG} {text}", quiet=True, remote=True)
    voice_io.push_event({"me": "📱 " + text, **{k: v for k, v in out.items() if k != "images"}})
    conversation.task_log(f"MOBILE out: {(out.get('reply') or out.get('error') or '')[:200]}")
    return out


def mobile_confirm(approved):
    out, _ = voice_io.confirm_pending(approved, quiet=True, remote=True)
    if not (out.get("reply") or out.get("pending") or out.get("activity") or out.get("error")):
        out["reply"] = "That question isn't waiting any more."
    voice_io.push_event({"me": "📱 " + ("Yes" if approved else "No"), **{k: v for k, v in out.items() if k != "images"}})
    return out


def _task_rows():
    """📋 the iPhone app's read-only Tasks list (the Tasks page's newest 30 rows)."""
    from nova.core.app import tasks_page
    return tasks_page.rows(limit=30)


mobile = mobile_mod.Mobile(on_message=mobile_message, on_confirm=mobile_confirm, on_stop=lambda: conversation.stop_now(),
                           status_text=phone_status, state=lambda: {"asleep": bool(voice_io.nap["asleep"]), "busy": session.lock.locked()},
                           tasks=_task_rows)


def tailscale_url():
    """Nova's private https address if Tailscale is installed and 'serve' is set up: shown in settings so it can be typed
    on the phone. "" when Tailscale isn't there."""
    import shutil
    import subprocess
    exe = shutil.which("tailscale") or "C:/Program Files/Tailscale/tailscale.exe"
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=6,
                             creationflags=0x08000000).stdout
        name = (json.loads(out).get("Self") or {}).get("DNSName", "").rstrip(".")
        return f"https://{name}" if name else ""
    except Exception:
        return ""


def mobile_info():
    return {"on": bool(config.settings().get("mobile")), "running": mobile.running(), "code": mobile.pairing(),
            "devices": mobile.devices(), "url": tailscale_url(), "port": mobile_mod.PORT,
            "serve": f"tailscale serve --bg --https=443 http://127.0.0.1:{mobile_mod.PORT}"}


@api.app.get("/api/mobile")
def mobile_get():
    return jsonify(mobile_info())


@api.app.post("/api/mobile")
def mobile_set():
    """⚙ Nova on iPhone: switch on/off, new pairing code, remove a paired device."""
    body = api._body()
    if "on" in body:
        s = config.settings()
        s["mobile"] = body["on"] is True
        config.save_settings(s)
        mobile.start() if s["mobile"] else mobile.stop()
    if body.get("new_code") is True:
        if not mobile.running():
            return jsonify(error="Switch Nova on iPhone on first.", **mobile_info()), 400
        mobile.new_pair_code()
    if "remove" in body:
        mobile.remove(str(body["remove"]))
    return jsonify(mobile_info())


def email_allowed():
    """Her mail is read by the local brain only - unless ⚙ allows the chosen cloud brain to see it too."""
    s = config.settings()
    return bool(s.get("email_cloud")) or not cloud.parse(conversation.task_brain(s))


tools.notes.email_ok = email_allowed


@api.app.get("/api/gmail")
def gmail_get():
    return jsonify(**gmail.status(), connecting=gmail.connecting["on"], error=gmail.connecting["error"])


@api.app.post("/api/gmail")
def gmail_set():
    """⚙ Gmail: save the Google client, connect (Google's own sign-in opens in the browser), disconnect."""
    body = api._body()
    try:
        if "client_id" in body or "client_secret" in body:
            gmail.save_client(api._text(body, "client_id"), api._text(body, "client_secret"))
        if body.get("connect") is True and not gmail.connecting["on"]:
            gmail.connecting.update(on=True, error="")

            def go():
                try:
                    gmail.connect()
                except gmail.GmailError as e:
                    gmail.connecting["error"] = str(e)
                    conversation.task_log(f"GMAIL connect: {e}")
            runtime.spawn("integrations.go", go)
        if body.get("disconnect") is True:
            gmail.disconnect()
    except gmail.GmailError as e:
        return jsonify(error=str(e), **gmail.status()), 400
    return jsonify(**gmail.status(), connecting=gmail.connecting["on"], error=gmail.connecting["error"])


@api.app.get("/api/phone")
def phone_get():
    return jsonify(phone.info())


@api.app.post("/api/phone")
def phone_set():
    """⚙ Phone: save / forget the bot token, unpair, new pairing code, forward reminders on/off."""
    body = api._body()
    try:
        if "token" in body:
            return jsonify(phone.set_token(api._text(body, "token")))
        if body.get("unpair") is True:
            return jsonify(phone.unpair())
        if body.get("new_code") is True:
            phone.new_pair_code()
        if "forward" in body:
            phone.set_forward(body["forward"] is True)
    except phone_link.PhoneError as e:
        return jsonify(error=str(e), **phone.info()), 400
    return jsonify(phone.info())


@api.app.get("/api/workers")
def workers_get():
    """⚙ Workers: the coding workers (workers.py) - installed / signed in, models, budgets, recent jobs."""
    return jsonify(workers.overview(fresh=request.args.get("fresh") == "1"))


@api.app.post("/api/workers")
def workers_set():
    try:
        return jsonify(workers.save_settings(api._body()))
    except ValueError as e:
        return jsonify(error=str(e), **workers.overview()), 400


GREET_EVERY = 600        # at most one "welcome back" per 10 minutes


_greeted = {"at": 0.0, "warned": False}


def _first_name():
    try:
        p = knowledge.load()["profile"]
        return (p.get("nickname") or p.get("name") or "").split()[0][:20]
    except Exception:
        return ""


def presence_idle():
    """Seconds since the user last did anything Nova can notice: keyboard / mouse, or talking to her."""
    idle = background.user_idle_secs()
    # Not mouth.busy(): her OWN voice (a reminder, a notification read out to an empty room) isn't the user being
    # here - it used to count, so a notification made her "see" the user come back and greet nobody.
    if voice_io.convo["on"] or voice_io.convo["thinking"]:
        return 0.0
    return min(idle, max(0.0, time.time() - voice_io.convo["last_heard"]))


def on_presence(ev):
    conversation.task_log(f"PRESENCE {ev} ({presence.state['note']})")
    if ev == "back" and voice_io.nap["asleep"] and voice_io.nap.get("reason") == "asked":
        # The user told her to sleep ("Nova, go to sleep"): coming back doesn't overrule that. Only "Hey Nova" (or a
        # typed message) wakes her, and what happened while they were away is kept for the next welcome.
        conversation.task_log("PRESENCE back, but you asked me to sleep - staying asleep and quiet")
        return
    if ev == "back" and time.time() - _greeted["at"] >= GREET_EVERY:
        _greeted["at"] = time.time()
        if voice_io.nap["asleep"]:
            voice_io.wake_body()
            voice_io.nap.update(asleep=False, last_task=time.time())
        line = presence.welcome(_first_name())
        voice_io.push_event({"reply": line, "kind": "suggestion"})
        voice_io.announce(line)
        body.react_later("wave", wait_for_speech=False)
    elif ev == "lock_warn":
        _greeted["warned"] = True
        voice_io.announce(f"Looks like you stepped away - I'll lock the laptop in {presence.LOCK_GRACE} seconds. "
                 "Move the mouse to stop me.", spoken="Looks like you stepped away. Locking the laptop in a moment.")
    elif ev == "lock":
        _greeted["warned"] = False
        tools.lock_screen()
        conversation.task_log("PRESENCE locked the screen")
    elif ev == "lock_cancel" and _greeted["warned"]:
        _greeted["warned"] = False
        voice_io.bubble("Okay, not locking 👍", secs=3)


def presence_watch():
    while True:
        time.sleep(5)
        try:
            s = config.settings()
            if not s.get("presence"):
                if presence.state["away"] or presence.state["locking_at"]:
                    presence.reset()
                continue
            idle, before = presence_idle(), presence.state["checks"]
            evs = presence.tick(time.time(), idle, presence.screen_locked(), presence.look,
                                lock_on=bool(s.get("presence_lock")))
            if presence.state["checks"] != before:      # each camera look, for "why didn't she notice I left?"
                conversation.task_log(f"PRESENCE check {presence.state['checks']}: {presence.state['note']} "
                         f"(idle {idle:.0f}s, misses {presence.state['misses']}, away {presence.state['away']})")
            for ev in evs:
                on_presence(ev)
        except Exception as e:
            conversation.task_log(f"presence watch: {type(e).__name__}: {e}")


HELP_SECS = 180          # a bare "yes" answers the help offer this long


HELP_EVERY = 90          # at most one help offer per 90 s


help_offer = {"item": None, "until": 0.0, "id": 0, "at": 0.0}


screen_watch = watcher.Watcher()


help_queue = []          # trouble found while an earlier offer was still fresh


def offer_help(item):
    help_offer.update(item=item, until=time.time() + HELP_SECS, id=help_offer["id"] + 1, at=time.time())
    line, spoken = watcher.offer_line(item)
    conversation.task_log(f"HELP OFFER {item['kind']} {item['proc']}: {item['title'][:60]} | {item['text'][:160]}")
    voice_io.push_event({"reply": "🆘 " + line, "kind": "suggestion",
                "offer": {"id": help_offer["id"], "label": "Close it" if item["kind"] == "hung" else "Help me",
                          "help": True}})
    voice_io.announce("🆘 " + line, spoken=spoken)
    if config.settings()["wake"] and not voice_io.convo["on"] and voice_io.silent_ok():     # listen for the "yes"
        voice_io.listen_state["single"] = True
        runtime.spawn("integrations.silent_loop", voice_io.silent_loop)


def take_help(text, force=None, offer_id=None):
    """'yes' / 'no' right after "want me to help with it?". Returns Nova's answer dict, or None if this isn't about it."""
    item = help_offer["item"] if help_offer["until"] > time.time() else None
    if not item or (offer_id is not None and offer_id != help_offer["id"]):
        return None
    clean = config._unspoken(text).strip()
    short = len(clean.split()) <= 6
    yes = force is True or (force is None and short and bool(intents.YES.search(clean) or intents.HELP_YES.search(clean)))
    no = force is False or (force is None and short and bool(intents.NO.search(clean)) and not yes)
    if not (yes or no):
        return None
    help_offer.update(item=None, until=0.0)
    voice_io.note_task()
    if no:
        out = {"reply": "Okay, I'll leave it. 👍", "activity": []}
    elif item["kind"] == "hung":
        session.think(f"🧊 Closing {item['app']} - it stopped responding.")
        out = {"reply": watcher.close_hung(item), "activity": ["Closed a frozen app"]}
        conversation.task_log(f"HELP closed hung {item['proc']}: {out['reply']}")
    else:
        out, _ = body.answer(watcher.help_request(item))        # she speaks this answer herself
        return out
    voice_io.announce(out["reply"])
    return out


def help_first():
    """Is the help offer newer than a finished job's "want to see it?" (so a bare yes goes to it)?"""
    return help_offer["until"] > time.time() and help_offer["at"] >= offer["until"] - OFFER_SECS


def help_step():
    """One look for trouble on screen (every 4 s)."""
    if not config.settings().get("error_help", True):
        return
    found = screen_watch.scan()
    if session.lock.locked() or session.state["pending"]:     # her own task opens dialogs too: she sees those herself
        return
    help_queue.extend(found)
    del help_queue[:-5]
    # Waits its turn (one offer per HELP_EVERY), and is dropped if the box closed / the app recovered meanwhile.
    # (Live test: a frozen app found 60 s after an error-box offer used to be thrown away.)
    help_queue[:] = [i for i in help_queue if watcher.still_there(i)]
    if help_queue and time.time() - help_offer["at"] >= HELP_EVERY:
        offer_help(help_queue.pop(0))


def help_watch():
    while True:
        time.sleep(4)
        try:
            help_step()
        except Exception as e:
            conversation.task_log(f"help watch: {type(e).__name__}: {e}")


@api.app.post("/api/help")
def help_answer():
    """The chat page's "Help me" / "Close it" / "Not now" buttons under a help offer."""
    body = api._body()
    oid = body.get("id")
    out = take_help("", force=body.get("yes") is True, offer_id=oid if isinstance(oid, int) else None)
    return jsonify(out or {"reply": "That one isn't waiting any more.", "activity": []})


toolpick.EXTRA = mcp.tools_list


toolpick.EXTRA_GROUPS = mcp.groups


@api.app.get("/api/forms")
def forms_get():
    """⚙ Forms: the user's saved form details (this page only - _ours() keeps other sites out)."""
    from nova.actions import forms
    labels = {k: lbl for k, lbl, _ in forms.FIELDS}
    d = forms.load()
    return jsonify(fields=[{"key": k, "label": labels[k], "value": d[k]} for k in forms.DIALOG_ORDER])


@api.app.post("/api/forms")
def forms_set():
    from nova.actions import forms
    body = api._body().get("details")
    if not isinstance(body, dict):
        return jsonify(error="expected details"), 400
    saved, err = forms.save(body)
    if err:
        return jsonify(error=err), 400
    conversation.task_log(f"FORMS: saved {len([v for v in saved.values() if v])} details (values not logged)")
    return forms_get()


@api.app.get("/api/mcp")
def mcp_get():
    try:
        text = json.dumps(mcp.load_config(), indent=2)
    except mcp.MCPError:
        text = mcp.config_file().read_text(encoding="utf-8")
    return jsonify(config=text, **mcp.summary())


@api.app.post("/api/mcp")
def mcp_set():
    """⚙ Connectors: save the config (the same JSON other MCP apps use) and reconnect every server."""
    raw = api._text(api._body(), "config")
    try:
        cfg = json.loads(raw or '{"mcpServers": {}}')
    except ValueError as e:
        return jsonify(error=f"That isn't valid JSON: {e}"), 400
    if isinstance(cfg, dict) and "mcpServers" not in cfg and cfg and all(isinstance(v, dict) for v in cfg.values()):
        cfg = {"mcpServers": cfg}          # just the servers was pasted
    try:
        mcp.save_config(cfg)
    except mcp.MCPError as e:
        return jsonify(error=str(e)), 400
    mcp.start(wait=True)
    conversation.task_log("CONNECTORS: " + "; ".join(f"{s['name']} {s['status']} {len(s['tools'])} tools {s['error'][:80]}"
                                        for s in mcp.summary()["servers"]))
    return jsonify(config=json.dumps(cfg, indent=2), **mcp.summary())


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import background, body, config, conversation, session, voice_io  # noqa: E402
