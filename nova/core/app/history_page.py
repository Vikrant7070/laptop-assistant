"""The "What Nova knows about me" part of the Knowledge page (manager plan, Phase 6): switch each laptop history source
on or off, the never list, pause, each history fact with its source (✓ / Delete), "forget everything from <source>",
and the idle-time read (laptop_history.run, called from learning.learn_now - never during a task)."""
import time

from flask import jsonify

from nova import runtime
from nova.actions import laptop_history
from nova.core.app import api

read_state = {"running": False}


def _busy():
    return bool(session.lock.locked() or voice_io.convo["thinking"] or session.state.get("pending"))


def read_if_due(now=None):
    """Called at idle time. Reads in its own thread so the idle watch isn't held up; one read at a time."""
    if read_state["running"] or _busy() or not laptop_history.due(now):
        return False
    read_state["running"] = True

    def go():
        try:
            out = laptop_history.run(busy=_busy)
            if out:
                conversation.task_log(f"LAPTOP HISTORY read: {out}")
        except Exception as e:
            conversation.task_log(f"LAPTOP HISTORY read failed: {type(e).__name__}: {e}")
        finally:
            read_state["running"] = False
    runtime.spawn("laptop_history.read", go)
    return True


@api.app.get("/api/laptop_history")
def laptop_history_get():
    return jsonify(**laptop_history.status(), reading=read_state["running"])


@api.app.post("/api/laptop_history")
def laptop_history_edit():
    b = api._body()
    do = b.get("do")
    src = api._text(b, "source")
    if do in ("source_on", "source_off", "forget") and src not in laptop_history.SOURCES:
        return jsonify(error="unknown source"), 400
    if do in ("fact_delete", "fact_confirm") and (not isinstance(b.get("id"), int) or isinstance(b.get("id"), bool)):
        return jsonify(error="id must be a number"), 400
    if do == "source_on":
        msg = laptop_history.set_source(src, True)
    elif do == "source_off":
        msg = laptop_history.set_source(src, False)
    elif do == "never_add":
        msg = laptop_history.never_add(api._text(b, "value"))
    elif do == "never_remove":
        msg = laptop_history.never_remove(api._text(b, "value"))
    elif do in ("pause", "resume"):
        msg = laptop_history.set_paused(do == "pause")
    elif do == "forget":
        msg = f"Forgot {laptop_history.forget(src)} fact(s) from {laptop_history.NAMES[src]}, and its counts."
    elif do == "fact_delete":
        msg = f"Forgot {laptop_history.delete(b['id'])} fact(s). It won't come back from the same history."
    elif do == "fact_confirm":
        from nova.actions import learner
        ok = any(f["id"] == b["id"] for f in laptop_history.facts()) and learner.confirm_fact(b["id"])
        msg = "Marked as right." if ok else "That fact isn't there any more."
    elif do == "read_now":
        state = laptop_history.load()
        if state["paused"]:
            msg = "Reading is paused."
        elif not any(state["on"].values()):
            msg = "Switch a source on first."
        elif read_state["running"]:
            msg = "Already reading."
        else:
            with laptop_history._lock:
                state["last_run"] = 0
                laptop_history._save(state)
            started = read_if_due(time.time())
            msg = "Reading now - the facts appear here in a moment." if started else "She's busy with a task; she'll read when it's done."
    else:
        return jsonify(error="unknown action"), 400
    return jsonify(ok=True, message=msg)


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import conversation, session, voice_io  # noqa: E402
