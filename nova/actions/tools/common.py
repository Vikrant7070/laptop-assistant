"""Helpers every tool module uses: JSON files, PowerShell, sizes, paths, and the schema shorthands."""


import json
import os
import subprocess
import threading
from pathlib import Path

from nova import store


"""Laptop-control tools for the assistant (Windows).

Each tool is a plain function returning a string. TOOLS holds the JSON schemas
sent to Claude; RISKY names the tools that need the user's click-to-approve
before they run.
"""


# Schema shorthands for the @tool declarations below.
# How a brain writes a new_move: joint angles in degrees from her own point of view (nova/avatar/body.py).
ANGLE_HELP = ('"keys": a list of poses over time, each {"at": 0..1 (when, as a fraction of the duration), and any of: '
              'right_arm_out/left_arm_out (0 arm down at her side, 90 straight out sideways, 135 raised high - the most; '
              'her big head hides arms raised higher), '
              'right_arm_forward/left_arm_forward (0 down, 90 pointing ahead, 160 up past her head), '
              'head_nod (+ looks down, - up, max 40), head_turn (+ to her left, max 70), head_tilt (+ to her left, '
              'max 35), lean_forward (+ bows, max 30), lean_side (max 25), turn (whole body, 360 = full spin), '
              'jump (0..100), right_leg_lift/left_leg_lift (0..90 kick forward), '
              'right_elbow/left_elbow (0 straight..150 fully bent; arm_forward + elbow = 90 points the forearm ahead), '
              'right_wrist/left_wrist (+ bends the hand to the palm, - opens it, max 75), right_wrist_side/left_wrist_side '
              '(a little wave from the wrist, max 50), right_elbow_side/left_elbow_side (forearm curls up/out +, in/behind -), '
              'right_hand/left_hand (relaxed, open, fist, point, hold, soft), hands_behind / right_hand_on_hip / '
              'left_hand_on_hip (0..1: clasp hands behind her back, hand on hip), '
              'eyes (open, closed, happy, half, up, wide, sad, angry, side_left, side_right), mouth (smile, open, grin, '
              'frown, o), blush (0..1), symbol (none, question, exclaim, note, heart, z, drop)}. '
              'Unset joints rest at 0; she starts and ends standing. Her right = the viewer\'s left. '
              'Example shrug: [{"at":0.3,"right_arm_out":45,"left_arm_out":45,"right_arm_forward":30,'
              '"left_arm_forward":30,"head_tilt":15,"eyes":"up","mouth":"o"},{"at":0.7,"right_arm_out":45,'
              '"left_arm_out":45,"right_arm_forward":30,"left_arm_forward":30,"head_tilt":15}]')


S = {"type": "string"}


N = {"type": "number"}


B = {"type": "boolean"}


try:  # real pixel coordinates, so clicks land where UI Automation says
    import ctypes as _c
    _c.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass  # intentional: UI Automation/cleanup is best effort; the tool reports its own result


HOME = Path.home()


store.current().root.mkdir(parents=True, exist_ok=True)


def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


_files_lock = threading.Lock()   # notes / reminders are read-modify-written from several threads


def _save(path, obj):
    """Write JSON atomically (a reader never sees a half-written file)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _ps(cmd, timeout=30):
    """Run a PowerShell command and return its output."""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = (r.stdout or "") + (("\nERR: " + r.stderr) if r.stderr.strip() else "")
    return out.strip()[:6000] or "(done, no output)"


def _fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _resolve(path):
    p = Path(os.path.expandvars(os.path.expanduser(path or "~")))
    shortcuts = {"desktop": HOME / "OneDrive" / "Desktop", "downloads": HOME / "Downloads",
                 "documents": HOME / "Documents", "pictures": HOME / "Pictures",
                 "music": HOME / "Music", "videos": HOME / "Videos", "home": HOME}
    key = str(path).strip().lower()
    if key in shortcuts:
        p = shortcuts[key]
        if key == "desktop" and not p.exists():
            p = HOME / "Desktop"
    return p
