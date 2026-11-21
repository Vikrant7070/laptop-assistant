"""Nova's 3D body: reactions, invented moves, looks; dragging windows."""


import re

from nova.actions.registry import tool
from nova.shared import REACTIONS
from nova.actions.tools import common


def reaction_name(reaction):
    """The brain's word for a reaction -> one of REACTIONS or a move she invented (body.py), else the cleaned word."""
    from nova.avatar import body
    name = str(reaction or "").lower().strip()
    if re.sub(r"[^a-z0-9]+", "_", name).strip("_") in body.load_moves():     # her own "shrug" beats the alias
        return re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    return {"laughing": "laugh", "dancing": "dance", "twirl": "spin", "applaud": "clap", "cheer": "happy", "jump": "happy",
            "cry": "sad", "shrug": "confused", "yes": "nod", "no": "shake", "oops": "facepalm", "thanks": "bow",
            "heart": "love", "stretch": "yawn", "hello": "wave", "hi": "wave"}.get(name, name)


def can_do():
    """Everything react can play: the built-in reactions and the moves she invented."""
    from nova.avatar import body
    moves = sorted(body.load_moves())
    return ", ".join(REACTIONS) + (f"; and my own moves: {', '.join(moves)}" if moves else "")


@tool("Act something out with your 3D body on screen: laugh, dance, spin, clap, surprised, sad, confused, nod, "
      "shake (no), facepalm (oops), bow (thanks), love, yawn, look, happy (jump), wave - or one of your own "
      "moves you made with new_move, by its name. Use it when the user asks you to dance, laugh, clap and so "
      "on. It plays right after you finish speaking your answer (a laugh after the joke), so call it first, "
      "then answer. For something none of these is, use new_move.", friendly="Acted it out", core=True,
      hints="dance laugh clap wave", props={"reaction": common.S, "times": common.N}, required=["reaction"])
def react(reaction, times=0):
    """Make 3D Nova act something out on screen right now. (In a chat, app.queue_reaction plays it after she speaks.)"""
    from nova.avatar import avatar_client, body
    name = reaction_name(reaction)
    if name not in REACTIONS and name not in body.load_moves():
        return (f"I can't do '{reaction}' yet. I can: {can_do()}. To do something new, invent it with new_move.")
    if not avatar_client.running():
        return "My 3D body isn't on screen right now, so I can't act it out."
    out = avatar_client.send("react", name=name, repeat=int(times) if times else None, wait=True, timeout=20)
    return f"Did it: {name}." if out and out.get("played") else f"I couldn't do '{name}' just now."


@tool("Invent a NEW body move for your 3D self and do it now (e.g. 'do a robot dance', 'shrug', "
      "'stretch', 'salute', 'do jumping jacks'), when no react reaction fits. You write the poses; it is saved "
      "under its name so react can do it again. " + common.ANGLE_HELP, friendly="Made up a new move",
      hints="do a new move, act out, shrug, salute, stretch, robot dance, jumping jacks, pose, show me how",
      props={"name": common.S, "duration": {"type": "number", "description": "seconds, 0.3 to 12"},
      "keys": {"type": "array", "items": {"type": "object"}}, "times": common.N,
      "sound": {"type": "object", "description":
      "optional sound played with the move: {\"say\": \"Yay!\"} (a short word in your own voice) or "
      "{\"notes\": [{\"type\": chime|pluck|tone|voice|boing|whoosh|pop|clap|drum, \"pitch\": "
      "\"C5\" (note or Hz), \"to\": pitch to slide to, \"at\": seconds, \"length\": seconds, "
      "\"volume\": 0..1}]}"}}, required=["name", "duration", "keys"])
