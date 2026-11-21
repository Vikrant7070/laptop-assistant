"""The small text protocol Nova's prompts and answers are made of.

There are no structured objects between Nova and a model: context, requests and notes are plain
text, marked with a handful of bracketed prefixes. They used to be spelled out in several modules
and matched with ad-hoc regexes, which made "did we strip every marker before speaking?" a guess.

Everything that emits or recognises one of those markers lives here. `strip_meta` is the gate
before anything is spoken or shown, and `parse_verdict` reads the vision model's answer.

The emitted strings are part of the prompt cache, so changing one is a prompt change, not a
cleanup.
"""
import re

# Request prefixes. `answer()` and the phone/mobile routes put one in front of the user's words.
SPOKEN = "[spoken; speech recognition may have misheard words]"
PHONE_TAG = ("[from the user's phone via Telegram; they are away from the laptop and read your answer as a text "
             "message, so keep it short and don't describe things only visible on the screen]")
MOBILE_TAG = ("[from the user's phone app (the Nova web app on their iPhone); they may be away from the laptop and read "
              "your answer as a short text message, so keep it brief and don't describe things only visible on the screen]")
SPOKEN_RE = re.compile(r"^\[(spoken|from the user's phone)[^\]]*\]\s*")      # spoken or phone request in front

# Hidden context, added by code and never meant to be read out.
NOTE_START = "[note from Nova's system"
WINDOW_NOTE_START = "[open windows"
CHECK_NOTE_START = "\n[check after step"
TOOL_RESULT_START = "TOOL RESULT"
INFO_ECHO_RE = re.compile(r"\(\s*laptop info[^)]*\)|\[\s*now:[^\]]*\]|\[\s*open windows[^\]]*\]", re.I)
STARTED_MARKER_RE = re.compile(r"\[(?:check|spoken)")                    # a whole marker has started
MARKER_RE = re.compile(r"\[(?:check|spoken)[^\]]*\]")                    # a complete check/spoken marker
PARTIAL_MARKER_RE = re.compile(r"\[(?:c(?:h(?:e(?:c)?)?)?|s(?:p(?:o(?:k(?:e(?:n)?)?)?)?)?)?$")   # half-written

# The vision model's answer.
TASK_DONE_RE = re.compile(r"TASK DONE:\s*(yes|no)", re.I)
STEP_WORKED_RE = re.compile(r"STEP WORKED:\s*(yes|no)", re.I)
SCREEN_RE = re.compile(r"SCREEN:\s*(.+)")


def unspoken(text):
    """The user's words without the "[spoken; ...]" (or phone) marker in front."""
    return SPOKEN_RE.sub("", text or "")


def note(body):
    """A hidden note to the model, e.g. "you said it is done but called no tool"."""
    return f"{NOTE_START}] {body}"


def check_note(step, windows):
    """The line appended to a tool result after an on-screen action."""
    return f"{CHECK_NOTE_START} {step}] Windows now, front first: {windows or 'none'}."


def windows_note(summary):
    """The hidden window list appended to a request."""
    return f"\n[open windows, front first: {summary}]"


def strip_echoes(text):
    """Drop the hidden window/timestamp echoes, and anything after a window note starts."""
    return INFO_ECHO_RE.sub("", text or "").split(WINDOW_NOTE_START)[0].strip()


def strip_meta(text):
    """Everything a person may hear: no hidden context, no window list, no "[check ...]" marker.

    The model sometimes repeats a marker (usually the request's own "[spoken] ...") after its
    answer; the text before the first marker wins, or the first non-empty piece after it.
    """
    parts = MARKER_RE.split(strip_echoes(text))
    if not parts:
        return ""
    return parts[0].strip() or " ".join(p.strip() for p in parts[1:]).strip()


def is_meta_line(text):
    """A message that is context or a tool result, not something the user said."""
    return (text or "").startswith((TOOL_RESULT_START, NOTE_START))


def parse_verdict(text):
    """The vision check's answer -> {"done": True/False/None, "screen": "what it sees"}."""
    done = TASK_DONE_RE.search(text or "")
    screen = SCREEN_RE.search(text or "")
    return {"done": (done.group(1).lower() == "yes") if done else None,
            "screen": screen.group(1).strip() if screen else ""}


def parse_worked(text):
    """A skill step's screen check -> {"worked": bool, "screen": what it sees (or the whole answer)}."""
    worked = STEP_WORKED_RE.search(text or "")
    screen = SCREEN_RE.search(text or "")
    return {"worked": bool(worked and worked.group(1).lower() == "yes"),
            "screen": screen.group(1).strip() if screen else (text or "")}
