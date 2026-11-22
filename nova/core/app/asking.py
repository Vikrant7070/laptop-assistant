"""Questions you answer with a tap (manager plan 4-pre e): one question card everywhere.

Code that needs an answer (the resume questions today, 4a's manager questions next) makes a card and asks it:

    card = asking.make("resume", [asking.question("task", "Which one should I carry on with?", "one",
                                                  choices=[(id1, "sort my photos"), (id2, "clean my laptop")])],
                       task=card_id, data={...})
    return asking.ask(card)          # -> the reply dict; the chat, the strip, /tasks and the phone show the card

A card holds at most MAX_QUESTIONS questions, each with its one-line reason. Kinds:
    yesno   two buttons (labels can be "Go" / "Change it")      one    a button per choice, one tap answers
    many    checkboxes + Done                                    text   a text box
Every "one" / "many" question can offer Other… (your own words), and any question with a `guess` can be skipped
(the owner is told it was a guess, so a brief can say so). `last` is the answer given last time (highlighted).
`fact` is the fact key 4a saves the answer under; this module only carries it.

Answers come in three ways, all ending in the owner's handler (`handler(owner, on_answer, on_drop)`):
- a tap: POST /api/answer {id, answers} (the phone: tap()) - checked against the choices offered, then run through
  body.answer with the chosen labels as the user's words, so the chat history reads the way it happened;
- typed or spoken words: from_words(), first thing in body.answer - "two", "the second one", a choice's name, yes/no,
  "skip", "other: …";
- words that don't answer it: the card is dropped (on_drop) and the words go on as a new request.
"""
import re
import time

from flask import jsonify

from nova import intents, protocol, task_state
from nova.core.app import api, eventbus
from nova.log import log_exception

ASK_SECS = 600              # an unanswered card expires after this
MAX_QUESTIONS = 2           # the user's rule: at most 2 questions at once (plan section 9)
MAX_CHOICES = 8
KINDS = ("yesno", "one", "many", "text")
NUMBERS = ("one", "two", "three", "four", "five", "six", "seven", "eight")
HANDLERS = {}               # owner -> (on_answer(card, answers) -> reply dict or None, on_drop(card) or None)


def handler(owner, on_answer, on_drop=None):
    HANDLERS[owner] = (on_answer, on_drop)


def question(key, text, kind="one", choices=(), why="", other=False, guess=None, last=None, fact=""):
    """One question. choices: (value, label) pairs or {"value", "label"} dicts; yesno defaults to Yes / No."""
    if kind not in KINDS:
        raise ValueError(f"unknown question kind {kind!r}")
    norm = [c if isinstance(c, dict) else {"value": str(c[0]), "label": str(c[1])} for c in choices][:MAX_CHOICES]
    if kind == "yesno" and not norm:
        norm = [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]
    norm = [{"value": str(c["value"]), "label": task_state.clean(c["label"], 80)} for c in norm]
    return {"key": key, "text": task_state.clean(text, 300), "why": task_state.clean(why, 160), "kind": kind,
            "choices": norm, "other": bool(other) and kind in ("one", "many"), "guess": guess, "last": last,
            "fact": fact}


def make(owner, questions, title="", task=None, data=None):
    if not questions or len(questions) > MAX_QUESTIONS:
        raise ValueError(f"a card holds 1 to {MAX_QUESTIONS} questions")
    return {"id": f"q{time.time():.6f}", "owner": owner, "title": task_state.clean(title, 200), "task": task or "",
            "questions": list(questions), "ts": time.time(), "data": data or {}}


def public(card):
    """What the pages and the phone get (no handler data)."""
    return {k: v for k, v in card.items() if k != "data"} if card else None


def spoken(card):
    """The card as words (her reply, read aloud and sent to the phone): choices are numbered, so "two" works."""
    parts = [card["title"]] if card["title"] else []
    for q in card["questions"]:
        line = q["text"]
        if q["kind"] in ("one", "many") and q["choices"]:
            line = line.rstrip() + " " + ", ".join(f"{n}: {c['label']}" for n, c in zip(NUMBERS, q["choices"])) + "."
        parts.append(line)
    return " ".join(parts)


