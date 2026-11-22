"""Save and resume tasks (manager plan 4-pre b): nothing she was doing is lost to a Stop, a barge-in or a restart.

- **Saved cards.** Each task's card (nova/task_state.py) goes to nova.db's `tasks` table after every batch of steps and
  when the task ends (`save`). Kept KEEP_DAYS days.
- **Chat after a restart.** The last CHAT_TAIL user/assistant turns go to chat_tail.json after every answer and are
  loaded back at start-up (`on_start`). A character switch or a chat reset clears them.
- **Restart.** At start-up a card still running or waiting becomes *interrupted (restart)*, and she asks: "Before the
  restart I was on step 3 of ... Should I continue?" She never carries on by herself.
- **"Continue" is code, not a brain turn** (`direct`, called by body.answer before anything else):
  1. right after a barge-in cut her off, it says the part she didn't get to say;
  2. otherwise it picks the stopped task: the newest one, or with several she asks which (numbered, so "two" works);
  3. safe resume: the Judge's goal checks run first (already done? say so); a step that changes things and was
     started but never confirmed is never redone without asking; if the app the task works in has gone, the rest
     of the plan is re-planned once (step outcomes only, never raw results);
  4. then `take` / `note` hand the brain the saved card with "carry on from step N, don't redo ✅ steps".
- **One laptop task at a time.** A new request while one is waiting or stopped leaves that one stopped and resumable
  (`leave`).
"""
import re
import time

from nova import db, intents, protocol, store, task_state
from nova.actions import judge, skills
from nova.actions import checks
from nova.brain import router
from nova.log import log_exception

KEEP_DAYS = 30              # task cards older than this are deleted at start-up
CHAT_TAIL = 12              # chat messages kept for after a restart
OFFER_HOURS = 24            # "continue" only picks up tasks stopped within this time
ASK_SECS = 600              # a resume question ("which one?", "redo that step?") expires after this
CUTOFF_SECS = 600           # "continue" finishes a cut-off reply only this soon after it
MAX_CHOICES = 3
CUT_MARK = "[cut off here: the user talked over you, the rest was never said]"


# ------------------------------------------------------------------ saving

def save(task=None):
    """Save the (current) task's card. Never raises: saving must not stop her answering."""
    card = task_state.of(task if task is not None else session.state.get("task"))
    if card is None or not card.get("id"):
        return
    try:
        db.save_task(card)
    except Exception:
        log_exception("resume: saving the task card")


def _keepable(m):
    """A message worth keeping across a restart: plain words from the user or her, nothing hidden or secret."""
    if m.get("role") not in ("user", "assistant") or m.get("tool_calls") or not isinstance(m.get("content"), str):
        return None
    text = m["content"]
    if protocol.is_meta_line(text):
        return None
    if m["role"] == "user":
        text = text.split(protocol.WINDOW_NOTE_START)[0].split("\n" + protocol.NOTE_START)[0]
    text = text.strip()
    if not text or task_state._SECRET_RE.search(text):
        return None
    return {"role": m["role"], "content": text[:2000]}


def save_chat():
    """The last few turns, for after a restart."""
    try:
        msgs = [k for m in session.state["messages"] if (k := _keepable(m))][-CHAT_TAIL:]
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        st = store.current()
        st.write_json(st.chat_tail, {"version": 1, "saved": time.time(), "messages": msgs})
    except Exception:
        log_exception("resume: saving the chat")


def clear_chat():
    """A fresh chat (character switch, reset): nothing to load back after a restart either."""
    try:
        store.current().chat_tail.unlink(missing_ok=True)
    except OSError:
        log_exception("resume: clearing the saved chat")


def load_chat():
    """The saved turns, or [] (none, unreadable, or not the shape we wrote)."""
    st = store.current()
    data = st.read_json(st.chat_tail, {}) if st.chat_tail.exists() else {}
    msgs = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(msgs, list):
        return []
    return [{"role": m["role"], "content": m["content"]} for m in msgs
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]


# ------------------------------------------------------------------ start-up

