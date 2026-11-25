"""🔒 Privacy: the activity log's watcher and ⚙ page, and the "save these form details?" card (manager plan Phase 7).

- activity_watch(): one activity.Sampler tick every activity.TICK seconds (started by background.start_background).
  The user counts as here when presence (YuNet) says so, if it is on; otherwise while the laptop is unlocked and
  was touched in the last few minutes.
- /api/activity: the switch, the pause, the blocked apps / sites, what was recorded (with delete), the routines.
  Only Nova's own page uses it (api.only_this_laptop); nothing from it goes to a brain.
- The form card: the sampler saw the user type new profile details into a form and the page moved on -> one card,
  "Save your email and phone for forms?" (labels only). Yes saves them encrypted (forms.notice.accept), No forgets.
"""
import time

from flask import jsonify

from nova.actions import activity, forms, presence
from nova.core.app import api, config
from nova.log import log_exception

ASK_WAIT = 600                 # a form card that couldn't be asked yet (she was busy) waits this long, then is dropped
_later = {"keys": [], "at": 0.0}


def _here(now):
    if config.settings().get("presence"):
        return not presence.state["away"] and not presence.screen_locked()
    return activity.default_here(now)


def _offer_forms(keys):
    _later.update(keys=list(keys), at=time.time())
    return _ask_forms()


def _ask_forms():
    """Ask the waiting form card when nothing else is going on (no open card, not busy)."""
    keys = _later["keys"]
    if not keys:
        return None
    if time.time() - _later["at"] > ASK_WAIT:
        _later.update(keys=[], at=0.0)
        forms.notice.forget()
        return None
    if asking.current() is not None or background.busy_now():
        return None
    _later.update(keys=[], at=0.0)
    what = forms.question_text(keys)
    card = asking.make("form_profile", [asking.question(
        "save", f"You just typed your {what} into a form. Save {'it' if len(keys) == 1 else 'them'} so I can fill "
        "forms for you next time?", "yesno", choices=[("yes", "Save"), ("no", "No, forget it")],
        why="kept encrypted on this laptop only; my AI brain never sees the values")],
        title="Save your form details?", data={"keys": keys})
    out = asking.ask(card, "Asked to save your form details", wait=False)
    voice_io.push_event({**out, "kind": "suggestion"})
    voice_io.announce(out.get("say") or "")
    return out


def _answered(qcard, answers):
    if answers.get("save", {}).get("value") == "yes":
        saved = forms.notice.accept()
        conversation.task_log(f"FORMS: saved {len(saved)} noticed detail(s) (values not logged)")
        line = (f"Saved your {forms.question_text(qcard['data'].get('keys') or [])} - encrypted, on this laptop. "
                "You can change them in ⚙ Settings → 📝 Forms.") if saved else "There was nothing left to save."
    else:
        forms.notice.forget()
        line = "Okay, I've forgotten them, and I won't ask about those again."
    return {"reply": line, "say": line, "activity": ["Form details"], "by_code": True}


def _dropped(qcard):
    forms.notice.forget()


sampler = activity.Sampler(user_here=_here, on_form_offer=_offer_forms)
_watch = {"saved_routines": 0.0}


def activity_watch():
    """The activity log's clock. Cheap: one foreground-window look per tick; routines once every few hours."""
    while True:
        time.sleep(activity.TICK)
        try:
            sampler.tick()
            if _later["keys"]:
                _ask_forms()
            now = time.time()
            if sampler.state != "off" and now - _watch["saved_routines"] > 6 * 3600:
                _watch["saved_routines"] = now
                activity.save_routines(now)
        except Exception:
            log_exception("activity watch")


# ------------------------------------------------------------------ ⚙ Privacy

def _view():
    s = activity.prefs()
    rows = activity.recent(300)
    return {**activity.status(sampler), "notice_forms": s["notice_forms"], "blocked_apps": s["blocked_apps"],
            "blocked_sites": s["blocked_sites"], "routines": activity.routines(), "recent": rows}


@api.app.get("/api/activity")
def activity_get():
    return jsonify(_view())


@api.app.get("/api/activity/state")
def activity_state():
    """The indicator: on / paused / recording / away / blocked (no titles)."""
    return jsonify(activity.status(sampler))


@api.app.post("/api/activity")
def activity_set():
    b = api._body()
    changes = {k: b[k] for k in ("on", "paused", "notice_forms", "blocked_apps", "blocked_sites") if k in b}
    try:
        if changes:
            s = activity.save_prefs(**changes)
            if not s["on"] or s["paused"]:
                sampler.stop()
                forms.notice.forget()
            conversation.task_log(f"ACTIVITY LOG settings: on={s['on']} paused={s['paused']} "
                                  f"forms={s['notice_forms']} blocked={len(s['blocked_apps'])}/{len(s['blocked_sites'])}")
        gone = 0
        if b.get("delete_all"):
            sampler.stop()
            gone = activity.delete(everything=True)
        elif b.get("delete_ids"):
            gone = activity.delete(ids=[int(x) for x in b["delete_ids"]])
        elif b.get("delete_app"):
            gone = activity.delete(app=str(b["delete_app"]))
        if b.get("block_app"):                          # "never record this one" from the view: block + delete
            s = activity.prefs()
            name = str(b["block_app"]).strip().lower()
            key = "blocked_sites" if "." in name and not name.endswith(".exe") else "blocked_apps"
            if name and name not in s[key]:
                activity.save_prefs(**{key: s[key] + [name]})
            gone = activity.delete(app=name)
        if gone:
            conversation.task_log(f"ACTIVITY LOG: deleted {gone} entries")
    except (KeyError, ValueError, TypeError) as e:
        return jsonify(error=f"Bad setting: {e}"), 400
    return jsonify({**_view(), "deleted": gone})


@api.app.post("/api/forms/forget")
def forms_forget():
    """⚙ Forms: forget every saved detail."""
    forms.delete_all()
    forms.notice.forget()
    conversation.task_log("FORMS: all saved details deleted")
    return jsonify(ok=True)


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import asking, background, conversation, voice_io  # noqa: E402
asking.handler("form_profile", _answered, _dropped)