def ask(card, activity="Asked you", wait=True):
    """Open the card. wait: the task it belongs to is now *waiting for you* (with the question as the why).
    -> the reply dict (spoken words + the card)."""
    session.state["question"] = card
    if wait and card["task"]:
        tcard = resume._find(card["task"])
        if tcard is not None:
            task_state.mark(tcard, "waiting", card["questions"][0]["text"][:120])
            resume.save({"card": tcard})
    eventbus.publish("question", public(card))
    line = spoken(card)
    conversation.task_log(f"ASKED ({card['owner']}): {line[:200]}")
    return {"reply": line, "say": line, "activity": [activity], "question": public(card), "by_code": True}


def current():
    """The open card, or None (none, or it expired - then its owner hears it was dropped)."""
    card = session.state.get("question")
    if card and time.time() - card["ts"] > ASK_SECS:
        _drop(card, "expired")
        return None
    return card


def _drop(card, why):
    session.state.pop("question", None)
    eventbus.publish("question", {"id": card["id"], "closed": why})
    on_drop = HANDLERS.get(card["owner"], (None, None))[1]
    if on_drop:
        try:
            on_drop(card)
        except Exception:
            log_exception("asking: on_drop")


# ------------------------------------------------------------------ checking a tap

def _check(card, given):
    """A tap's answers ({key: value | [values] | {"other": text} | {"skip": true}}) -> {key: {"value", "how"}}.
    Raises ValueError with what's wrong: only answers the card offered get through."""
    out = {}
    given = given if isinstance(given, dict) else {}
    for q in card["questions"]:
        a = given.get(q["key"])
        values = [c["value"] for c in q["choices"]]
        if a is None or (isinstance(a, dict) and a.get("skip")):
            if q["guess"] is None:
                raise ValueError(f"“{q['text'][:60]}” needs an answer.")
            out[q["key"]] = {"value": q["guess"], "how": "skip"}
        elif isinstance(a, dict) and "other" in a:
            text = str(a["other"] or "").strip()[:300]
            if not (q["other"] or q["kind"] == "text") or not text:
                raise ValueError("That question has no Other… answer.")
            out[q["key"]] = {"value": text, "how": "other"}
        elif q["kind"] == "many":
            picked = [str(v) for v in (a if isinstance(a, list) else [a])]
            if not picked or any(v not in values for v in picked):
                raise ValueError("Pick from the choices offered.")
            out[q["key"]] = {"value": [v for v in values if v in picked], "how": "tap"}
        elif q["kind"] == "text":
            text = str(a).strip()[:500]
            if not text:
                raise ValueError("Type an answer, or skip it.")
            out[q["key"]] = {"value": text, "how": "tap"}
        else:
            if str(a) not in values:
                raise ValueError("Pick from the choices offered.")
            out[q["key"]] = {"value": str(a), "how": "tap"}
    return out


def _as_words(card, answers):
    """A checked answer as the user's words in the chat ("Sort my photos", "Yes", "Home, Docs")."""
    said = []
    for q in card["questions"]:
        a = answers[q["key"]]
        label = {c["value"]: c["label"] for c in q["choices"]}
        if a["how"] == "skip":
            said.append("(you choose)")
        elif isinstance(a["value"], list):
            said.append(", ".join(label.get(v, v) for v in a["value"]))
        else:
            said.append(label.get(a["value"], a["value"]))
    return "; ".join(said)


# ------------------------------------------------------------------ words

def _norm(s):
    return " ".join(re.findall(r"\w+", str(s or "").lower()))


def _by_label(q, text):
    """The choice whose label the words name ("sort my photos", "the photos one")."""
    t = _norm(text)
    if not t:
        return None
    for c in q["choices"]:
        lab = _norm(c["label"])
        if lab and (t == lab or (len(t) >= 4 and t in lab) or (len(lab) >= 4 and lab in t)):
            return c["value"]
    return None


