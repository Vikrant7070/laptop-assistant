"""Who the assistant is. Each character lives in her own folder, characters/<id>/ at the top of the project:

  character.json   name, menu label, voice, wake word spellings, look colours, 3D model fit
  persona.md       how she thinks and talks (# Who you are / # How you talk / # Touch)
  model.vrm        her 3D model (VRoid Studio export), optional

Add or edit a folder to change a character; nothing in the code needs to change (see characters/README.md).
Each character answers to her own name: "Hey Lucy" for Lucy, "Hey Makima" for Makima.
Makima and Lucy are fan characters for this laptop only - their 3D models must never be shared.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "characters"
DEFAULT = "nova"
SECTIONS = {"who you are": "intro", "how you talk": "talk", "touch": "touch"}


def _persona(text):
    """persona.md -> {"intro": ..., "talk": ..., "touch": ...} (the text under each heading)."""
    parts, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^#\s+(.+?)\s*$", line)
        if m:
            cur = SECTIONS.get(m.group(1).lower())
            if cur:
                parts[cur] = []
            continue
        if cur:
            parts[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in parts.items()}


def load(root=ROOT):
    """Every character folder with a usable character.json (a name and a voice) -> {id: character}."""
    out = {}
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            ch = json.loads((d / "character.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not (isinstance(ch, dict) and ch.get("name") and ch.get("voice")):
            continue
        name = ch["name"]
        persona = d / "persona.md"
        ch["persona"] = _persona(persona.read_text(encoding="utf-8")) if persona.exists() else {}
        ch.setdefault("label", name)
        wake = ch.setdefault("wake", {})
        wake["names"] = [str(n).lower() for n in (wake.get("names") or [])] or [name.lower()]
        if name.lower() not in wake["names"]:
            wake["names"].insert(0, name.lower())
        wake.setdefault("hint", f"Hey {name}. {name}, what's the time? Hey {name}, open Chrome.")
        ch.setdefault("look_preset", d.name)
        ch.setdefault("look", {})
        model = ch.setdefault("model", {})
        model.setdefault("file", "model.vrm")
        model.setdefault("fit", {})
        ch["folder"] = d
        out[d.name] = ch
    return out


CHARACTERS = load()


def get(cid):
    """The character with this id (the default one for an unknown id), with its id filled in."""
    cid = cid if cid in CHARACTERS else DEFAULT if DEFAULT in CHARACTERS else next(iter(CHARACTERS), None)
    if cid is None:
        raise RuntimeError(f"No characters found in {ROOT}")
    return dict(CHARACTERS[cid], id=cid)


def choices():
    return [dict(id=k, label=v["label"], name=v["name"]) for k, v in CHARACTERS.items()]


def model_path(ch):
    """Her VRM file, or None when the folder has none (she uses the toon body)."""
    p = ch["folder"] / ch["model"]["file"]
    return p if p.is_file() else None


def wake_names(ch):
    """A regex group matching any spelling of her name (longest first, so "novah" wins over "nova")."""
    names = sorted(set(ch["wake"]["names"]), key=len, reverse=True)
    return "(?:" + "|".join(re.escape(n).replace(r"\ ", " ") for n in names) + ")"


def all_names():
    """Every character's name spellings: words to strip from a request ("hey lucy, open chrome" -> "open chrome")."""
    return sorted({n for ch in CHARACTERS.values() for n in ch["wake"]["names"]}, key=len, reverse=True)


def look_presets():
    """{preset name: colours} for change_look, from each character's look."""
    return {ch["look_preset"]: dict(ch["look"]) for ch in CHARACTERS.values() if ch["look_preset"] != "default"}
