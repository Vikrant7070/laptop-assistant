"""The Judge: decides whether a task is really done (manager plan, phase 1).

Order of trust:
  1. Deterministic checks (checks.py) made for the goal. If any check fails, the task is NOT done, and the vision
     model can't overrule that.
  2. All checks pass and together they cover the whole goal: done, with no screen look needed.
  3. No check covers the goal (a video really playing, an email's wording): the vision model may look once, at the
     end. Its answer is advisory only: a "no" gets one nudge, then a question to the user, never a hard overrule.

Checks come from two places: code for the common goal shapes (goal_checks), and otherwise up to 3 checks the brain
proposes from the checks.py list (parse_proposed). Those are validated like skill `expect:` lines: invalid ones are
dropped, never fixed.
"""
import os
import re

from nova import prompts as promptfiles

VISION_ENV = "NOVA_VISION_CHECK"

# "please", "hey nova, can you…": words that carry no goal.
_LEAD = re.compile(r"^(?:(?:hey|hi|ok|okay)\s+)?(?:nova|makima|lucy)?[\s,]*(?:please\s+)?"
                   r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?", re.I)
_SPLIT = re.compile(r"\s*(?:,|;|\band then\b|\bthen\b|\band\b)\s*", re.I)
_OPEN = re.compile(r"^(?:open(?:\s+up)?|launch|start|run|bring up|go to|visit|navigate to)\s+(.+)$", re.I)
_DOMAIN = re.compile(r"\b([a-z0-9-]+)\.(?:com|org|net|in|io|dev|ai|co|app|gov|edu|me|tv)\b", re.I)
# "switch windows to dark mode", "make windows dark": the thing being changed is Windows or its theme. "make it
# darker" is brightness, not the colour mode, so "dark" must end there (\b) and "it" is not one of the things.
_THEME_TO = r"\b(?:(?:switch|change|set|turn)\s+(?:windows|the\s+theme|everything)\s+to|make\s+(?:windows|the\s+theme|everything))\s+"
_DARK_ON = re.compile(r"\b(?:(?:turn|switch|put)\s+on|enable|switch\s+to|use|go)\s+(?:the\s+)?dark\s*(?:mode|theme)\b"
                      r"|\bdark\s*(?:mode|theme)\s+on\b"
                      r"|" + _THEME_TO + r"dark\b", re.I)
_DARK_OFF = re.compile(r"\b(?:(?:turn|switch|put)\s+off|disable)\s+(?:the\s+)?dark\s*(?:mode|theme)\b"
                       r"|\bdark\s*(?:mode|theme)\s+off\b"
                       r"|\b(?:(?:turn|switch|put)\s+on|enable|switch\s+to|use|go)\s+(?:the\s+)?light\s*(?:mode|theme)\b"
                       r"|\b(?:go\s+)?back\s+to\s+(?:the\s+)?light\s*(?:mode|theme)\b"
                       r"|" + _THEME_TO + r"light\b", re.I)
_FULL = re.compile(r"\b(?:in\s+)?full\s?-?screen\b", re.I)
_FILLER = {"my", "the", "a", "an", "app", "application", "program", "folder", "website", "site", "page", "window",
           "for", "me", "please", "now", "up"}
# A part that starts with one of these is its own action, not more things to open ("open notepad and type hi").
_VERBS = {"type", "write", "play", "search", "find", "send", "click", "close", "press", "scroll", "make", "turn",
          "switch", "put", "set", "go", "show", "tell", "read", "save", "copy", "paste", "delete", "move", "watch",
          "listen", "check", "look", "maximize", "minimize", "resize", "enable", "disable", "use", "add", "create"}
_VAGUE = {"it", "this", "that", "them", "something", "anything", "new", "file", "tab", "one"}


def _strip_lead(text):
    return _LEAD.sub("", str(text or "").strip()).strip(" .!?")


def _target(words):
    """'my downloads folder' -> 'downloads'; '' when what's left is too vague to check by window title."""
    if _DOMAIN.search(words):
        return _DOMAIN.search(words).group(1).lower()
    left = [w for w in re.findall(r"[\w'+.-]+", words.lower()) if w not in _FILLER]
    if left in (["explorer"], ["file", "explorer"], ["windows", "explorer"]):
        # Windows 11 titles these windows "Downloads - File Explorer". "explorer" alone would also match every titled
        # window of explorer.exe, which runs the taskbar (done too early), and "windows explorer" is never in a title
        # (a failed check: "not done" when it was). "file" alone is too vague; "file explorer" isn't.
        return "file explorer"
    if not left or len(left) > 3 or any(w in _VAGUE for w in left):
        return ""
    return " ".join(left)


def clause_check(clause):
    """One part of a goal -> a list of checks for it (empty if code has no check for this shape)."""
    c = clause.strip(" .!?")
    if _DARK_OFF.search(c):
        return [{"check": "dark_mode", "arg": "off"}]
    if _DARK_ON.search(c):
        return [{"check": "dark_mode", "arg": "on"}]
    out = []
    full = bool(_FULL.search(c))
    c = _FULL.sub("", c).strip()
    m = _OPEN.match(c)
    if m:
        name = _target(m.group(1))
        if name:
            out.append({"check": "window_open", "arg": name})
        elif not full:
            return []
    if full:
        out.append({"check": "fullscreen", "arg": ""})
    return out


def goal_checks(goal):
    """The checks code can make for a goal: {"checks": [...], "covers": bool}.
    covers is True only when every part of the goal got a check, so passing checks mean the WHOLE task is done.
    When only some parts got one ('open Notepad and type hello'), the checks still count as must-be-true, but
    passing them doesn't prove the task is done."""
    parts = [p for p in _SPLIT.split(_strip_lead(goal)) if p.strip()]
    found, covered, verb = [], 0, ""
    for p in parts:
        m = _OPEN.match(p.strip())
        if m:
            verb = p.strip()[:m.start(1)]
        elif verb and p.strip().split()[0].lower() not in _VERBS:
            p = verb + p.strip()              # "open calculator and paint": "paint" means "open paint"
        else:
            verb = ""
        cs = clause_check(p)
        covered += bool(cs)
        found += [c for c in cs if c not in found]
    return {"checks": found, "covers": bool(parts) and covered == len(parts)}


PROPOSE_PROMPT = promptfiles.load("propose_checks")


def parse_proposed(text, limit=3):
    """The brain's answer -> up to `limit` valid checks. Invalid lines are dropped, never fixed. NONE -> []."""
    from nova.actions import skills
    out = []
    for line in str(text or "").splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip("`")
        if not line or line.upper().startswith("NONE"):
            continue
        chk = skills.parse_check(line)
        if chk and chk["check"] != "result_has" and chk not in out:
            out.append(chk)
        if len(out) >= limit:
            break
    return out


def run_checks(checks_list, ctx=None, timeout=5.0, evaluate=None):
    """Run each check (waiting up to `timeout` s for it to come true). Returns [{check, arg, ok, seen}]."""
    if evaluate is None:
        from nova.actions import checks
        evaluate = checks.evaluate
    out = []
    for c in checks_list:
        ok, seen = evaluate(c, ctx or {}, timeout=timeout)
        out.append({"check": c["check"], "arg": c.get("arg", ""), "ok": bool(ok), "seen": seen})
    return out


def vision_on():
    """NOVA_VISION_CHECK=0 turns the Judge's screen look off, for A/B runs (do tasks finish as well without it?).
    Read on every call, so it can be flipped without restarting. Anything else, or unset, leaves it on."""
    return os.environ.get(VISION_ENV, "1").strip() != "0"


def needs_vision(results, covers, strict=True):
    """May the screen look help? Only when no check failed and the checks don't already prove the whole goal.
    strict=False (checks the brain proposed): a failed one may itself be wrong, so the look may still help."""
    failed = any(not r["ok"] for r in results)
    if failed:
        return not strict
    return not (results and covers)


def decide(results, covers, vision=None, strict=True):
    """The trust order. results: run_checks output. vision: the screen look's TASK DONE answer (True/False/None).
    strict: the checks were made by code and are trusted when they fail. Checks the brain proposed (strict=False)
    can prove 'done' when they all pass, but a failed one only means 'not sure' (live tests showed a small brain
    proposing checks that could never pass, like the window title before typing changed it).
    Returns {"done": True|False|None, "source": "checks"|"vision"|"none", "advisory": bool, "why": str}."""
    failed = [r for r in results if not r["ok"]]
    if failed and not strict:
        results, covers = [], False        # fall through to the screen look, as if no check fitted
    if failed and strict:
        return {"done": False, "source": "checks", "advisory": False,
                "why": "; ".join(f"{describe(r)} -> {r['seen']}" for r in failed)}
    if results and covers:
        return {"done": True, "source": "checks", "advisory": False,
                "why": "; ".join(f"{describe(r)} -> {r['seen']}" for r in results)}
    if vision is not None:
        return {"done": bool(vision), "source": "vision", "advisory": True,
                "why": "the screen look says " + ("it's done" if vision else "it isn't done")}
    return {"done": None, "source": "none", "advisory": True, "why": "nothing could check this"}


def describe(r):
    return r["check"] + (f' "{r["arg"]}"' if r.get("arg") else "")


_PLAIN = {"window_open": 'no "{a}" window is open', "front_window": 'the "{a}" window isn\'t in front',
          "page_has": 'the screen doesn\'t show "{a}"', "page_lacks": 'the screen still shows "{a}"',
          "fullscreen": "the window isn't full screen", "dark_mode": "dark mode isn't {a}",
          "process_running": '"{a}" isn\'t running', "result_has": 'the step\'s result doesn\'t mention "{a}"'}


def plain_failure(results):
    """What the user hears when a check failed: 'no "spotify" window is open'."""
    return " and ".join(_PLAIN.get(r["check"], "{c} failed").format(a=r.get("arg", ""), c=r["check"])
                        for r in results if not r["ok"])


def record(goal, checks_list, results, vision_note, verdict):
    """The verdict record every checked task ends with (the episode log in phase 2 stores these)."""
    return {"goal": goal, "checks": [describe(c) for c in checks_list],
            "results": [{"check": describe(r), "ok": r["ok"], "seen": r["seen"]} for r in results],
            "vision_note": (vision_note or "")[:300], "verdict": verdict["done"], "source": verdict["source"]}
