"""Who the assistant is: each character has a personality, a voice and a 3D look. Picked in Settings -> Nova.

The wake word stays "Hey Nova" for every character (the ears are tuned to that name); each one introduces herself
by her own name. Makima and Lucy are fan characters for this laptop only - their 3D models must never be shared.
"""

CHARACTERS = {
    "nova": dict(
        name="Nova", label="✨ Nova - cheerful desi dost (the original)",
        persona="desi", voice="hf_alpha", look="default", vrm="nova.vrm"),
    "makima": dict(
        name="Makima", label="🖤 Makima - calm, composed, quietly in charge",
        persona="composed", voice="af_nicole", look="office", vrm="makima.vrm"),
    "lucy": dict(
        name="Lucy", label="🗝 Lucy - bright, kind, a little dramatic",
        persona="lucy", voice="af_bella", look="celestial", vrm="lucy.vrm"),
}
DEFAULT = "nova"


def get(cid):
    """The character with this id (the default one for an unknown id), with its id filled in."""
    cid = cid if cid in CHARACTERS else DEFAULT
    return dict(CHARACTERS[cid], id=cid)


def choices():
    return [dict(id=k, label=v["label"], name=v["name"]) for k, v in CHARACTERS.items()]
