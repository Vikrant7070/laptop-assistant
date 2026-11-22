"""3D Nova's commands: what each `{"do": ...}` sent to her window (avatar_client.send) does.

Each command is `cmd_<name>(nova, cmd, done, result)`, looked up in COMMANDS by `Nova.handle`. `done` is the
caller's Event (None for commands she queues for herself) and `result` the dict sent back to it.

A command that finishes later (she walks somewhere first) starts a thread that sets `done` when it's over, and
returns True. Then handle() doesn't add her position or set `done` itself. Otherwise handle() does both.
(This was one 130-line if/elif in Nova.handle; roadmap R6 step 3. Every branch body is unchanged.)
"""
import os
import time

from nova import runtime
from nova.avatar import body
from nova.platform import avatar_window as win32


def cmd_anim(nova, cmd, done, result):
    if cmd.get("name") not in nova.sp.anims:
        return
    name = cmd["name"]
    if nova.asleep:
        name = "nap"     # napping: she stays lying down (her mouth still moves if she talks)
    nova.base = name


def cmd_sleep(nova, cmd, done, result):
    if cmd.get("on"):
        nova.nap()
        if cmd.get("deep") and not nova.deep:
            nova.deep = True       # her mic is off now - say so once, quietly
            nova.think_aloud("💤 mic off - click me to wake me", 8)
    else:
        nova.wake_up(clicked=bool(cmd.get("clicked")))


def cmd_work(nova, cmd, done, result):
    on, now = bool(cmd.get("on")), time.time()
    if on and not nova.working and now - nova.working_end >= nova.WORK_HOLD:
        nova.working_since = now         # a new job (not the next step of the same one): the desk comes out
    if nova.working and not on:
        nova.working_end = now
    nova.working = on


def cmd__doze(nova, cmd, done, result):
    if nova.asleep:
        nova.base = "nap"
        nova.start_oneshot("yawn", after="liedown")


def cmd_say(nova, cmd, done, result):
    nova.say(cmd.get("text", ""), float(cmd.get("secs", 6)))


def cmd_thought(nova, cmd, done, result):
    nova.think_aloud(cmd.get("text", ""), float(cmd.get("secs", 12)))


def cmd_walk_to(nova, cmd, done, result):
    ev = nova.walk_to(float(cmd["x"]), float(cmd["y"]))
    runtime.spawn("commands.cmd_walk_to", lambda: (ev.wait(15), done.set()))
    return True


def cmd_go_point(nova, cmd, done, result):
    tx, ty = float(cmd["x"]), float(cmd["y"])
    fx, fy, flip = nova.point_spot(tx, ty)
    ev = nova.walk_to(fx, fy)

    def then():
        ev.wait(15)
        nova.q.put(({"do": "_point", "flip": flip}, done, result))
    runtime.spawn("commands.then", then)
    return True


def cmd__point(nova, cmd, done, result):
    nova.facing_left = cmd["flip"]
    ev = nova.start_oneshot("point", hold_at=nova.sp.tap)
    runtime.spawn("commands.cmd__point", lambda: (ev.wait(5), done.set()))
    return True


def cmd_finish_point(nova, cmd, done, result):
    if nova.oneshot and nova.oneshot["name"] == "point":
        nova.oneshot["hold_at"] = None
        nova.oneshot["after"] = "happy"
    else:
        nova.start_oneshot("happy")


def cmd_happy(nova, cmd, done, result):          # also "wave"
    do = cmd.get("do")
    nova.start_oneshot(do)
    if not cmd.get("quiet"):     # quiet: the microphone is recording right now
        nova.sound(do)


def cmd_react(nova, cmd, done, result):
    ev = nova.react(cmd.get("name", ""), cmd.get("repeat"))
    result["played"] = bool(ev)
    result["sound"] = bool(ev) and getattr(nova, "last_sound", False)
    if ev and cmd.get("wait"):
        runtime.spawn("commands.cmd_react", lambda: (ev.wait(20), done.set()))
        return True


def cmd_carry_to(nova, cmd, done, result):
    nova.oneshot = None
    nova.carrying = True
    ev = nova.walk_to(float(cmd["x"]), float(cmd["y"]))

    def arrived():
        ev.wait(20)
        nova.carrying = False
        done.set()
    runtime.spawn("commands.arrived", arrived)
    return True


def cmd_clickthrough(nova, cmd, done, result):
    win32.set_clickthrough(nova.hwnd, nova.base_style, bool(cmd.get("on")))


def cmd_show(nova, cmd, done, result):
    nova.visible = True
    nova.root.deiconify()


def cmd_hide(nova, cmd, done, result):
    nova.visible = False
    nova.root.withdraw()


def cmd_duck(nova, cmd, done, result):          # step out of a screenshot for a moment
    nova.root.withdraw()


def cmd_unduck(nova, cmd, done, result):
    if nova.visible:
        nova.root.deiconify()


def cmd_quit(nova, cmd, done, result):          # the chat's ⏻ Quit button / tray "Quit Nova": close her for good
    done.set()
    runtime.spawn("commands.cmd_quit", lambda: os._exit(0), delay=0.3, daemon=False)
    return True


def cmd_move(nova, cmd, done, result):
    move, err = body.clean_move(**{k: cmd.get("move", {}).get(k) for k in ("name", "duration", "keys", "loop")})
    if err:
        result["error"] = err
        return
    nova.moves[move["name"]] = move
    result["live"] = nova.live_now()
    if cmd.get("play") is False:          # learn it now, play it later (after she has spoken)
        result["played"] = False
        if done:
            done.set()
        return True
    ev = nova.react(move["name"], cmd.get("repeat"))
    result["played"] = bool(ev)
    if ev and cmd.get("wait"):
        runtime.spawn("commands.cmd_move", lambda: (ev.wait(30), done.set()))
        return True


def cmd_forget_move(nova, cmd, done, result):
    nova.moves.pop(cmd.get("name"), None)


def cmd_look(nova, cmd, done, result):
    nova.look = {**body.DEFAULT_LOOK, **(cmd.get("look") or {})}
    nova.look_v += 1
    result["live"] = nova.live_now()


def cmd_speech(nova, cmd, done, result):
    nova.on_speech(cmd)


def cmd_listen(nova, cmd, done, result):
    on = bool(cmd.get("on"))
    nova.listen_until = time.time() + float(cmd.get("secs") or 6) if on else 0.0
    nova.hearing = on and bool(cmd.get("hearing"))


def cmd__heard(nova, cmd, done, result):
    nova.heard(cmd.get("what"))


COMMANDS = {name[len("cmd_"):]: fn for name, fn in list(globals().items()) if name.startswith("cmd_")}
COMMANDS["wave"] = cmd_happy
ERRANDS = ("walk_to", "go_point", "carry_to", "_point")      # she goes back to her corner a few seconds after these


def run(nova, cmd, done, result):
    """Do one command. True: it finishes `done` itself, later."""
    do = cmd.get("do")
    if do in ERRANDS:
        nova.last_errand = time.time()
    fn = COMMANDS.get(do)
    return bool(fn and fn(nova, cmd, done, result))
