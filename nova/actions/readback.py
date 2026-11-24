"""Check a step by reading its result back (manager plan 4c, the whole laptop).

The worker's or the brain's "done" is never the verdict: after a step that changes something, code looks at the
result from somewhere else - the file on disk, the window list, the setting read again. (OpenAdapt's tests: the agent
claimed success on all 30 bad saves; only reading back caught them. Nova's own task exam, 2026-11-23: "File saved"
with no file.)

    read_back(tool, args, out) -> None for a step that changes nothing (reading, listing, looking), else
                                  {"state": "passed" | "failed" | "skipped", "why": "...", "target": "..."}

A step this table has no read-back for is "skipped" with the reason - never left out, never counted as passed.
`target` names what was checked (a path, a window), so a later step that fixes the same thing replaces the verdict.
"""
from pathlib import Path

from nova.actions import checks
from nova.actions.skills import format as skillformat

KEEP_TEXT = 60               # the start of the written text that must be in the file
READ_MAX = 1_000_000         # bytes of a file read back

# Changes nothing by name, beyond the skills' READ_ONLY prefixes (no read-back needed).
QUIET = {"react", "new_move", "go_to_sleep", "more_tools", "escalate", "start_watching", "stop_watching",
         "remember", "add_note", "set_reminder", "web_research", "research_in_background", "hand_off_job",
         "background_jobs", "stop_background_job", "research_report", "list_windows", "media_control",
         "ask_user", "note_fact"}
SELF_CHECKED = {"run_skill": "the skill checks its own steps",
                "undo_last_change": "the undo checks what it put back and says so",
                "start_coding_job": "the worker job is checked when it ends",
                "commit_coding_job": "git says what was committed",
                "retry_coding_job": "the worker job is checked when it ends"}


def _resolve(path):
    from nova.actions.tools import common
    return common._resolve(path)


def _passed(why, target=""):
    return {"state": "passed", "why": why, "target": target}


def _failed(why, target=""):
    return {"state": "failed", "why": why, "target": target}


def _skipped(why, target=""):
    return {"state": "skipped", "why": why, "target": target}


# ------------------------------------------------------------------ files

def _file_has(p, text):
    try:
        with open(p, "rb") as f:
            data = f.read(READ_MAX).decode("utf-8", errors="replace")
    except OSError as e:
        return None, f"couldn't read it back ({e.strerror or e})"
    want = " ".join(str(text or "").split())[:KEEP_TEXT]
    return (not want or want in " ".join(data.split())), ""


def write_text_file(args, out):
    p = _resolve(args.get("path", ""))
    if not p.is_file():
        return _failed(f"{p.name} is missing", str(p))
    if p.stat().st_size == 0 and str(args.get("content") or ""):
        return _failed(f"{p.name} is empty", str(p))
    ok, err = _file_has(p, args.get("content"))
    if ok is None:
        return _skipped(err, str(p))
    return _passed(f"{p.name} has the text", str(p)) if ok else _failed(f"{p.name} doesn't hold what was written", str(p))


def _moved_to(args, out):
    """The real destination: the tool's own 'Moved A -> B', else destination (/ the source's name for a folder)."""
    if " -> " in str(out):
        return Path(str(out).split(" -> ", 1)[1].strip().rstrip("."))
    src, dst = _resolve(args.get("source", "")), _resolve(args.get("destination", ""))
    return dst / src.name if dst.is_dir() else dst


def move_or_rename(args, out):
    src, dst = _resolve(args.get("source", "")), _moved_to(args, out)
    if not dst.exists():
        return _failed(f"{dst.name} is missing", str(dst))
    if src.exists():
        return _failed(f"{src.name} is still there", str(dst))
    return _passed(f"{dst.name} is there and {src.name} is gone", str(dst))


def copy_path(args, out):
    dst = _moved_to(args, out)
    return _passed(f"{dst.name} is there", str(dst)) if dst.exists() else _failed(f"{dst.name} is missing", str(dst))


def create_folder(args, out):
    p = _resolve(args.get("path", ""))
    if str(out).startswith("Nothing created"):        # it was already there: no new folder was made
        return _failed(f"no new folder was made ({p.name} was already there)", str(p))
    return _passed(f"{p.name} is there", str(p)) if p.is_dir() else _failed(f"{p.name} is missing", str(p))


def delete_to_recycle_bin(args, out):
    p = _resolve(args.get("path", ""))
    return _failed(f"{p.name} is still there", str(p)) if p.exists() else _passed(f"{p.name} is gone", str(p))


# ------------------------------------------------------------------ windows and settings

def _check(name, arg, ctx=None, timeout=3.0):
    ok, seen = checks.evaluate({"check": name, "arg": arg}, ctx or {}, timeout=timeout)
    return ok, seen


