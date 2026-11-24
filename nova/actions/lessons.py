"""Lessons from the user's corrections (manager plan 4f) - pure code, no brain call.

When the user corrects her, a one-line lesson goes on the daily question list (questions.py, kind "lesson"). It is
NOT in her knowledge bank yet: only when the user ticks it (a yes to the daily question, or ✓ on the Knowledge page)
does it become a lesson she reads before answering (knowledge.learn_lesson, source "user"). Section 9, answer 4.

Three kinds of correction, read from the user's next request and the task before it:
  - "don't do X" / "never X" / "stop doing X"  -> "Don't X."
  - "no, the other folder" / "wrong window" / "that's not what I asked" -> "For “<last request>”: <what she did> was
    wrong - the user said “<their words>”."
  - Stop, then the same request again in other words -> "For “<last request>”: the user stopped me at <step> and asked
    again as “<new words>”."

The review brain (learning.review_turn) may phrase the same correction better a moment later; its lesson then
replaces the text of this one (`offer(..., replace=qid)`), so the user is asked once.
"""
import re
import time

from nova import intents
from nova.actions import questions
from nova.log import log_exception

DONT_RE = re.compile(r"^\W*(?:no\W+|nope\W+|and\W+)?(?:please\W+)?(?:nova\W+)?(?:don'?t|do not|never|stop doing)\s+"
                     r"(?:ever\s+)?(?!(?:worry|mind|forget|bother|know|understand|think|care|want|need|have|like|get|"
                     r"see|hear|remember|be|stop|go)\b)(?P<what>.{3,140}?)[\s.!]*$", re.I)
# "don't do that": nothing to learn from the words alone - the lesson comes from what she did.
VAGUE_RE = re.compile(r"^(do )?(that|this|it)( again)?$|^doing (that|this|it)$|^(it|that) (now|please)$|^now$", re.I)
OTHER_RE = re.compile(r"\b(the other (one|folder|file|window|app|tab|drive|song|video)|not (that|this) (one|folder|file|"
                      r"window|app|tab)|wrong (folder|file|one|window|app|tab|drive|song|video|person|place))\b", re.I)
WINDOW_SECS = 30 * 60           # a correction or redo counts only this soon after the task it is about
SIMILAR = 0.5                   # Stop + redo: at least this share of the two requests' words in common
_WORD_RE = re.compile(r"[a-z0-9]+")
_FILLERS = {"a", "an", "the", "to", "my", "me", "please", "nova", "and", "it", "in", "on", "for", "of", "can", "you"}


def _words(text):
    return {w for w in _WORD_RE.findall(str(text or "").lower()) if w not in _FILLERS}


def _short(text, n):
    line = " ".join(str(text or "").split())
    return line if len(line) <= n else line[: n - 1].rstrip() + "…"


def _did(task):
    """What she did last in that task: 'open_app notepad' (the newest step that ran), or ""."""
    from nova.actions import appnotes
    for st in reversed((task or {}).get("trace") or []):
        if st.get("ok"):
            return appnotes.step_text(st)
    return ""


def from_request(last, text, now=None):
    """The user's new request `text` after the task `last` -> a lesson dict {situation, lesson, why}, or None."""
    now = now or time.time()
    said = _short(text, 160)
    if not said:
        return None
    prev = last if last and last.get("goal") and now - float(last.get("started") or 0) < WINDOW_SECS else None
    goal = _short((prev or {}).get("goal"), 100)
    m = DONT_RE.match(said)
    if m and not VAGUE_RE.match(m.group("what").strip()):
        what = m.group("what").strip()
        what = what[0].lower() + what[1:]
        return {"situation": goal, "lesson": f"Don't {what}.", "why": f"you told me “{said}”"}
    if prev is None or not (prev.get("trace") or []):
        return None
    did = _did(prev)
    if m or intents.CORRECTION.search(said) or OTHER_RE.search(said):
        lesson = (f"{did} was wrong" if did else "What I did was wrong") + f" - the user said “{said}”."
        return {"situation": goal, "lesson": lesson, "why": f"you corrected me after “{goal}”"}
    if prev.get("stopped"):
        a, b = _words(prev.get("goal")), _words(said)
        if a and b and a != b and len(a & b) / len(a | b) >= SIMILAR:
            where = f" at “{did}”" if did else ""
            return {"situation": goal, "lesson": f"The user stopped me{where} and asked again as “{said}” - do it that way.",
                    "why": "you pressed Stop and asked again in other words"}
    return None


def _key(lesson):
    return "lesson:" + " ".join(sorted(_words(lesson)))[:200]


def offer(situation, lesson, why, replace=None, now=None):
    """Queue the lesson on the daily list (or rewrite question `replace`, when it is still open). -> its id or None.
    Nothing secret-looking is ever queued."""
    from nova.actions.knowledge import SECRET
    situation, lesson = _short(situation, 120), _short(lesson, 220)
    if not lesson or SECRET.search(situation + " " + lesson):
        return None
    text = "Shall I keep this lesson? " + lesson + (f" (When: {situation}.)" if situation else "")
    data = {"situation": situation, "lesson": lesson}
    try:
        if replace and questions.update(replace, text=text, data=data, key=_key(lesson)):
            return replace
        return questions.add("lesson", text, why, data=data, key=_key(lesson), now=now)
    except Exception:
        log_exception("lesson offer")
        return None


def from_turn(last, text, now=None):
    """The whole path for one new request: a correction -> a lesson on the daily list. -> its id or None."""
    found = from_request(last, text, now)
    return offer(found["situation"], found["lesson"], found["why"], now=now) if found else None


def accept(q):
    """The user ticked a lesson question: it goes into her knowledge bank (used from now on). -> her answer."""
    from nova.actions import knowledge
    d = (q or {}).get("data") or {}
    if not d.get("lesson"):
        return "Okay."
    knowledge.learn_lesson(d.get("situation", ""), d["lesson"], "mistake", source="user")
    return "Okay - I'll remember that."
