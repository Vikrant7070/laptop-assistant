"""Undo for level-1 steps (manager plan 4e): a change that can be undone keeps an undo record.

    snap = before(call)            just before the step runs: what is there now (an old value, an old file) - or None
    rec  = after(call, out, ok, snap, task_id)  the step really ran: the record, saved to data/undo.json - or None
    undo(which)                    put it back: "latest", a record number, or words from what it did. -> a sentence
    records(task_id=None)          the records (newest last), for the strip and /tasks

What can be undone, and how:
    move_or_rename   moved back (only when nothing was at the new place before)
    copy_path        the copy goes to the Recycle Bin (only a copy that wasn't there before)
    create_folder    the new folder goes to the Recycle Bin (only while it is still empty)
    write_text_file  the old text is put back (kept in data/undo/), or a new file goes to the Recycle Bin
    set_dark_mode    back to the old colour mode          set_brightness   back to the old level
    set_volume       the same number of steps the other way (mute <-> unmute)
    toggle_wifi      back to the old on / off             set_wallpaper    back to the old picture (if still there)
Nothing is ever deleted for good: what is taken away goes to the Recycle Bin. A record is used once.
"""
import os
import shutil
import threading
import time
from pathlib import Path

from nova import store
from nova.log import log_exception

KEEP = 50                    # records kept
BACKUP_MAX = 5_000_000       # bytes: a bigger file's old text isn't kept (no undo for that write)
UNDOABLE = ("move_or_rename", "copy_path", "create_folder", "write_text_file", "set_dark_mode", "set_brightness",
            "set_volume", "toggle_wifi", "set_wallpaper")
OPPOSITE = {"up": "down", "down": "up", "mute": "unmute", "unmute": "mute"}
_lock = threading.RLock()


def _path():
    return store.current().file("undo.json")


def _backups():
    return store.current().file("undo")


def _resolve(path):
    from nova.actions.tools import common
    return common._resolve(path)


def _final(src, dst):
    """Where move / copy really puts it: into dst when dst is a folder that is already there."""
    return dst / src.name if dst.is_dir() else dst


def records(task_id=None):
    data = store.current().read_json(_path(), {"next": 1, "records": []}) or {"next": 1, "records": []}
    recs = data.get("records") or []
    return [r for r in recs if task_id is None or r.get("task") == task_id]


def _save(rec):
    with _lock:
        data = store.current().read_json(_path(), None) or {"next": 1, "records": []}
        rec["id"] = data["next"]
        data["next"] += 1
        data["records"] = (data["records"] + [rec])[-KEEP:]
        store.current().write_json(_path(), data)
        return rec


def _update(rec):
    with _lock:
        data = store.current().read_json(_path(), None) or {"next": 1, "records": []}
        data["records"] = [rec if r.get("id") == rec["id"] else r for r in data["records"]]
        store.current().write_json(_path(), data)


# ------------------------------------------------------------------ before / after a step

def before(call):
    """What there is now, for a step that can be undone. Never raises."""
    name, args = call["name"], call.get("args") or {}
    if name not in UNDOABLE:
        return None
    try:
        if name in ("move_or_rename", "copy_path"):
            src, dst = _resolve(args.get("source")), _resolve(args.get("destination"))
            final = _final(src, dst)
            return {"src": str(src), "final": str(final), "clear": not final.exists()}
        if name == "create_folder":
            from nova.actions.tools.files import folder_target
            p = folder_target(args)
            return {"path": str(p), "new": not p.exists()}
        if name == "write_text_file":
            p = _resolve(args.get("path"))
            if not p.exists():
                return {"path": str(p), "new": True}
            if p.is_file() and p.stat().st_size <= BACKUP_MAX:
                keep = _backups() / f"{time.time():.6f}-{p.name}"
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, keep)
                return {"path": str(p), "new": False, "backup": str(keep)}
            return None
        if name == "set_dark_mode":
            from nova import platform
            old = platform.current().dark_mode()
            return None if old is None else {"dark": bool(old)}
        if name == "set_brightness":
            from nova.actions.tools import common
            now = str(common._ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness")
                      or "").strip().splitlines()
            return {"percent": int(now[0])} if now and now[0].strip().isdigit() else None
        if name == "set_volume":
            act = str(args.get("action") or "")
            return {"action": OPPOSITE[act], "steps": args.get("steps", 5)} if act in OPPOSITE else None
        if name == "toggle_wifi":
            from nova.actions import readback
            old = readback.wifi_on()
            return None if old is None else {"on": old}
        if name == "set_wallpaper":
            from nova import platform
            old = platform.current().wallpaper()
            return {"image": old} if old else None          # no wallpaper before (or unreadable): nothing to go back to
    except Exception as e:
        log_exception("undo: before a step", e)
    return None


