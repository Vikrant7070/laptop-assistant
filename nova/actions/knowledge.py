"""Nova's knowledge bank: who the user is, and what she has learned from her own mistakes.

Everything lives in one readable file on this laptop: data/knowledge.json
  profile  - the user: name, what to call them, work, interests, favourite apps, preferences, routines, people, facts
  lessons  - things Nova got wrong (or found a better way to do), each with the lesson for next time

Nova reads a short summary of it before every answer (prompt_block): her core profile, how the user likes things
done, and the facts and lessons closest to the request (retrieval.py), so the bank can grow without the prompt
growing. She adds to it herself through her tools
(update_profile, learn_lesson, remember_fact), and app.py adds lessons automatically after a failed task or when the
user corrects her. The user can read and edit all of it on the Knowledge page.
"""
import json
import re
import threading
import time

from nova.brain import retrieval
from nova.shared import DATA

FILE = DATA / "knowledge.json"
OLD_MEMORY = DATA / "memory.json"      # the earlier flat list of facts
_lock = threading.RLock()

ONE = ("name", "nickname", "work", "city")                        # one value each
MANY = ("interests", "favourite_apps", "preferences", "routines", "people", "facts")   # lists
FIELDS = ONE + MANY
LABELS = {"name": "Name", "nickname": "Call them", "work": "Work / studies", "city": "Lives in", "interests": "Interests",
          "favourite_apps": "Favourite apps and sites", "preferences": "How they like things done",
          "routines": "Routines", "people": "People in their life", "facts": "Other things to remember"}
KINDS = ("mistake", "improvement", "preference")
ALWAYS = ("preferences",)          # how they like things done: matters for every answer
# Only the relevant part reaches the prompt now, so these are just a sanity limit for the file and the Knowledge page.
MAX_LESSONS, MAX_LIST = 400, 200
SECRET = re.compile(r"password|passcode|\bpin\b|otp|cvv|card number|aadhaar|aadhar|\bpan\b|api[ _-]?key|secret|token|"
                    r"\b\d{12,19}\b|"
                    r"[\w.+-]+@[\w-]+\.[\w.-]+|"          # email addresses
                    r"\+?\d(?:[\s().-]?\d){9,}", re.I)   # phone numbers (10+ digits)
# None of the above is ever stored: the profile summary goes to the cloud brain with every message.


def _blank():
    return {"version": 1, "profile": {**{k: "" for k in ONE}, **{k: [] for k in MANY}}, "lessons": [], "next_id": 1}


def load():
    with _lock:
        try:
            k = json.loads(FILE.read_text(encoding="utf-8"))
        except Exception:
            k = _blank()
            try:   # carry over facts from the earlier memory file
                k["profile"]["facts"] = [str(x) for x in json.loads(OLD_MEMORY.read_text(encoding="utf-8"))][:MAX_LIST]
            except Exception:
                pass  # intentional: the old memory file is optional
        base = _blank()
        for key, val in base["profile"].items():
            k.setdefault("profile", {}).setdefault(key, val)
        k.setdefault("lessons", [])
        k.setdefault("next_id", 1 + max([l.get("id", 0) for l in k["lessons"]] or [0]))
        return k