def _word_answer(q, text):
    """One question from words -> {"value", "how"} or None (the words don't answer it)."""
    if intents.SKIP.search(text):
        return {"value": q["guess"], "how": "skip"} if q["guess"] is not None else None
    other = intents.OTHER_ANSWER.match(text)
    if other and (q["other"] or q["kind"] == "text"):
        return {"value": other.group(1).strip()[:300], "how": "other"}
    if q["kind"] == "text":
        return None if intents.NO.search(text) else {"value": text.strip()[:500], "how": "words"}
    values = [c["value"] for c in q["choices"]]
    if q["kind"] == "yesno":
        yes, no = values[0], values[-1]
        if intents.NO.search(text):
            return {"value": no, "how": "words"}
        if intents.YES.search(text) or intents.RESUME.search(text):
            return {"value": yes, "how": "words"}
        hit = _by_label(q, text)
        return {"value": hit, "how": "words"} if hit else None
    if q["kind"] == "many":
        if intents.ALL_OF_THEM.search(text):
            return {"value": values, "how": "words"}
        picked = {values[i] for i in intents.choices(text) if i < len(values)}
        picked |= {c["value"] for c in q["choices"] if _norm(c["label"]) and _norm(c["label"]) in _norm(text)}
        return {"value": [v for v in values if v in picked], "how": "words"} if picked else None
    i = intents.choice(text)
    if i is not None and i < len(values):
        return {"value": values[i], "how": "words"}
    hit = _by_label(q, text)
    if hit:
        return {"value": hit, "how": "words"}
    if len(values) == 1 and (intents.YES.search(text) or intents.RESUME.search(text)):
        return {"value": values[0], "how": "words"}
    return None


def words(card, text):
    """Typed / spoken words -> answers, or None. Words answer the first question; a second one takes its guess."""
    first, rest = card["questions"][0], card["questions"][1:]
    a = _word_answer(first, text)
    if a is None or any(q["guess"] is None for q in rest):
        return None
    return {first["key"]: a, **{q["key"]: {"value": q["guess"], "how": "skip"} for q in rest}}


def from_words(text):
    """body.answer, before anything else. -> (handled, reply or None). handled=False: no card, or the words weren't
    an answer (the card is dropped and the words go on as a new request). A reply of None with handled=True means
    the owner queued work for the brain (resume: the task to carry on)."""
    card = current()
    stash = session.state.pop("answer_now", None)
    if card is None:
        return False, None
    if stash and stash["id"] == card["id"]:
        answers = stash["answers"]
    else:
        answers = words(card, protocol.unspoken(text).strip())
    if answers is None:
        conversation.task_log(f"QUESTION dropped (not an answer): {text[:80]}")
        _drop(card, "moved on")
        return False, None
    session.state.pop("question", None)
    session.state.pop("last_reply", None)
    session.state.pop("stop_why", None)
    eventbus.publish("question", {"id": card["id"], "closed": "answered"})
    conversation.task_log(f"ANSWERED ({card['owner']}): {answers}")
    on_answer = HANDLERS.get(card["owner"], (None, None))[0]
    if on_answer is None:
        return True, {"reply": "Thanks!", "say": "Thanks!", "activity": ["Got your answer"], "by_code": True}
    return True, on_answer(card, answers)


def tap(qid, given, prefix="", quiet=False, remote=False):
    """A tap on a card (the chat, the strip, /tasks, the phone). -> (reply dict, http status)."""
    card = current()
    if card is None or card["id"] != qid:
        return {"error": "That question has expired - ask me again if you still want it."}, 410
    try:
        answers = _check(card, given)
    except ValueError as e:
        return {"error": str(e)}, 400
    session.state["answer_now"] = {"id": qid, "answers": answers}
    return body.answer(prefix + _as_words(card, answers), quiet=quiet, remote=remote)


@api.app.post("/api/answer")
def answer_post():
    b = api._body()
    out, status = tap(api._text(b, "id"), b.get("answers"))
    return jsonify(out), status


@api.app.get("/api/question")
def question_get():
    """The open card (a page that opens later shows it too), or {}."""
    return jsonify(public(current()) or {})


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import body, conversation, resume, session  # noqa: E402
