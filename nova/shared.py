"""Shared constants and utilities used by multiple Nova modules.

Avoids duplicating definitions across tools.py, avatar.py, desktop.py, knowledge.py and cloud.py.
"""
import ctypes
import os
import re
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # the project folder (nova/ lives inside it)
# Where Nova keeps settings, notes, knowledge and keys. NOVA_DATA points it elsewhere (the tests use a temp folder).
DATA = Path(os.environ.get("NOVA_DATA") or ROOT / "data")

# ── Reactions that Nova's 3D body can perform ──
REACTIONS = (
    "laugh", "dance", "spin", "clap", "surprised", "sad", "confused", "nod", "shake",
    "facepalm", "bow", "love", "yawn", "look", "happy", "wave",
)

# ── Stage directions the brain writes instead of calling react ──
# The small local brain writes '[react: "laugh"]', '*laughs*' or a whole line like '*(I'm dancing - desi vibes!)*'.
# Read aloud, she *said* the reaction instead of doing it (2026-11-18). These are never spoken; the move they name
# is played instead (app.after_turn).
REACT_MARKER_RE = re.compile(r"\[\s*(?:react|reaction|action|gesture|emote|tool)\b[^\]\n]*\]", re.I)   # [react: "laugh"], [react, reaction=clap]
_MARKER_NAME = re.compile(r"[:=]\s*[\"']?\s*([a-z_]+)", re.I)
_MARKER_BARE = re.compile(r"^\[\s*[a-z]+[\s,]+[\"']?([a-z_]+)", re.I)
STAGE_VERBS = {"laughs": "laugh", "laughing": "laugh", "giggles": "laugh", "giggling": "laugh", "chuckles": "laugh",
               "waves": "wave", "waving": "wave", "dances": "dance", "dancing": "dance", "spins": "spin",
               "spinning": "spin", "twirls": "spin", "twirling": "spin", "claps": "clap", "clapping": "clap",
               "bows": "bow", "bowing": "bow", "nods": "nod", "nodding": "nod", "shrugs": "confused",
               "yawns": "yawn", "jumps": "happy", "cheers": "happy", "smiles": "happy", "grins": "happy",
               "facepalms": "facepalm", "gasps": "surprised", "cries": "sad", "sighs": "sad", "hugs": "love"}
_BARE_VERBS = {"laugh": "laugh", "wave": "wave", "dance": "dance", "danced": "dance", "spin": "spin", "spun": "spin",
               "clap": "clap", "clapped": "clap", "bow": "bow", "nod": "nod", "shrug": "confused"}   # inside *( ... )*
_STAGE = [
    REACT_MARKER_RE,
    re.compile(r"\*\(.*?\)\*|\(\*.*?\*\)"),                                      # *(I'm dancing!)*
    re.compile(r"(?m)^[ \t]*([*_])(?![*_\s])[^*_\n]+(?<![\s*_])\1[ \t]*$"),       # a whole line in *italics*
    re.compile(r"\*(?:%s)(?:\s+[a-z]+){0,3}\*" % "|".join(STAGE_VERBS), re.I),   # *laughs* / *spins happily*
]


def stage_directions(text):
    """(text without its stage directions, [the stage directions])."""
    found = []
    for rx in _STAGE:
        text = rx.sub(lambda m: found.append(m.group(0)) or " ", text)
    return text, found


def stage_reaction(found):
    """The move the first stage direction names ('[react: "laugh"]' -> 'laugh', '*spins*' -> 'spin'), or None."""
    for bit in found:
        if REACT_MARKER_RE.fullmatch(bit.strip()):
            m = _MARKER_NAME.search(bit.strip())
            if m:
                return (m.group(1) or m.group(2)).lower()
        for word in re.findall(r"[a-z]+", bit.lower()):
            if word in STAGE_VERBS or word in _BARE_VERBS:
                return STAGE_VERBS.get(word) or _BARE_VERBS[word]
    return None


def work_area():
    """Usable screen rectangle (left, top, right, bottom) excluding the taskbar."""
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


def ps_escape(s):
    """Escape a string for safe interpolation inside a PowerShell single-quoted string.

    PowerShell single-quoted strings treat everything literally except ``'`` (and its typographic
    variants, which PowerShell also accepts as quotes), which must be doubled. This prevents shell
    injection when tool arguments (which come from the LLM) are passed to PowerShell commands.
    Always use it as  f"'{ps_escape(x)}'".
    """
    return "".join(c * 2 if c in "'‘’‚‛" else c for c in str(s))


LOCAL_HOSTS = ("127.0.0.1", "localhost")


def host_allowed(host, port):
    """Is an HTTP Host header one of ours? (Stops DNS-rebinding pages from talking to Nova's local servers.)"""
    host = (host or "").lower()
    return host in {f"{h}:{port}" for h in LOCAL_HOSTS} | (set(LOCAL_HOSTS) if port == 80 else set())
