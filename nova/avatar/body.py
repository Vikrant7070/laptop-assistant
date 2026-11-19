"""3D Nova's body when she is rendered live: the moves she has invented and how she looks.

Live rendering: static/nova3d.html?live=1 runs in a hidden browser window (desktop.py), poses her 3D model each
frame and sends the pixels to her desktop window (avatar.py, POST /frame). The saved sprite frames are only the
fallback for when that window isn't running. Because the model is posed live, she can:
  - play moves nobody animated before: the brain writes keyframes (new_move tool), checked and stored here in
    data/moves.json so she can do them again by name;
  - change her look: colours and accessories (change_look tool), stored in data/look.json.

Angles are in degrees from her own point of view, so a brain can write them without knowing the model:
  right_arm_out / left_arm_out          0 = hanging at her side, 90 = straight out sideways, 135 = raised high
                                        (her head is big: higher than 140 hides the arm behind it)
  right_arm_forward / left_arm_forward  0 = hanging down, 90 = pointing straight ahead, 160 = up past her head
  head_nod (+ looks down, - up), head_turn (+ to her left = the viewer's right), head_tilt (+ to her left shoulder)
  lean_forward (+ bows), lean_side (+ to her left), turn (whole body; + to her left), jump (0..100 = how high)
  right_leg_lift / left_leg_lift        0 = standing, 60 = knee-high kick forward
  eyes, mouth, blush (0..1), symbol     her face and the little sign beside her head
"""
import json
import re

from nova.shared import DATA

ROAM_MODES = ("corner", "edge", "free")
ROAM_DEFAULT = "corner"
ROAM_RETURN_AFTER = 8.0      # seconds after her last errand (pointing, carrying a window) before she goes back "home"