def after(call, out, ok, snap, task_id=""):
    """The step ran: keep its undo record when it changed something that can be put back. -> the record or None."""
    if not ok or snap is None:
        return None
    name, args = call["name"], call.get("args") or {}
    what = {"move_or_rename": lambda: f"moved {Path(snap['src']).name} to {snap['final']}",
            "copy_path": lambda: f"copied {Path(snap['src']).name} to {snap['final']}",
            "create_folder": lambda: f"made the folder {snap['path']}",
            "write_text_file": lambda: f"{'wrote the new file' if snap.get('new') else 'changed'} {snap['path']}",
            "set_dark_mode": lambda: f"turned dark mode {'on' if args.get('enabled') in (True, 'true', 'on', 1) else 'off'}",
            "set_brightness": lambda: f"set the brightness to {args.get('percent')}%",
            "set_volume": lambda: f"turned the volume {args.get('action')}",
            "toggle_wifi": lambda: f"turned Wi-Fi {'on' if args.get('on') in (True, 'true', 'on', 1) else 'off'}",
            "set_wallpaper": lambda: f"set the wallpaper to {Path(str(args.get('image_path') or '')).name}"}[name]()
    if name in ("move_or_rename", "copy_path") and not (snap["clear"] and Path(snap["final"]).exists()):
        return None                      # something was at the new place before: putting it back could lose it
    if name == "create_folder" and not snap.get("new"):
        return None
    return _save({"tool": name, "what": what, "snap": snap, "task": task_id, "at": time.time(), "state": "can undo"})


# ------------------------------------------------------------------ undo

def _find(which):
    recs = [r for r in records() if r.get("state") == "can undo"]
    w = str(which or "latest").strip().lower().lstrip("#")
    if not recs:
        return None
    if w in ("", "latest", "last", "that", "it"):
        return recs[-1]
    if w.isdigit():
        return next((r for r in recs if r["id"] == int(w)), None)
    return next((r for r in reversed(recs) if w in r["what"].lower()), None)


def _recycle(path):
    from nova.actions import tools
    return tools.run_tool("delete_to_recycle_bin", {"path": path})


def _put_back(rec):
    """-> (done?, words)."""
    from nova.actions import tools
    s, name = rec["snap"], rec["tool"]
    if name == "move_or_rename":
        if Path(s["src"]).exists():
            return False, f"something is at {s['src']} again, so I didn't move it back over it"
        if not Path(s["final"]).exists():
            return False, f"{s['final']} isn't there any more"
        out = tools.run_tool("move_or_rename", {"source": s["final"], "destination": s["src"]})
        return Path(s["src"]).exists(), out
    if name == "copy_path":
        if not Path(s["final"]).exists():
            return True, "the copy was already gone"
        out = _recycle(s["final"])
        return not Path(s["final"]).exists(), "moved the copy to the Recycle Bin" if not Path(s["final"]).exists() else out
    if name == "create_folder":
        p = Path(s["path"])
        if not p.exists():
            return True, "the folder was already gone"
        if any(p.iterdir()):
            return False, f"{p} has things in it now, so I left it"
        _recycle(str(p))
        return True, f"moved {p} to the Recycle Bin"
    if name == "write_text_file":
        p = Path(s["path"])
        if s.get("new"):
            if p.exists():
                _recycle(str(p))
            return True, f"moved the new file {p.name} to the Recycle Bin"
        backup = Path(s["backup"])
        if not backup.exists():
            return False, "the old text wasn't kept any more"
        shutil.copy2(backup, p)
        os.remove(backup)
        return True, f"put the old text of {p.name} back"
    if name == "set_dark_mode":
        out = tools.run_tool("set_dark_mode", {"enabled": s["dark"]})
        return True, out
    if name == "set_brightness":
        return True, tools.run_tool("set_brightness", {"percent": s["percent"]})
    if name == "set_volume":
        return True, tools.run_tool("set_volume", {"action": s["action"], "steps": s["steps"]})
    if name == "toggle_wifi":
        return True, tools.run_tool("toggle_wifi", {"on": s["on"]})
    if name == "set_wallpaper":
        if not Path(s["image"]).is_file():
            return False, f"the old wallpaper ({Path(s['image']).name}) isn't there any more"
        return True, tools.run_tool("set_wallpaper", {"image_path": s["image"]})
    return False, "I don't know how to undo that"


def undo(which="latest"):
    rec = _find(which)
    if rec is None:
        return "There's nothing I can undo" + ("" if str(which or "latest") in ("latest", "") else f" that matches '{which}'") + "."
    try:
        ok, words = _put_back(rec)
    except Exception as e:
        log_exception("undo: putting a change back", e)
        ok, words = False, f"it went wrong ({e.__class__.__name__})"
    rec.update(state="undone" if ok else "can't undo", why=str(words)[:200], undone_at=time.time())
    _update(rec)
    return (f"Undone: {rec['what']} - {words}." if ok else f"I couldn't undo '{rec['what']}': {words}.").replace("..", ".")


def describe(rec):
    return {"id": rec["id"], "what": rec["what"], "state": rec["state"], "at": rec["at"], "task": rec.get("task", "")}

