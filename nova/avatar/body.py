"""3D Nova's body when she is rendered live: the moves she has invented and how she looks.

Live rendering: static/nova3d.html?live=1 runs in a hidden browser window (desktop.py), poses her 3D model each
frame and sends the pixels to her desktop window (avatar.py, POST /frame). The saved sprite frames are only the
fallback for when that window isn't running. Because the model is posed live, she can:
  - play moves nobody animated before: the brain writes keyframes (new_move tool), checked and stored here in
    data/moves.json so she can do them again by name;
  - change her look: colours and accessories (change_look tool), stored in data/look.json.

Angles are in degrees from her own point of view, so a brain can write them without knowing the model:
  right_arm_out / left_arm_out          0 = hanging at her side, 90 = straight out sideways, 170 = straight up
  right_arm_forward / left_arm_forward  0 = hanging down, 90 = pointing straight ahead, 160 = up past her head
  head_nod (+ looks down, - up), head_turn (+ to her left = the viewer's right), head_tilt (+ to her left shoulder)
  lean_forward (+ bows), lean_side (+ to her left), turn (whole body; + to her left), jump (0..100 = how high)
  right_leg_lift / left_leg_lift        0 = standing, 60 = knee-high kick forward
  eyes, mouth, blush (0..1), symbol     her face and the little sign beside her head
"""
import json
import re

from nova.shared import DATA

MOVES_FILE = DATA / "moves.json"
LOOK_FILE = DATA / "look.json"

ANGLES = {   # name: (lowest, highest)
    "right_arm_out": (-20, 180), "left_arm_out": (-20, 180),
    "right_arm_forward": (-60, 180), "left_arm_forward": (-60, 180),
    "head_nod": (-40, 40), "head_turn": (-70, 70), "head_tilt": (-35, 35),
    "lean_forward": (-20, 45), "lean_side": (-25, 25), "turn": (-360, 360), "jump": (0, 100),
    "right_leg_lift": (-30, 90), "left_leg_lift": (-30, 90),
}
EYES = ("open", "closed", "happy", "half", "up", "wide", "sad", "side_left", "side_right")
MOUTHS = ("smile", "open", "grin", "frown", "o")
SYMBOLS = ("none", "question", "exclaim", "note", "heart", "z", "drop")
MAX_KEYS = 24
BUILT_IN = ("idle", "walk", "talk", "think", "point", "happy", "wave", "sleepy", "laugh", "dance", "spin", "clap",
            "surprised", "sad", "confused", "nod", "shake", "facepalm", "bow", "love", "carry", "yawn", "look")

# How she looks. Colours are "#rrggbb" or a colour word the browser knows ("pink", "teal", "gold"...).
DEFAULT_LOOK = {
    "hair": "#6b4fe0", "hair_tips": "#45d4ef", "eyes": "#7c5cff", "skin": "#ffe2d4", "hoodie": "#2c3266",
    "pants": "#252a4d", "shoes": "#f1f2ff", "glow": "#5fe3f5", "accent": "#ffd25e", "hat": "#ff6b9d",
    "headphones": True, "hair_clip": True, "long_hair": True, "ahoge": True,
    "glasses": False, "cat_ears": False, "beanie": False, "bow": False,
}
COLOURS = [k for k, v in DEFAULT_LOOK.items() if isinstance(v, str)]
SWITCHES = [k for k, v in DEFAULT_LOOK.items() if isinstance(v, bool)]
PRESETS = {
    "default": {},
    "sakura": {"hair": "#ff8fc8", "hair_tips": "#fff0f7", "eyes": "#e0457b", "hoodie": "#ffffff", "pants": "#f3c6dc",
               "glow": "#ff8fc8", "accent": "#ff6b9d", "hat": "#ff6b9d", "bow": True, "headphones": False},
    "midnight": {"hair": "#1b1b2f", "hair_tips": "#e43f5a", "eyes": "#e43f5a", "hoodie": "#16161f", "pants": "#101018",
                 "shoes": "#2a2a3a", "glow": "#e43f5a", "accent": "#e43f5a", "glasses": True},
    "forest": {"hair": "#3e7a3e", "hair_tips": "#d9f99d", "eyes": "#2f855a", "hoodie": "#7c5a3a", "pants": "#3b2f25",
               "glow": "#a3e635", "accent": "#facc15", "hat": "#4d7c0f", "beanie": True},
    "sunny": {"hair": "#f59e0b", "hair_tips": "#fde68a", "eyes": "#d97706", "hoodie": "#38bdf8", "pants": "#1e3a8a",
              "glow": "#fde047", "accent": "#fb923c", "cat_ears": True},
}
COLOUR_RE = re.compile(r"^(#[0-9a-f]{3}|#[0-9a-f]{6}|[a-z]{3,20})$")


