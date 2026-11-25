"""Shared constants and utilities used by multiple Nova modules.

Avoids duplicating definitions across the tools package, avatar.py, desktop.py, knowledge.py and cloud.py.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # the project folder (nova/ lives inside it)
# Where Nova keeps settings, notes, knowledge and keys. NOVA_DATA points it elsewhere (the tests use a temp folder).
DATA = Path(os.environ.get("NOVA_DATA") or ROOT / "data")

# ── The user's real folders ──
# The small brain guessed C:\Users\Vikrant\Downloads from the user's name (the real one is C:\Users\vikra\Downloads),
# went round in circles and gave up (2026-11-23). The brains are now told the real paths. Desktop and Documents may
# be moved into OneDrive, so Windows is asked (platform known_folder) instead of assuming <home>\<name>.
_folders = {}


def user_folders():
    """{"Home", "Desktop", "Documents", "Downloads"} -> the real paths on this laptop (asked once, then kept)."""
    if not _folders:
        from nova import platform
        _folders["Home"] = str(Path.home())
        for name in ("Desktop", "Documents", "Downloads"):
            _folders[name] = platform.current().known_folder(name) or str(Path.home() / name)
    return dict(_folders)


def folders_line():
    """One line for a prompt: the real folders, so no brain guesses a path from the user's name."""
    return "; ".join(f"{k} = {v}" for k, v in user_folders().items())

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


_ASIDE = _STAGE[1]                                                   # *(I'm dancing!)*
_EMOJI = r"[\U0001F300-\U0001FAFF\U0001F1E6-\U0001F1FF☀-➿‍️]"
_EMOJI_TAIL = re.compile(r"(?:\s*%s){2,}\s*$" % _EMOJI)               # "...anything else? 😊📚🎬"
_EMOJI_LINE = re.compile(r"(?m)^[ \t]*(?:%s[ \t]*)+$\n?" % _EMOJI)


def tidy_reply(text):
    """Her reply without "*(aside)*" lines and the trailing pile of emoji the small model adds to everything (QA
    2026-11-25: 30 of 44 replies ended "😊📚🎬"). Kept out of her history too, so she stops copying it. Speech
    already drops these (wording._clean_for_speech); this is what's on screen and what she reads back."""
    if not text:
        return text
    text = _ASIDE.sub("", text)
    text = _EMOJI_LINE.sub("", _EMOJI_TAIL.sub("", text))
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def tidy_message(msg):
    """tidy_reply on an assistant message for the history (a copy; tool calls untouched)."""
    if isinstance(msg, dict) and msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
        return {**msg, "content": tidy_reply(msg["content"])}
    return msg


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
    """Usable screen rectangle (left, top, right, bottom) excluding the taskbar (nova.platform)."""
    from nova import platform
    return platform.current().work_area()




def autohide_taskbar():
    """(edge, rect) of the taskbar when it is set to auto-hide (edge 0 left, 1 top, 2 right, 3 bottom), else None."""
    from nova import platform
    return platform.current().autohide_taskbar()


def stand_area(area=None, taskbar=...):
    """Where desktop Nova may stand: the work area, but above an auto-hidden taskbar too. Windows counts the whole
    screen as usable when the taskbar auto-hides, so she stood on the bottom row and the taskbar covered her shoes
    whenever it popped up (2026-11-20: 60 px on this laptop)."""
    left, top, right, bottom = area or work_area()
    tb = autohide_taskbar() if taskbar is ... else taskbar
    if tb:
        edge, (tl, tt, tr, tbm) = tb
        # its thickness, in the work area's own scale (a process that isn't DPI-aware gets the work area scaled but
        # the taskbar rectangle not): a top/bottom taskbar spans the full width, a side one the full height
        if edge in (1, 3) and tr > tl:
            thick = round((tbm - tt) * (right - left) / (tr - tl))
            bottom, top = (bottom - thick, top) if edge == 3 else (bottom, top + thick)
        elif edge in (0, 2) and tbm > tt:
            thick = round((tr - tl) * (bottom - top) / (tbm - tt))
            left, right = (left + thick, right) if edge == 0 else (left, right - thick)
    return left, top, right, bottom


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