def open_app(args, out):
    from nova.actions.tools import apps
    name = str(args.get("name") or "").strip()
    if not name:
        return _skipped("no app name")
    target = apps.APPS.get(name.lower(), name)
    for q in dict.fromkeys([name, str(target).removesuffix(".exe")]):
        ok, seen = _check("window_open", q, timeout=5.0)          # a slow app (VS Code) gets its few seconds
        if ok:
            return _passed(seen, name)
    return _failed(f"no {name} window is open", name)


def window_action(args, out):
    name, action = str(args.get("title_or_app") or ""), str(args.get("action") or "focus").lower()
    if action == "focus":
        ok, seen = _check("front_window", name)
        return _passed(seen, name) if ok else _failed(seen, name)
    if action == "close":
        ok, seen = checks.evaluate({"check": "window_open", "arg": name}, {}, timeout=0)
        return _failed(f"{name} is still open", name) if ok else _passed(f"{name} is closed", name)
    return _skipped(f"no read-back for '{action}' yet", name)


def type_text(args, out):
    text = " ".join(str(args.get("text") or "").split())[:40]
    if not text:
        return _skipped("nothing was typed")
    win = str(args.get("into_window") or "")
    ok, seen = _check("page_has", text, {"window": win}, timeout=2.0)
    # Not seen isn't proof: some apps don't show their text to the screen reader (checks.py: a check that can't look
    # is not evidence of failure). So it's skipped, with why, and the Judge's goal checks still decide.
    return _passed(seen, win) if ok else _skipped(f"couldn't see the text in {win or 'the window in front'}", win)


def set_dark_mode(args, out):
    on = args.get("enabled") in (True, "true", "on", 1)
    ok, seen = _check("dark_mode", "on" if on else "off", timeout=1.0)
    return _passed(f"Windows is {seen}", "dark mode") if ok else _failed(f"Windows is still {seen}", "dark mode")


def set_brightness(args, out):
    from nova.actions.tools import common
    want = max(0, min(100, int(args.get("percent") or 0)))
    now = str(common._ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness") or "")
    digits = [int(x) for x in now.split() if x.strip().isdigit()]
    if not digits:
        return _skipped("couldn't read the brightness back", "brightness")
    return (_passed(f"brightness is {digits[0]}%", "brightness") if abs(digits[0] - want) <= 2
            else _failed(f"brightness is {digits[0]}%, not {want}%", "brightness"))


def toggle_wifi(args, out):
    from nova.actions.tools import common
    on = args.get("on") in (True, "true", "on", 1)
    now = str(common._ps('netsh interface show interface name="Wi-Fi"') or "").lower()
    if "administrative state" not in now:
        return _skipped("couldn't read the Wi-Fi state back", "wi-fi")
    is_on = "enabled" in now.split("administrative state", 1)[1][:40]
    return _passed(f"Wi-Fi is {'on' if is_on else 'off'}", "wi-fi") if is_on == on else \
        _failed(f"Wi-Fi is still {'on' if is_on else 'off'}", "wi-fi")


READERS = {"write_text_file": write_text_file, "move_or_rename": move_or_rename, "copy_path": copy_path,
           "create_folder": create_folder, "delete_to_recycle_bin": delete_to_recycle_bin, "open_app": open_app,
           "window_action": window_action, "type_text": type_text, "set_dark_mode": set_dark_mode,
           "set_brightness": set_brightness, "toggle_wifi": toggle_wifi}
NOT_READABLE = {"press_keys": "keys can't be read back", "set_volume": "the volume can't be read back yet",
                "click_element": "no read-back for clicks yet", "click_at": "no read-back for clicks yet",
                "click_nth": "no read-back for clicks yet", "scroll": "nothing to read back"}


def changes_things(tool):
    t = str(tool or "")
    return not (t.startswith(skillformat.READ_ONLY) or t in QUIET)


def read_back(tool, args, out):
    """The read-back for one finished step (see the module doc). Never raises."""
    if not changes_things(tool):
        return None
    if tool in SELF_CHECKED:
        return _skipped(SELF_CHECKED[tool])
    fn = READERS.get(tool)
    if fn is None:
        return _skipped(NOT_READABLE.get(tool, "no read-back for this step yet"))
    try:
        return fn(dict(args or {}), out)
    except Exception as e:
        return _skipped(f"couldn't read it back ({type(e).__name__})")


def note(rb):
    """The line the brain reads under the step's result."""
    if not rb:
        return ""
    return {"passed": f"\n[check: passed - {rb['why']}]", "failed": f"\n[check: FAILED - {rb['why']}. It is NOT done.]",
            "skipped": f"\n[check: skipped - {rb['why']}]"}[rb["state"]]