def _num(v, lo, hi):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, v)) if v == v else None     # v == v: not NaN


def clean_move(name, duration, keys, loop=False):
    """A move the brain wrote -> (move, None) ready to play and save, or (None, what is wrong)."""
    name = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")[:30]
    if not name:
        return None, "Give the move a short name, like 'shrug' or 'robot_dance'."
    if name in BUILT_IN:
        return None, f"'{name}' is already one of my built-in reactions - just use react."
    dur = _num(duration, 0.3, 12)
    if dur is None:
        return None, "duration must be a number of seconds (0.3 to 12)."
    if isinstance(keys, str):
        try:
            keys = json.loads(keys)
        except ValueError:
            return None, "keys must be a list of poses."
    if not isinstance(keys, list) or not keys:
        return None, "keys must be a list of at least one pose, e.g. [{\"at\": 0, \"right_arm_out\": 90}]."
    out = []
    for i, k in enumerate(keys[:MAX_KEYS]):
        if not isinstance(k, dict):
            return None, f"pose {i + 1} must be an object like {{\"at\": 0.5, \"head_nod\": 20}}."
        at = _num(k.get("at", i / max(1, len(keys) - 1)), 0, 1)
        pose = {"at": at if at is not None else 0}
        for joint, (lo, hi) in ANGLES.items():
            if joint in k:
                v = _num(k[joint], lo, hi)
                if v is not None:
                    pose[joint] = round(v, 1)
        for field, allowed in (("eyes", EYES), ("mouth", MOUTHS), ("symbol", SYMBOLS)):
            if field in k:
                v = str(k[field]).lower().strip()
                if v not in allowed:
                    return None, f"{field} must be one of: {', '.join(allowed)}."
                pose[field] = v
        if "blush" in k:
            v = _num(k["blush"], 0, 1)
            if v is not None:
                pose["blush"] = v
        if len(pose) == 1:
            known = ", ".join(list(ANGLES) + ["eyes", "mouth", "blush", "symbol"])
            return None, f"pose {i + 1} sets nothing I know. Use: {known}."
        out.append(pose)
    out.sort(key=lambda p: p["at"])
    return {"name": name, "duration": round(dur, 2), "loop": bool(loop), "keys": out}, None


def load_moves():
    try:
        moves = json.loads(MOVES_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in moves.items() if isinstance(v, dict) and v.get("keys")}
    except (OSError, ValueError):
        return {}


def save_move(move):
    moves = load_moves()
    moves[move["name"]] = move
    MOVES_FILE.parent.mkdir(parents=True, exist_ok=True)
    MOVES_FILE.write_text(json.dumps(moves, indent=1), encoding="utf-8")


def forget_move(name):
    moves = load_moves()
    if moves.pop(name, None) is None:
        return False
    MOVES_FILE.write_text(json.dumps(moves, indent=1), encoding="utf-8")
    return True


def load_look():
    look = dict(DEFAULT_LOOK)
    try:
        saved = json.loads(LOOK_FILE.read_text(encoding="utf-8"))
        look.update({k: v for k, v in saved.items() if k in DEFAULT_LOOK and type(v) is type(DEFAULT_LOOK[k])})
    except (OSError, ValueError):
        pass
    return look


def change_look(changes, preset=None):
    """Apply a preset and/or changes to her saved look -> (look, list of problems). Saved to data/look.json."""
    look = load_look()
    problems = []
    if preset:
        p = str(preset).lower().strip()
        if p not in PRESETS:
            problems.append(f"No outfit called '{preset}'. Outfits: {', '.join(PRESETS)}.")
        else:
            look = {**DEFAULT_LOOK, **PRESETS[p]}
    for k, v in (changes or {}).items():
        if k in COLOURS:
            c = str(v).lower().replace(" ", "").strip()
            if COLOUR_RE.match(c):
                look[k] = c
            else:
                problems.append(f"'{v}' isn't a colour I know for {k} (use a word like pink or a code like #ff66aa).")
        elif k in SWITCHES:
            look[k] = v if isinstance(v, bool) else str(v).lower() in ("true", "yes", "on", "1")
        elif v not in (None, ""):
            problems.append(f"I don't have '{k}'. I can change: {', '.join(COLOURS + SWITCHES)}.")
    LOOK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOOK_FILE.write_text(json.dumps(look, indent=1), encoding="utf-8")
    return look, problems
