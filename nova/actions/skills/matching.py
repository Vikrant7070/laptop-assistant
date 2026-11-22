"""Matching a request to a skill: fillers, the fuzzy floor, pick/match, guessing {input}, the prompt block, similar."""
import re
from nova.core import characters
from nova.brain import retrieval


# Fuzzy matching must clear BOTH bars. (Tuned on the real log with `python -m nova.tools.skill_eval`.)
FUZZY_FLOOR = 0.85            # with the embedding model (measured with nova.skill_eval: 0.75 let "good night" start the morning briefing); FUZZY_FLOOR_WORDS is for the word-overlap fallback


FUZZY_FLOOR_WORDS = 0.6


FUZZY_MARGIN = 0.05           # ... and beat the next-best skill by this much


# Every character's name ("hey lucy, movie time" is the same request as "hey nova, movie time").
_NAMES = tuple(n for n in characters.all_names() if " " not in n) or ("nova",)


FILLERS = (*(f"{g} {n}" for n in _NAMES for g in ("hey", "hi", "ok", "okay", "hello")), *_NAMES,
           "hey", "hi", "ok", "okay", "please", "can you", "could you", "would you", "will you", "so", "just", "now", "and")


TRAILING = ("please", *_NAMES, "now", "for me")


STOPWORDS = set("""a an the and or of to is are was were be been i me my you your it its this that these those can could
would will do does did what which who whom how where when why for on in at with about tell check please hey ok
okay so just some any want need let s from as if then than too very up out one""".split()) | set(_NAMES)


last_decision = ""            # why the last pick() answered as it did - app.new_task writes it to the task log


# ------------------------------------------------------------------ matching

def _norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower())).strip()


def _strip_fillers(q):
    """'hey nova, can you movie time please' -> 'movie time'."""
    changed = True
    while changed and q:
        changed = False
        for f in FILLERS:
            if q == f:
                return ""
            if q.startswith(f + " "):
                q, changed = q[len(f) + 1:], True
        for f in TRAILING:
            if q.endswith(" " + f):
                q, changed = q[:-len(f) - 1], True
    return q


def _content(text):
    return {w for w in _norm(text).split() if w not in STOPWORDS and len(w) > 1}


def _has(q, phrase):
    return f" {phrase} " in f" {q} "


def _phrase_fires(q, p, needs_input):
    """Does the request start this skill outright? One word: only the whole request (or, for a skill that takes
    input, the first word: 'research quantum batteries'). Two words: at the start or the end ('it's movie time'). Three
    or more: anywhere. A loose word in the middle of a sentence ('where did you find out') never starts a skill."""
    n = len(p.split())
    if not n:
        return False
    if q == p:
        return True
    if n == 1:
        return needs_input and q.startswith(p + " ")
    if n == 2:
        return q.startswith(p + " ") or q.endswith(" " + p)
    return _has(q, p)


def _vetoed(q, s):
    words = set(q.split())
    for phrase in s["not_for"] + stats.stats_get(s["name"]).get("learned_not_for", []):
        p = _norm(phrase)
        if not p:
            continue
        if _has(q, p):
            return phrase
        cw = _content(p)
        if len(cw) >= 2 and cw <= words:
            return phrase
    return ""


def _requires_ok(q, s):
    return not s["requires_any"] or any(_has(q, _norm(r)) for r in s["requires_any"] if _norm(r))


def floor():
    return FUZZY_FLOOR if retrieval.ready() else FUZZY_FLOOR_WORDS


def pick(query):
    """The skill this request is about -> (skill, "exact" | "fuzzy"), or (None, None). Exact: the request says the
    skill's name or one of its example phrases. Fuzzy: it only sounds like it - Nova must ask before a fuzzy match
    that changes things (app.is_risky). A request a skill's `not_for` phrase describes never starts it, and a
    request that matches nothing well gets no skill at all (so it is just answered)."""
    global last_decision
    q = _strip_fillers(_norm(query))
    skills = [s for s in library.load() if s["kind"] != "broken" and s["status"] != "deprecated"]
    if not skills or not q:
        last_decision = "SKILL NO MATCH (nothing to match)"
        return None, None
    vetoes, live = [], []
    for s in skills:
        v = _vetoed(q, s)
        if v:
            vetoes.append(f"{s['name']} vetoed by '{v}'")
        else:
            live.append(s)
    exact = []
    for s in live:
        for p in [_norm(s["name"])] + [_norm(e) for e in s["examples"]]:
            if p and _phrase_fires(q, p, s["needs_input"]) and _requires_ok(q, s):
                exact.append((len(p.split()), len(p), s, p))
                break
    if exact:
        _, _, s, p = max(exact, key=lambda e: e[:2])
        last_decision = f"SKILL MATCH exact {s['name']} (phrase '{p}')"
        return s, "exact"
    # A guess needs something to go on: two meaningful words at least, and a skill that has to be told what to work on
    # ("research <topic>") can't start from a guess at all - it has no way to know the topic.
    cands = [s for s in live if s["invoke"] != "explicit_only" and s["status"] == "active" and not s["needs_input"]
             and _requires_ok(q, s)] if len(_content(q)) >= 2 else []
    why = "; ".join(vetoes)
    if not cands:
        last_decision = "SKILL NO MATCH" + (f" ({why})" if why else " (no exact phrase)")
        return None, None
    texts, owner = [], []
    for s in cands:
        for t in [f"{s['name'].replace('-', ' ')}: {s['description']}"] + s["examples"]:
            texts.append(t)
            owner.append(s)
    sc = retrieval.scores(q, texts)
    best = {}
    for s, x in zip(owner, sc):
        best[s["name"]] = max(best.get(s["name"], 0.0), x)
    ranked = sorted(cands, key=lambda s: best[s["name"]], reverse=True)
    top, top_score = ranked[0], best[ranked[0]["name"]]
    second = best[ranked[1]["name"]] if len(ranked) > 1 else 0.0
    own = _content(" ".join([top["name"].replace("-", " ")] + top["examples"]))
    overlap = bool(own & _content(q))
    bar = floor() + (0 if overlap else 0.1)
    detail = f"{top['name']} score={top_score:.2f} 2nd={second:.2f} floor={bar:.2f} overlap={overlap}"
    if top_score < bar:
        last_decision = f"SKILL NO MATCH below the floor ({detail})" + (f" [{why}]" if why else "")
    elif top_score - second < FUZZY_MARGIN:
        last_decision = f"SKILL NO MATCH too close to call ({detail})"
    elif not overlap and top["risk"] == "read_only":
        last_decision = f"SKILL NO MATCH no shared words ({detail})"
    else:
        last_decision = f"SKILL MATCH fuzzy {detail}"
        return top, "fuzzy"
    return None, None