def on_start():
    """Load the chat back, mark tasks a restart cut short as interrupted. -> the line to say, or ""."""
    msgs = load_chat()
    if msgs and not session.state["messages"]:
        session.state["messages"].extend(msgs)
    try:
        db.prune_tasks(time.time() - KEEP_DAYS * 86400)
        for card in db.load_tasks(statuses=("running", "waiting")):
            card = task_state.upgrade(card)
            if card is not None and not card.get("read_only"):
                task_state.interrupt(card, "restart")
                db.save_task(card)
        cards = _candidates()
    except Exception:
        log_exception("resume: reading saved tasks at start-up")
        return ""
    cards = [c for c in cards if c["status"] == "interrupted"]
    if not cards:
        return ""
    session.state["resume_ask"] = {"kind": "restart", "ids": [cards[0]["id"]], "ts": time.time()}
    return f"Before the restart I was on {task_state.where(cards[0])}. Should I continue?"


def offer_after_start():
    """Started by background.start_background: ask about an interrupted task once she's up."""
    try:
        line = on_start()
    except Exception:
        log_exception("resume: start-up")
        return
    if line:
        conversation.task_log("RESUME OFFER: " + line)
        voice_io.push_event({"reply": line, "kind": "resume"})
        voice_io.announce(line)


# ------------------------------------------------------------------ cut-off replies (barge-in)

def spoke(reply, t0):
    """body.answer: this reply is being said, its sentences started coming out from t0."""
    session.state["last_reply"] = {"text": reply or "", "t0": t0}


def _words(text):
    return [m.group(0).lower() for m in re.finditer(r"\w+", text or "")]


def unsaid_part(reply, said):
    """What of `reply` comes after the last sentence really said (`said`: sentences, in order). None = can't tell."""
    if not said:
        return reply.strip()
    tail = _words(said[-1])[-6:]
    if not tail:
        return None
    spans = list(re.finditer(r"\w+", reply))
    words = [m.group(0).lower() for m in spans]
    for i in range(len(words) - len(tail), -1, -1):
        if words[i:i + len(tail)] == tail:
            return reply[spans[i + len(tail) - 1].end():].lstrip(" ,.;:!?—–-…").strip()
    return None


def cut_off(t_stop, mouth=None):
    """Barge-in stopped her at t_stop: keep only what was really said in the history, mark it cut off, and keep the
    rest so "continue" can finish it. Sentences still playing at t_stop count as not said."""
    last = session.state.get("last_reply") or {}
    reply = last.get("text") or ""
    if not reply or t_stop - last.get("t0", 0) > 600:
        return
    mouth = mouth or voice_io.mouth
    lines = [(s, e, w) for s, e, w in list(getattr(mouth, "said_lines", ())) if s >= last["t0"]]
    if not any(s <= t_stop < e for s, e, w in lines):
        return                                  # not in the middle of this reply (a gap, a later task, voice off)
    said = [w for s, e, w in lines if e <= t_stop]
    rest = unsaid_part(reply, said)
    if not rest:
        return                                  # all said, or the words don't line up: leave the history alone
    if not session.lock.acquire(timeout=1):
        return
    try:
        for m in reversed(session.state["messages"]):
            if m.get("role") == "assistant" and m.get("content") == reply:
                m["content"] = (reply[: len(reply) - len(rest)].rstrip() + " " + CUT_MARK).strip()
                break
        session.state["cutoff"] = {"rest": rest, "full": reply, "ts": time.time()}
        session.state.pop("last_reply", None)
        card = task_state.of(session.state.get("task"))
        if card is not None:
            card["unsaid"] = task_state.clean(rest, 600)
            save()
        conversation.task_log(f"CUT OFF after: {' '.join(said)[-80:]!r} - unsaid kept: {rest[:80]!r}")
    finally:
        session.lock.release()