def roam_mode(settings=None):
    """Where she hangs about while idle (setting "avatar_roam"): corner = stays in the bottom-right corner, only
    leaving it to point at a button or carry a window, then walking back; edge = strolls only along the bottom
    right of the screen; free = anywhere on the bottom edge or the screen (the old behaviour)."""
    if settings is None:
        try:
            settings = json.loads((DATA / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
    mode = settings.get("avatar_roam")
    return mode if mode in ROAM_MODES else ROAM_DEFAULT


def roam_spot(mode, area, sprite_w, sprite_h, rnd):
    """Where her feet go for an idle stroll. area = (left, top, right, bottom) of the usable screen, rnd = random.uniform."""
    left, top, right, bottom = area
    home = (right - sprite_w * .42, bottom)                 # the nap corner: hair and "z" stay on screen
    if mode == "corner":
        return home
    if mode == "edge":                                      # the right fifth of the bottom edge: never over the work
        return rnd(max(left + 150, right - max(sprite_w * 1.6, (right - left) * .2)), home[0]), bottom
    if rnd(0, 1) < .7:                                      # free: along the bottom ...
        return rnd(left + 150, right - 150), bottom
    return rnd(left + 150, right - 150), rnd(top + sprite_h, bottom)      # ... or anywhere


MOVES_FILE = DATA / "moves.json"
LOOK_FILE = DATA / "look.json"

ANGLES = {   # name: (lowest, highest)
    "right_arm_out": (-20, 140), "left_arm_out": (-20, 140),
    "right_arm_forward": (-60, 180), "left_arm_forward": (-60, 180),
    "head_nod": (-40, 40), "head_turn": (-70, 70), "head_tilt": (-35, 35),
    "lean_forward": (-20, 30), "lean_side": (-25, 25), "turn": (-360, 360), "jump": (0, 100),
    "right_leg_lift": (-30, 90), "left_leg_lift": (-30, 90),
}
EYES = ("open", "closed", "happy", "half", "up", "wide", "sad", "side_left", "side_right")
MOUTHS = ("smile", "open", "grin", "frown", "o")
SYMBOLS = ("none", "question", "exclaim", "note", "heart", "z", "drop")
MAX_KEYS = 24
BUILT_IN = ("idle", "walk", "talk", "think", "point", "happy", "wave", "sleepy", "laugh", "dance", "spin", "clap",
            "surprised", "sad", "confused", "nod", "shake", "facepalm", "bow", "love", "carry", "yawn", "look", "wakeup", "nap", "liedown")

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
    # Character looks (characters.py): Makima-style office look and Lucy-style celestial look.
    "office": {"hair": "#d9776a", "hair_tips": "#b5574d", "eyes": "#e8b730", "hoodie": "#f4f4f2", "pants": "#22242b",
               "shoes": "#151515", "glow": "#e8b730", "accent": "#1a1a1a", "headphones": False, "hair_clip": False,
               "ahoge": False},
    "celestial": {"hair": "#f7d774", "hair_tips": "#fff3b0", "eyes": "#8a5a2b", "hoodie": "#f8fafc", "pants": "#2563eb",
                  "shoes": "#3b2a1a", "glow": "#93c5fd", "accent": "#f5c542", "hat": "#2563eb", "bow": True,
                  "headphones": False, "ahoge": False},
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


# ------------------------------------------------------------------ talking with her whole body
# While she speaks, voice.Mouth sends each sentence's words and loudness to avatar.py. The loudness moves her mouth
# and gives her a little bounce on stressed words (live renderer); the words pick a gesture for that sentence.
# Gestures are short moves in the new_move format; some sentences use a built-in reaction instead (nod, shrug...).
GESTURES = {
    "explain": [{"at": .3, "right_arm_forward": 45, "right_arm_out": 25, "left_arm_forward": 40, "left_arm_out": 25},
                {"at": .7, "right_arm_forward": 35, "right_arm_out": 35, "left_arm_forward": 50, "left_arm_out": 20}],
    "explain_one": [{"at": .25, "right_arm_forward": 55, "right_arm_out": 30, "head_tilt": 5},
                    {"at": .5, "right_arm_forward": 45, "right_arm_out": 40},
                    {"at": .75, "right_arm_forward": 55, "right_arm_out": 30, "head_tilt": 5}],
    "point_you": [{"at": .3, "right_arm_forward": 85, "right_arm_out": 10, "head_nod": 6, "eyes": "happy"},
                  {"at": .7, "right_arm_forward": 80, "right_arm_out": 12, "eyes": "open"}],
    "point_me": [{"at": .3, "right_arm_forward": 60, "right_arm_out": -15, "head_tilt": 6},
                 {"at": .7, "right_arm_forward": 60, "right_arm_out": -15}],
    "ask": [{"at": .3, "right_arm_forward": 35, "right_arm_out": 30, "left_arm_forward": 35, "left_arm_out": 30,
             "head_tilt": 16, "eyes": "up"},
            {"at": .75, "right_arm_forward": 35, "right_arm_out": 30, "left_arm_forward": 35, "left_arm_out": 30,
             "head_tilt": 16, "eyes": "open"}],
    "excited": [{"at": .25, "right_arm_out": 115, "left_arm_out": 115, "right_arm_forward": 25, "left_arm_forward": 25,
                 "jump": 18, "eyes": "happy", "blush": 1},
                {"at": .55, "right_arm_out": 100, "left_arm_out": 100, "jump": 0, "eyes": "happy", "blush": 1},
                {"at": .8, "right_arm_out": 115, "left_arm_out": 115, "jump": 14, "eyes": "happy"}],
    "think": [{"at": .3, "right_arm_forward": 120, "right_arm_out": -12, "head_tilt": 10, "eyes": "up"},
              {"at": .75, "right_arm_forward": 120, "right_arm_out": -12, "head_tilt": 10, "eyes": "up"}],
    "count": [{"at": .2, "right_arm_forward": 70, "right_arm_out": 20}, {"at": .4, "right_arm_forward": 60},
              {"at": .6, "right_arm_forward": 70, "right_arm_out": 20}, {"at": .8, "right_arm_forward": 60}],
}
# (pattern, gesture or built-in reaction) - the first that matches the sentence wins
SPEECH_RULES = [
    (r"\b(ha ?ha|hehe|haha|lol|lmao|hilarious|funny)\b|😂|🤣", "laugh"),
    (r"\b(sorry|oops|my bad|my mistake|galti)\b", "facepalm"),
    (r"\b(thank|thanks|shukriya|dhanyavaad)\b", "bow"),
    (r"\b(love|adore)\b|❤|💖", "love"),
    (r"\b(wow|whoa|woah|omg|no way)\b|\breally\?", "surprised"),
    (r"\b(i don'?t know|not sure|no idea|maybe|perhaps|who knows|pata nahi)\b", "confused"),
    (r"\b(let me think|hmm+|i wonder)\b", "think"),
    (r"^\W*(no|nope|nah|nahi|can'?t|cannot|won'?t)\b", "shake"),
    (r"^\W*(yes|yeah|yep|sure|of course|okay|ok|done|right|exactly|haan|bilkul|absolutely)\b", "nod"),
    (r"\b(awesome|amazing|great|yay|woohoo|fantastic|shabash|wah|brilliant|perfect|excellent)\b.*!", "excited"),
    (r"\?\s*$", "ask"),
    (r"\b(first|second|third|then|next|finally|step)\b", "count"),
    (r"\b(you|your|you'?re|aap|tum)\b", "point_you"),
    (r"^\W*(i|i'?m|i'?ll|i'?ve|my|me)\b", "point_me"),
]
SPEECH_REACTIONS = ("laugh", "facepalm", "bow", "love", "surprised", "confused", "shake", "nod")


def speech_gesture(words, secs, n=0):
    """What her body does while she says this sentence: a gesture move, a built-in reaction name, or None.
    n = how many sentences she has said in this reply (plain sentences alternate explaining hands and stillness)."""
    text = " ".join(str(words or "").lower().split())
    if not text or secs < 0.5:
        return None
    for pattern, g in SPEECH_RULES:
        if re.search(pattern, text):
            return g if g in SPEECH_REACTIONS else gesture_move(g, secs)
    if secs >= 1.2 and n % 3 != 2:         # most plain sentences get talking hands, every third one stays calm
        return gesture_move("explain" if n % 2 == 0 else "explain_one", secs)
    return None


def gesture_move(name, secs):
    """A speech gesture as a playable move, stretched to fit the sentence."""
    return {"name": "gesture_" + name, "duration": round(max(1.0, min(3.2, secs * 0.9)), 2), "loop": False,
            "keys": GESTURES[name]}


# ------------------------------------------------------------------ sounds for her invented moves
# A move may carry a sound: a short word in her own voice ({"say": "Yay!"}) or a little synthesized piece built from
# notes ({"notes": [{"type": "chime", "pitch": "C6", "at": 0}, ...]}). Made once, saved in data/sfx/move_<name>.wav.
SOUND_TYPES = ("chime", "pluck", "tone", "voice", "whoosh", "pop", "clap", "boing", "drum")
NOTE_RE = re.compile(r"^([a-g])(#|b)?(\d)$")


def pitch_hz(p):
    """'C5', 'F#4', 'Bb3' or a number of Hz -> Hz (None if it isn't one)."""
    if isinstance(p, bool):
        return None
    if isinstance(p, (int, float)):
        return float(p) if 40 <= p <= 4000 else None
    m = NOTE_RE.match(str(p or "").strip().lower())
    if not m:
        try:
            return pitch_hz(float(p))
        except (TypeError, ValueError):
            return None
    semis = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}[m.group(1)] + {"#": 1, "b": -1}.get(m.group(2) or "", 0)
    return round(440 * 2 ** ((semis - 9) / 12 + int(m.group(3)) - 4), 2)


def clean_sound(sound):
    """A sound the brain wrote -> (sound, None), (None, None) when there is none, or (None, what is wrong)."""
    if not sound:
        return None, None
    if isinstance(sound, str):
        try:
            sound = json.loads(sound)
        except ValueError:
            sound = {"say": sound}
    if not isinstance(sound, dict):
        return None, 'sound must be {"say": "Yay!"} or {"notes": [...]}.'
    if sound.get("say"):
        say = " ".join(str(sound["say"]).split())[:40]
        if not re.search(r"[A-Za-z]", say):
            return None, "say needs a word or sound like 'Yay!' or 'Ehehe!'."
        return {"say": say}, None
    notes = sound.get("notes")
    if not isinstance(notes, list) or not notes:
        return None, 'sound must be {"say": "Yay!"} or {"notes": [{"type": "chime", "pitch": "C6", "at": 0}]}.'
    out = []
    for i, n in enumerate(notes[:24]):
        if not isinstance(n, dict) or n.get("type") not in SOUND_TYPES:
            return None, f"note {i + 1}: type must be one of {', '.join(SOUND_TYPES)}."
        vol = _num(n.get("volume", 1), 0, 1)
        note = {"type": n["type"], "at": _num(n.get("at", 0), 0, 4) or 0,
                "length": _num(n.get("length", 0.3), 0.03, 2) or 0.3, "volume": 1.0 if vol is None else vol}
        if note["type"] in ("chime", "pluck", "tone", "voice", "boing"):
            hz = pitch_hz(n.get("pitch", "C5"))
            if hz is None:
                return None, f"note {i + 1}: pitch must be a note like C5 or F#4, or Hz (40-4000)."
            note["pitch"] = hz
            to = pitch_hz(n["to"]) if n.get("to") is not None else None
            if to:
                note["to"] = to
        if note["type"] == "voice":
            note["vowel"] = n.get("vowel") if n.get("vowel") in ("a", "e", "o", "u", "m") else "a"
        out.append(note)
    return {"notes": out}, None