def match(query):
    """The skill this request is about, or None (see pick)."""
    return pick(query)[0]


def guess_input(skill, query):
    """For a routine with {input}: the part of the request after the trigger phrase ("play lofi music" -> "lofi music")."""
    low = query.lower()
    for p in sorted([skill["name"].replace("-", " ")] + skill["examples"], key=len, reverse=True):
        i = low.find(p.lower())
        if p and i >= 0:
            rest = query[i + len(p):].strip(" ,.:-")
            rest = re.sub(r"^(for|about|of|to|on)\s+", "", rest, flags=re.I)
            if rest:
                return rest
    return ""


# The request asks for more than the skill ("research tea and then email it"): the brain has to plan the rest.
MORE_WORK = re.compile(r"\b(and then|then|after that|afterwards|also|as well|plus)\b|\band (?:also )?(?:send|email|mail|open|"
                       r"save|add|tell|show|make|write|put|play|share|text|message|post|commit)\b", re.I)


def direct_args(skill, query):
    """run_skill's arguments when an exact match can run without asking the brain first, else None.
    Only an active routine (its steps run as code anyway), only when the request is just the skill, and only when
    a needed {input} can be read from the request. (Live 2026-11-21: on an exact 'web-research' match the small brain
    called list_windows and focused the user's Netflix window before - or instead of - running the skill.)"""
    if not skill or skill.get("kind") != "routine" or skill.get("status") != "active" or MORE_WORK.search(query or ""):
        return None
    args = {"name": skill["name"]}
    if skill.get("needs_input"):
        guess = guess_input(skill, query)
        if not guess:
            return None
        args["input"] = guess
    return args


def prompt_block(query, matched=None, how="exact"):
    """What goes into the prompt about skills for this request: the matching one in full, and the names of the rest."""
    skills = [s for s in library.load() if s["kind"] != "broken" and s["status"] == "active"]
    if not skills:
        return ""
    lines = []
    if matched and how == "fuzzy":
        lines.append(f"The user MIGHT want your skill '{matched['name']}' ({matched['description'] or 'a routine'}). "
                     f"Call run_skill(name='{matched['name']}') only if the request really asks for that. If it is a "
                     "question or something else, ignore the skill and just help.")
    elif matched:
        if matched["kind"] == "routine":
            arg = ""
            if matched["needs_input"]:
                guess = guess_input(matched, query)
                arg = f", input='{guess}'" if guess else ", input=<what the user wants it for>"
            lines.append(f"Your skill '{matched['name']}' fits this request ({matched['description'] or 'a routine'}). "
                         f"Call run_skill(name='{matched['name']}'{arg}) - it does all the steps itself; don't do "
                         "them one by one.")
        else:
            lines.append(f"Your skill '{matched['name']}' fits this request. Follow it, one tool call at a time:\n"
                         + matched["body"])
    others = [s["name"] for s in skills if s is not matched][:20]
    if others:
        lines.append("Your other saved skills (run one with run_skill): " + ", ".join(others) + ".")
    return "\n\n" + "\n\n".join(lines)


# ------------------------------------------------------------------ library health

def similar(description, examples, skip=""):
    """An existing skill that is nearly the same thing as this one (name, score), or None."""
    mine = f"{description}. {' '.join(examples)}".strip()
    best = (None, 0.0)
    for s in library.load():
        if s["name"] == skip or s["kind"] == "broken":
            continue
        sc = retrieval.scores(mine, [f"{s['description']}. {' '.join(s['examples'])}"])[0]
        if sc > best[1]:
            best = (s["name"], sc)
    bar = 0.9 if retrieval.ready() else 0.8
    return best if best[0] and best[1] >= bar else None


from nova.actions.skills import library, stats  # noqa: E402  (used when the code runs; no cycles)