def _finish_cut_off(cut):
    """'Continue' right after a cut-off: say the rest. The history keeps the cut reply (what she really said), the
    user's "continue" and then the rest, so it reads the way it happened."""
    card = task_state.of(session.state.get("task"))
    if card is not None:
        card["unsaid"] = ""
    conversation.task_log(f"CONTINUE: finishing the cut-off reply ({len(cut['rest'])} chars), no brain turn")
    return _out(cut["rest"], "Finished what I was saying")


# ------------------------------------------------------------------ "continue" and its questions

def _out(reply, activity):
    """A reply made by code (no brain turn); body.answer stores it in the history like the brain's."""
    return {"reply": reply, "say": reply, "activity": [activity], "by_code": True}


def _candidates():
    """Stopped / interrupted tasks that can be carried on, newest first (at most MAX_CHOICES)."""
    out, seen = [], set()
    cur = task_state.of(session.state.get("task"))
    if task_state.resumable(cur):
        out.append(cur)
        seen.add(cur["id"])
    for card in db.load_tasks(statuses=task_state.RESUMABLE, since=time.time() - OFFER_HOURS * 3600, limit=10):
        card = task_state.upgrade(card)
        if card is not None and card["id"] not in seen and task_state.resumable(card):
            out.append(card)
            seen.add(card["id"])
    return out[:MAX_CHOICES]


def _find(cid):
    cur = task_state.of(session.state.get("task"))
    if cur is not None and cur["id"] == cid:
        return cur
    for card in db.load_tasks(statuses=task_state.RESUMABLE, limit=20):
        if card.get("id") == cid:
            return task_state.upgrade(card)
    return None


def _choice(text, n):
    """'two' / 'the second one' / '2' -> 1; a plain yes with one choice -> 0; else None."""
    i = intents.choice(text)
    if i is not None and i < n:
        return i
    if n == 1 and (intents.YES.search(text) or intents.RESUME.search(text)):
        return 0
    return None


def direct(text):
    """The user's words, before any brain turn. -> a reply made by code, or None (go on as usual; `take` may then
    have a task to carry on with)."""
    clean = protocol.unspoken(text).strip()
    session.state.pop("last_reply", None)          # a new request: a barge-in from now on isn't about the old reply
    ask = session.state.pop("resume_ask", None)
    if ask and time.time() - ask["ts"] < ASK_SECS:
        got = _answer(ask, clean)
        if got is not False:
            return got
    if not intents.RESUME.search(clean):
        return None
    cut = session.state.pop("cutoff", None)
    if cut and time.time() - cut["ts"] < CUTOFF_SECS:
        return _finish_cut_off(cut)
    try:
        cards = _candidates()
    except Exception:
        log_exception("resume: reading stopped tasks")
        return None
    if not cards:
        return None                     # nothing stopped: an ordinary request ("keep going" with nothing to go on with)
    if len(cards) > 1:
        names = ", ".join(f"{w}: {c['goal'][:60]}" for w, c in zip(("one", "two", "three"), cards))
        session.state["resume_ask"] = {"kind": "which", "ids": [c["id"] for c in cards], "ts": time.time()}
        return _out(f"I stopped more than one task. Which one should I carry on with? {names}.", "Asked which task")
    return prepare(cards[0])


def _answer(ask, clean):
    """The answer to a resume question. -> a reply, None (carry on through take), or False (not an answer)."""
    if ask["kind"] in ("which", "restart"):
        i = _choice(clean, len(ask["ids"]))
        if i is None:
            if intents.NO.search(clean):
                return _out("Okay, I'll leave it. Say “continue” any time.", "Left the task")
            return False                # a new request: the old tasks stay stopped
        card = _find(ask["ids"][i])
        return prepare(card) if card else _out("I can't find that task any more.", "Task gone")
    if ask["kind"] == "redo":
        card = _find(ask["ids"][0])
        if card is None:
            return False
        if intents.YES.search(clean) or intents.RESUME.search(clean):
            return prepare(card, redo=True)
        if intents.NO.search(clean):
            return prepare(card, redo=False)
    return False


def _changes_things(tool):
    return not str(tool).startswith(skills.READ_ONLY)


