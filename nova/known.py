"""What a task knows, and what it still has to ask (manager plan 4d, Magentic-One's task ledger). Pure code.

On the task card, kept by code:

    card["known"] = [{"key", "value", "how": "given" | "found" | "guess"}]
        given  - in the request (paths, links, emails, "quoted text" - from_goal), or the user's answer
        found  - she found it on the way (note_fact), or a saved answer from before (the facts on the Knowledge page)
        guess  - her guess, marked as one; never used to answer a question
    card["asks"]  = [{"key", "text", "why", "now", "state": "open" | "asked"}]   questions waiting for the user

The brain sees both on the card ("KNOWN: …", "ASKED THE USER: …"). ask_user (nova/actions/tools/taskcard.py) answers
from what is known first; the rest waits here while other steps go on, and code asks them all on one card when she
has nothing else to do (nova/core/app/watching.gather).
"""
import re

HOWS = ("given", "found", "guess")
MAX_KNOWN = 20
MAX_ASKS = 6
VALUE_CHARS = 120
current_card = None          # () -> the running task's card; set by the app (nova/core/app/watching.py)

_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\s\"'<>|?*]+")
_URL = re.compile(r"\bhttps?://[^\s\"'<>]+", re.I)
_EMAIL = re.compile(r"\b[\w.+'-]+@[\w-]+(?:\.[\w-]+)+\b")
_QUOTED = re.compile(r"[\"“]([^\"”]{2,120})[\"”]")


def key_of(text):
    """'GitHub user name' -> 'github_user_name'."""
    return "_".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))[:40]


def _clean(value, n=VALUE_CHARS):
    from nova import task_state
    return task_state.clean(value, n)


def add(card, key, value, how="found"):
    """Put a fact on the card (one per key). A guess never replaces a given or found fact. -> the entry, or None."""
    if card is None or how not in HOWS:
        return None
    key, value = key_of(key), _clean(value)
    if not key or not value:
        return None
    known = card.setdefault("known", [])
    old = next((k for k in known if k["key"] == key), None)
    if old is not None:
        if how == "guess" and old["how"] != "guess":
            return old
        known.remove(old)
    entry = {"key": key, "value": value, "how": how}
    known.append(entry)
    del known[: max(0, len(known) - MAX_KNOWN)]
    for a in card.get("asks") or []:
        if a["key"] == key and how != "guess":
            a["state"] = "answered"
    return entry


def from_goal(goal):
    """Facts given in the request itself: paths, links, emails, quoted text."""
    out, text = [], str(goal or "")
    for kind, rx in (("path", _PATH), ("link", _URL), ("email", _EMAIL)):
        for i, m in enumerate(rx.findall(text)[:3]):
            out.append({"key": kind if i == 0 else f"{kind}_{i + 1}", "value": _clean(m.rstrip(".,;)")), "how": "given"})
    for i, m in enumerate(_QUOTED.findall(text)[:3]):
        out.append({"key": "quoted" if i == 0 else f"quoted_{i + 1}", "value": _clean(m), "how": "given"})
    return [k for k in out if k["value"]]


def lookup(card, key):
    """A fact (given or found, never a guess) for this key: the exact key, else one whose words cover the key's."""
    key = key_of(key)
    if not key or card is None:
        return None
    facts = [k for k in card.get("known") or [] if k["how"] != "guess"]
    hit = next((k for k in facts if k["key"] == key), None)
    if hit:
        return hit
    want = set(key.split("_"))
    return next((k for k in facts if want and want <= set(k["key"].split("_"))), None)


def queue(card, key, text, why="", now=False):
    """A question for the user, waiting on the card (the same key is queued once). -> the ask."""
    asks = card.setdefault("asks", [])
    key = key_of(key) or key_of(text)
    old = next((a for a in asks if a["key"] == key and a["state"] != "answered"), None)
    if old:
        old["now"] = old["now"] or bool(now)
        return old
    a = {"key": key, "text": _clean(text, 200), "why": _clean(why, 160), "now": bool(now), "state": "open"}
    asks.append(a)
    del asks[: max(0, len(asks) - MAX_ASKS)]
    return a


def pending(card):
    return [a for a in (card or {}).get("asks") or [] if a["state"] == "open"]


def render(card):
    """The card's lines for the brain: what is known, what the user has been asked."""
    lines = []
    known = (card or {}).get("known") or []
    if known:
        lines.append("KNOWN: " + "; ".join(f"{k['key']}={k['value'][:60]}" + (" (a guess)" if k["how"] == "guess" else "")
                                           for k in known[-8:]))
    waiting = [a for a in (card or {}).get("asks") or [] if a["state"] in ("open", "asked")]
    if waiting:
        lines.append("WAITING FOR THE USER'S ANSWER: " + "; ".join(a["text"][:80] for a in waiting[-3:])
                     + " - do the steps that don't need it")
    return lines
