"""The laptop around Nova: start with Windows, open folders, the window / tray hooks."""


import json
import os
import time
from pathlib import Path

from flask import jsonify, request, send_file

from nova.avatar import avatar_client
from nova.core import characters
from nova.actions import tools
from nova.shared import ps_escape
from nova.core.app import api


STARTUP_LNK = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Nova.lnk"


def set_autostart(on):
    """Start Nova (hidden in the tray) when Windows starts."""
    if not on:
        STARTUP_LNK.unlink(missing_ok=True)
        return
    import sys
    pyw = Path(sys.executable).with_name("pythonw.exe")
    tools._ps(
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{ps_escape(STARTUP_LNK)}');"
        f"$s.TargetPath='{ps_escape(pyw)}';$s.Arguments='\"{ps_escape(config.ROOT / 'desktop.py')}\"';"
        f"$s.WorkingDirectory='{ps_escape(config.ROOT)}';$s.Save()")


@api.app.get("/api/look")
def get_look():
    """3D Nova's saved look (nova/avatar/body.py) - the 3D preview page dresses her in it."""
    from nova.avatar import body
    return jsonify(body.load_look())


@api.app.get("/api/model")
def get_model():
    """The chosen character's 3D model for nova3d.html (characters/<id>/: a VRM made in VRoid Studio): where to load
    it, the colours it was made with (so change_look can tint it) and how to fit the moves onto her body.
    ?character=lucy asks about another character (testing). vrm is null when her folder has no model.vrm."""
    from nova.avatar import body
    ch = characters.get(request.args.get("character") or config.settings().get("character"))
    path = characters.model_path(ch)
    # ?v= changes whenever the file does (a new export from VRoid Studio): the 3D page then loads the new model
    vrm = f"/characters/{ch['id']}/model.vrm?v={path.stat().st_mtime_ns}" if path else None
    style = body.character_style(ch["id"])      # how she shows feelings and how big she moves (character.json)
    return jsonify(id=ch["id"], name=ch["name"], vrm=vrm,
                   base={**body.DEFAULT_LOOK, **ch["look"]}, fit=ch["model"]["fit"],
                   style={"size": style["size"], "faces": style["faces"], "walk": style["walk"],
                          # ?full=1 (the joint check): her own moves and talking gestures too
                          **({"moves": style["moves"], "gestures": style["gestures"]} if request.args.get("full") else {})})


@api.app.get("/api/dev/character_ids")
def dev_character_ids():
    """Every character with a folder (the 3D page's joint check runs through all of them)."""
    return jsonify(ids=list(characters.CHARACTERS))


@api.app.post("/api/dev/joint_report")
def dev_joint_report():
    """The 3D page's joint check (nova3d.html?check=1): every pose measured on each character's bones.
    Saved as tests/joint_report.json; tests/test_joints.py reads it."""
    from nova.avatar import joint_check
    report = request.get_json(force=True)
    report["inputs"] = joint_check.inputs_digest()       # what it was measured on (the tests compare)
    joint_check.REPORT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return jsonify(ok=True)


@api.app.get("/characters/<cid>/model.vrm")
def character_model(cid):
    """A character's VRM file (characters/<id>/model.vrm), only for this laptop's own pages."""
    if cid not in characters.CHARACTERS:
        return ("", 404)
    path = characters.model_path(characters.get(cid))
    if not path:
        return ("", 404)
    return send_file(path, mimetype="application/octet-stream", max_age=0)


show_window_hook = None  # set by desktop.py


@api.app.post("/api/wake")
def wake_by_click():
    """She was clicked while asleep (avatar.py): wake her up - ears back on, the nap clock starts again."""
    was = voice_io.nap["asleep"]
    voice_io.nap["last_task"] = time.time()
    voice_io.wake_body(clicked=True)
    if was:
        conversation.task_log("WOKE by a click")
    return jsonify(ok=True, was_asleep=was)


@api.app.post("/api/show_window")
def show_window():
    voice_io.wake_body()            # clicking her wakes her up
    if show_window_hook:
        show_window_hook()
    return jsonify(ok=True)


@api.app.post("/api/avatar")
def avatar():
    """Chat window tells desktop Nova what she's doing: talking, thinking, happy, or a line to say."""
    body = api._body()
    mood = body.get("state")
    # Her lips are moved only by the voice itself (voice.Mouth), at the moment the sound plays. The chat window's
    # "talk" (and an "idle" while she's still speaking) would put them out of step with the audio, so they're ignored.
    if mood == "talk" or (mood == "idle" and voice_io.mouth.busy()):
        mood = None
    if mood in ("think", "idle", "sleepy"):
        avatar_client.send("anim", name=mood, timeout=2)
    elif mood in ("happy", "wave"):
        avatar_client.send(mood, timeout=2)
    say = api._text(body, "say")[:500]
    if say:
        avatar_client.send("say", text=say, secs=min(14, 3 + len(say) / 15), timeout=2)
    return jsonify(ok=True)


@api.app.post("/api/voice_typing")
def voice_typing():
    """Desktop window has no browser speech recognition, so start Windows voice typing (Win+H).
    ?fresh=1 first closes a voice-typing panel that may still be open, so Win+H starts listening again
    instead of toggling it off."""
    if request.args.get("fresh"):
        tools.keyboard.send("esc")
        time.sleep(0.3)
    tools.keyboard.send("windows+h")
    return jsonify(ok=True)


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import config, conversation, voice_io  # noqa: E402