def _already_done(card):
    """The Judge's goal checks, when they cover the whole goal: all pass -> the why, else None."""
    made = judge.goal_checks(card["goal"])
    if not made["checks"] or not made["covers"]:
        return None
    results = judge.run_checks(made["checks"], {"window": card.get("app", "")}, timeout=2)
    return "; ".join(r.get("seen") or r["check"] for r in results) if results and all(r["ok"] for r in results) else None


def _missing(card):
    """The app the task works in, if it has gone. -> words for the brain, or ""."""
    app_ = card.get("app")
    if not app_:
        return ""
    ok, _seen = checks.evaluate({"check": "window_open", "arg": app_}, {}, timeout=1)
    return "" if ok else f"{app_} isn't open any more"


def _replan(card, missing):
    """The one re-plan a task gets: the deep brain sees the goal, one-line step outcomes and what's missing."""
    card["replanned"] = True
    goal = (card["goal"] + "\nAlready tried, step by step:\n" + "\n".join(task_state.outcomes(card))
            + f"\nNow: {missing}. Plan only what is left to do.")
    try:
        plan, m = router.make_plan(config.settings(), goal)
    except Exception:
        log_exception("resume: re-plan")
        return False
    steps = task_state.steps_from_plan(plan, {t["name"] for t in tools.TOOLS}) if plan else []
    if not steps:
        conversation.task_log(f"RESUME re-plan gave nothing ({m})")
        return False
    task_state.set_plan(card, steps, "deep brain")
    conversation.task_log(f"RESUME re-planned with {m}: {len(steps)} steps")
    return True


def prepare(card, redo=None):
    """Safe resume. -> a reply made by code (done already / redo that step?), or None with the task queued for take."""
    try:
        done = _already_done(card)
    except Exception:
        log_exception("resume: goal checks")
        done = None
    if done:
        task_state.mark(card, "done", "already done when checked")
        save({"card": card})
        return _out(f"That's already done - my check says: {done}.", "Checked it's done")
    half = [r for r in task_state.unconfirmed(card) if _changes_things(r["tool"])]
    extra = []
    if half and redo is None:
        r = half[-1]
        session.state["resume_ask"] = {"kind": "redo", "ids": [card["id"]], "ts": time.time()}
        what = f"{r['tool']} {r['hint']}".strip()
        return _out(f"When it stopped I was in the middle of a step ({what}) - it may have half happened. "
                    "Should I do that step again?", "Asked before redoing a step")
    if half and redo is False:
        for r in half:
            r["state"], r["result"] = "skipped", "not redone - the user said no"
        extra.append("The user said NOT to redo the half-done step - skip it.")
    elif half:
        extra.append("The user said to redo the half-done step - check its state first, then do it again.")
    missing = _missing(card)
    if missing:
        if card["plan"] and not card.get("replanned") and _replan(card, missing):
            extra.append(f"{missing}, so the plan above was redone for what is left.")
        else:
            extra.append(f"{missing} - open it again first.")
    session.state["resume_next"] = {"card": card, "extra": extra}
    return None


def take():
    """new_task: the task "continue" picked (prepare), or None."""
    return session.state.pop("resume_next", None)


def note(resumed):
    """The hidden note in front of the brain's first turn on a resumed task."""
    card = resumed["card"]
    task_state.mark(card, "running", "the user said carry on")
    return "\n" + protocol.note(
        "The user wants you to carry on with this task, which was stopped before it was finished. Here is where it "
        f"got to, written by code from the steps that really ran:\n{task_state.render(card)}\n"
        + "".join(e + "\n" for e in resumed.get("extra") or [])
        + "Continue from where you stopped. Don't redo steps that already worked (✅).")


def leave(task):
    """A new request: the task before it is over for now. Still waiting (an approval never answered) -> stopped."""
    card = task_state.of(task)
    if card is not None and card["status"] in ("running", "waiting") and not task.get("paused"):
        task_state.mark(card, "stopped" if card["runs"] else "done", "a new request came")
        save(task)


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.actions import tools  # noqa: E402
from nova.core.app import config, conversation, session, voice_io  # noqa: E402