def new_move(name, duration, keys, times=1, save=True, sound=None):
    """Invent a move for 3D Nova's live body, play it, and remember it (data/moves.json) to do again with react.
    (In a chat, app.queue_new_move runs instead: it plays the move after she has spoken, and can use her voice.)"""
    from nova.audio import sfx
    from nova.avatar import avatar_client, body
    move, err = body.clean_move(name, duration, keys)
    if err:
        return f"Error in new_move: {err}"
    snd, err = body.clean_sound(sound)
    if err:
        return f"Error in new_move: sound: {err}"
    if snd and snd.get("notes"):
        sfx.save_move_sound(move["name"], snd)
        move["sound"] = snd
    if not avatar_client.running():
        return "My 3D body isn't on screen right now, so I can't act it out."
    out = avatar_client.send("move", move=move, repeat=int(times or 1), wait=True, timeout=30) or {}
    if out.get("error"):
        return f"Error in new_move: {out['error']}"
    if not out.get("played"):
        return ("I can't do new moves right now: my live 3D body is off (tray menu: Live 3D Nova), so only my "
                "built-in reactions work.")
    if save:
        body.save_move(move)
    return (f"Did my new move '{move['name']}' ({len(move['keys'])} poses, {move['duration']} s)"
            + (" and saved it: say react with that name to do it again." if save else "."))


@tool("Delete one of your own invented moves.", friendly="Forgot a move", hints="forget that move, delete your move",
      props={"name": common.S}, required=["name"])
def forget_move(name):
    from nova.avatar import avatar_client, body
    key = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    if not body.forget_move(key):
        return f"I have no move called '{name}'. My moves: {', '.join(sorted(body.load_moves())) or 'none yet'}."
    avatar_client.send("forget_move", name=key, timeout=3)
    return f"Forgot my move '{key}'."


@tool("Change how your 3D self looks, and keep it: an outfit (default, sakura, midnight, forest, "
      "sunny) and/or colours (a colour word like pink/teal/gold or #rrggbb) of hair, hair_tips, eyes, skin, "
      "hoodie, pants, shoes, glow, accent (star), hat (beanie and bow colour), and things to wear on/off: "
      "headphones, hair_clip, long_hair, ahoge (the hair curl), glasses, cat_ears, beanie, bow. "
      "preset 'default' puts everything back.", friendly="Changed my look",
      hints="change your look, outfit, hair colour, clothes, wear glasses, cat ears, hat, dress up, new style",
      props={"preset": {"type": "string", "enum": ["default", "sakura", "midnight", "forest", "sunny"]},
      "hair": common.S, "hair_tips": common.S, "eyes": common.S, "skin": common.S, "hoodie": common.S, "pants": common.S, "shoes": common.S, "glow": common.S,
      "accent": common.S, "hat": common.S, "headphones": common.B, "hair_clip": common.B, "long_hair": common.B, "ahoge": common.B, "glasses": common.B,
      "cat_ears": common.B, "beanie": common.B, "bow": common.B}, required=[])
def change_look(preset="", hair="", hair_tips="", eyes="", skin="", hoodie="", pants="", shoes="", glow="", accent="",
                hat="", headphones=None, hair_clip=None, long_hair=None, ahoge=None, glasses=None, cat_ears=None,
                beanie=None, bow=None):
    """Change how 3D Nova looks (live 3D): an outfit preset and/or colours and things she wears. Saved for next time."""
    from nova.avatar import avatar_client, body
    args = dict(hair=hair, hair_tips=hair_tips, eyes=eyes, skin=skin, hoodie=hoodie, pants=pants, shoes=shoes,
                glow=glow, accent=accent, hat=hat, headphones=headphones, hair_clip=hair_clip, long_hair=long_hair,
                ahoge=ahoge, glasses=glasses, cat_ears=cat_ears, beanie=beanie, bow=bow)
    changes = {k: v for k, v in args.items() if v not in (None, "")}
    if not changes and not preset:
        return f"Tell me what to change. Outfits: {', '.join(body.PRESETS)}; or colours/things: " \
               f"{', '.join(body.COLOURS + body.SWITCHES)}."
    look, problems = body.change_look(changes, preset or None)
    out = avatar_client.send("look", look=look, timeout=3) or {}
    worn = ", ".join(k.replace("_", " ") for k in body.SWITCHES if look[k] and not body.DEFAULT_LOOK[k])
    msg = "New look saved" + (f" (wearing {worn})" if worn else "") + "."
    if not out:
        msg += " My 3D body isn't on screen right now; I'll wear it when it is."
    elif not out.get("live"):
        msg += " But my live 3D body is off, so it shows only once Live 3D Nova is on in the tray menu."
    return msg + (" " + " ".join(problems) if problems else "")