def save(k):
    with _lock:
        FILE.parent.mkdir(exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(k, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(FILE)


def _clean(text, limit=220):
    return " ".join(str(text or "").split())[:limit].strip()


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in
            {"the", "and", "for", "that", "this", "with", "was", "when", "then", "not", "you", "are", "her", "she", "his", "user"}}


def _alike(a, b):
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.7


# ------------------------------------------------------------------ profile

def update_profile(field, value, remove=False):
    """Add (or remove) something about the user. Returns a short sentence for Nova."""
    field = str(field or "").strip().lower().replace(" ", "_")
    field = {"fact": "facts", "interest": "interests", "preference": "preferences", "routine": "routines", "apps": "favourite_apps",
             "app": "favourite_apps", "job": "work", "call_me": "nickname", "other": "facts", "likes": "interests",
             "person": "people", "location": "city"}.get(field, field)
    value = _clean(value)
    if field not in FIELDS:
        return f"Unknown profile field '{field}'. Use one of: {', '.join(FIELDS)}."
    if not value:
        return "Nothing to save."
    if SECRET.search(value):
        return "I don't keep passwords, PINs, card or ID numbers, emails or phone numbers in my notes - that stays with you."
    with _lock:
        k = load()
        p = k["profile"]
        if field in ONE:
            if remove:
                p[field] = ""
            else:
                p[field] = value
        else:
            if remove:
                before = len(p[field])
                p[field] = [x for x in p[field] if value.lower() not in x.lower()]
                save(k)
                return f"Removed {before - len(p[field])} item(s) from {LABELS[field].lower()}."
            if any(_alike(value, x) or value.lower() == x.lower() for x in p[field]):
                return "I already know that."
            p[field] = (p[field] + [value])[-MAX_LIST:]
        save(k)
    return f"Noted in my knowledge bank ({LABELS[field].lower()})."


def facts():
    return list(load()["profile"]["facts"])


def forget(text):
    """Remove anything in the profile or lessons that contains the text."""
    text = _clean(text).lower()
    if not text:
        return "Tell me what to forget."
    n = 0
    with _lock:
        k = load()
        p = k["profile"]
        for f in ONE:
            if p[f] and text in p[f].lower():
                p[f] = ""
                n += 1
        for f in MANY:
            keep = [x for x in p[f] if text not in x.lower()]
            n += len(p[f]) - len(keep)
            p[f] = keep
        keep = [l for l in k["lessons"] if text not in (l["situation"] + " " + l["lesson"]).lower()]
        n += len(k["lessons"]) - len(keep)
        k["lessons"] = keep
        save(k)
    return f"Forgot {n} item(s)."


# ------------------------------------------------------------------ lessons

def learn_lesson(situation, lesson, kind="mistake", source="nova"):
    """Record what went wrong (or what works better) and what to do next time."""
    situation, lesson = _clean(situation, 160), _clean(lesson, 240)
    kind = kind if kind in KINDS else "mistake"
    if not lesson:
        return "Nothing to save."
    if SECRET.search(situation + " " + lesson):
        return "I don't keep passwords, ID numbers, emails or phone numbers in my notes."
    with _lock:
        k = load()
        for l in k["lessons"]:
            if _alike(lesson, l["lesson"]) or (situation and _alike(situation + " " + lesson, l["situation"] + " " + l["lesson"])):
                l["hits"] = l.get("hits", 1) + 1          # the same mistake again: it matters more
                l["last"] = time.time()
                if len(lesson) > len(l["lesson"]):
                    l["lesson"] = lesson
                save(k)
                return f"I had that lesson already - it has now come up {l['hits']} times, so I'll take it more seriously."
        k["lessons"].append({"id": k["next_id"], "kind": kind, "situation": situation, "lesson": lesson, "hits": 1,
                             "when": time.strftime("%Y-%m-%d %H:%M"), "last": time.time(), "source": source})
        k["next_id"] += 1
        if len(k["lessons"]) > MAX_LESSONS:               # keep the ones that matter: repeated, then recent
            k["lessons"].sort(key=lambda l: (l.get("hits", 1), l.get("last", 0)))
            k["lessons"] = k["lessons"][-MAX_LESSONS:]
            k["lessons"].sort(key=lambda l: l["id"])
        save(k)
    return "Lesson saved in my knowledge bank. I'll do better next time."


def delete_lesson(lesson_id):
    try:
        lesson_id = int(lesson_id)
    except (TypeError, ValueError):
        return 0
    with _lock:
        k = load()
        before = len(k["lessons"])
        k["lessons"] = [l for l in k["lessons"] if l.get("id") != lesson_id]
        save(k)
        return before - len(k["lessons"])


def _ranked(lessons):
    now = time.time()
    return sorted(lessons, key=lambda l: l.get("hits", 1) * 3 - (now - l.get("last", now)) / 86400 / 7, reverse=True)


def _lesson_text(l):
    return f"{l['situation']} -> {l['lesson']}" if l.get("situation") else l["lesson"]


def _relevant(query, items, text_of):
    """(score, item) for the items about the query, best first."""
    if not query.strip() or not items:
        return []
    bar = retrieval.relevant_bar()
    s = retrieval.scores(query, [text_of(x) for x in items])
    return sorted(((sc, x) for sc, x in zip(s, items) if sc >= bar), key=lambda p: -p[0])


def _profile_items(p, fields):
    return [(f, v) for f in fields for v in p[f]]


def _group(items):
    by = {}
    for f, v in items:
        by.setdefault(f, []).append(v)
    return [f"{LABELS[f]}: " + "; ".join(vs) for f, vs in sorted(by.items(), key=lambda x: FIELDS.index(x[0]))]


def recall(about=""):
    """What Nova knows, as text. With `about`, only the parts that are about it (by meaning, not just same words)."""
    k = load()
    p = k["profile"]
    items = [(f, p[f]) for f in ONE if p[f]] + _profile_items(p, MANY)
    lessons = _ranked(k["lessons"])
    if about.strip():
        q = _words(about)
        hit = lambda text: q & _words(text)                                   # noqa: E731
        items = [x for _, x in _relevant(about, items, lambda fv: f"{LABELS[fv[0]]}: {fv[1]}")] + \
            [fv for fv in items if hit(fv[1] + " " + LABELS[fv[0]])]
        lessons = [l for _, l in _relevant(about, lessons, _lesson_text)] + [l for l in lessons if hit(_lesson_text(l))]
        items, lessons = list(dict.fromkeys(items)), list({l["id"]: l for l in lessons}.values())
    out = _group(items)
    for l in lessons[:15]:
        out.append(f"Lesson ({l['kind']}, came up {l.get('hits', 1)}x): {l['situation']} -> {l['lesson']}")
    return "\n".join(out) or ("Nothing in my knowledge bank about that yet." if about else "My knowledge bank is still empty.")


def prompt_block(query="", max_items=8, max_lessons=6, max_chars=1400):
    """The short summary Nova reads before answering `query`: always her core profile and how the user likes things
    done; then the facts and lessons that are about this request. When few are, the most recent facts and the
    most-repeated lessons fill in, so she still knows the user a little during small talk."""
    k = load()
    p = k["profile"]
    who = [(f, p[f]) for f in ONE if p[f]] + [(f, v) for f in ALWAYS for v in p[f][-6:]]
    rest = _profile_items(p, [f for f in MANY if f not in ALWAYS])
    facts = [x for _, x in _relevant(query, rest, lambda fv: f"{LABELS[fv[0]]}: {fv[1]}")][:max_items]
    if len(facts) < 4:                      # a little general background: the newest thing in each list
        facts += [fv for fv in reversed(rest) if fv not in facts and fv[0] not in {f for f, _ in facts}][:4 - len(facts)]
    who += facts
    ranked = _ranked(k["lessons"])
    lessons = [l for _, l in _relevant(query, ranked, _lesson_text)][:max_lessons]
    lessons += [l for l in ranked if l not in lessons][:max(0, 3 - len(lessons))]   # the mistakes she repeats most
    lines = []
    if who:
        lines.append("What you know about the user (use it naturally, don't recite it):\n- " + "\n- ".join(_group(who)))
    if lessons:
        lines.append("Lessons from your own past mistakes - follow them:\n- " +
                     "\n- ".join(f"{l['lesson']}" + (f" (when: {l['situation']})" if l["situation"] else "") for l in lessons))
    text = "\n\n".join(lines)
    return ("\n\n" + text[:max_chars]) if text else ""


def seed(name=""):
    """First run: start the bank with what the user has already chosen, so it isn't empty."""
    with _lock:
        if FILE.exists():
            return
        k = load()
        p = k["profile"]
        if name and not p["name"]:
            p["name"] = name
        p["preferences"] += [x for x in ("Speaks English with Nova", "Likes short spoken answers",
                                         "Chose the desi-dost personality and a natural voice",
                                         "Does not want extra windows popping up while talking to Nova") if x not in p["preferences"]]
        save(k)
    learn_lesson("Pressing a keyboard shortcut such as Ctrl+T",
                 "Bring the right window to the front first and check the shortcut landed there before saying it is done.",
                 "mistake", source="starter")
    learn_lesson("Opening a website such as Netflix or YouTube",
                 "Pages take a few seconds to load; wait and look again before deciding a step failed.",
                 "improvement", source="starter")
