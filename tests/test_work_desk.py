"""💻 While she works on something (and isn't listening) 3D Nova sits at her desk typing on a laptop."""
import re
import types

from nova.avatar import avatar, body
from nova.core import app
from nova.shared import ROOT


def fake_nova(**over):
    """Just the bits of avatar.Nova that at_desk() looks at - no window, no renderer."""
    n = types.SimpleNamespace(working=True, working_since=0.0, working_end=0.0, asleep=False, listen_until=0.0, target=None, drag=None,
                              last_errand=0.0, live_now=lambda: True, sp=avatar.Rig())
    n.WORK_AFTER, n.WORK_HOLD = avatar.Nova.WORK_AFTER, avatar.Nova.WORK_HOLD
    for k, v in over.items():
        setattr(n, k, v)
    return n


def at_desk(n, now=1000.0):
    return avatar.Nova.at_desk(n, now)


def test_busy_for_a_moment_means_desk():
    assert at_desk(fake_nova(working_since=990.0))


def test_quick_answers_dont_flash_the_desk():
    assert not at_desk(fake_nova(working_since=999.5))


def test_no_desk_while_listening_napping_walking_or_on_an_errand():
    assert not at_desk(fake_nova(listen_until=1001.0))
    assert not at_desk(fake_nova(asleep=True))
    assert not at_desk(fake_nova(target=(10, 10)))
    assert not at_desk(fake_nova(last_errand=1000.0 - body.ROAM_RETURN_AFTER + 1))
    assert not at_desk(fake_nova(working=False))


def test_no_desk_until_the_renderer_draws_her():
    assert not at_desk(fake_nova(live_now=lambda: False))


def test_work_animation_exists_in_3d_page():
    page = (ROOT / "static" / "nova3d.html").read_text(encoding="utf-8")
    assert "work: 24" in page and 'name === "work"' in page


def test_rig_matches_the_3d_page():
    """avatar.Rig's frame size and animation lengths are the 3D page's own (W, H, ANIMS)."""
    page = (ROOT / "static" / "nova3d.html").read_text(encoding="utf-8")
    anims = re.search(r"const ANIMS = \{(.*?)\};", page, re.S).group(1)
    assert dict((k, int(v)) for k, v in re.findall(r"(\w+): (\d+)", anims)) == avatar.Rig.anims
    assert f"const W = {avatar.Rig.w}, H = {avatar.Rig.h};" in page
    assert not (ROOT / "static" / "nova3d").exists()        # no saved sprite frames any more


def test_app_tells_her_body_when_busy(monkeypatch):
    sent = []
    monkeypatch.setattr(app.avatar_client, "send", lambda do, **k: sent.append((do, k.get("on"))) or {"ok": True})
    monkeypatch.setattr(app.time, "sleep", lambda s: (_ for _ in ()).throw(SystemExit) if len(sent) >= 2 else None)
    busy = iter([True, True, False])
    monkeypatch.setattr(app.background, "busy_now", lambda: next(busy))
    try:
        app.work_watch()
    except SystemExit:
        pass
    assert sent == [("work", True), ("work", False)]


def test_busy_while_a_request_runs():
    assert not app.busy_now()
    with app.lock:
        assert app.busy_now()


def test_long_bubble_stays_on_screen_at_the_right_edge(monkeypatch):
    """She lives in the bottom-right corner: a long bubble slides left instead of running off the screen."""
    from PIL import Image, ImageFont
    monkeypatch.setattr(avatar, "stand_area", lambda: (0, 0, 1536, 960))
    ns = types.SimpleNamespace(font=ImageFont.load_default())
    frame = Image.new("RGBA", (208, 272))
    text = "Battery is full, RAM is at 77% and you have 72 GB free on the disk, so all is well boss " * 2
    sx = 1536 - 208 * .58                                   # her frame hangs over the right edge a little
    canvas, xoff, yoff = avatar.Nova.compose_bubble(ns, frame, text, False, sx)
    left = sx - xoff                                        # where the canvas starts on the screen
    bubble_right = left + canvas.width
    assert left >= 0
    assert bubble_right <= 1536 + (sx + 208 - 1536) + 1     # nothing beyond her own frame's edge
    assert xoff > 0                                         # the bubble sits to her left, she stays put


def test_desk_stays_between_the_steps_of_a_long_task():
    """The busy signal drops for a moment between steps: she keeps typing instead of blinking away from the desk."""
    gap = fake_nova(working=False, working_since=900.0, working_end=997.0)       # 3 s since the last step
    assert at_desk(gap)
    assert not at_desk(fake_nova(working=False, working_since=900.0, working_end=990.0))   # really finished (10 s)


def test_next_step_does_not_restart_the_wait():
    n = types.SimpleNamespace(working=False, working_since=900.0, working_end=time_now() - 2, WORK_HOLD=avatar.Nova.WORK_HOLD)
    avatar.Nova.handle(n, {"do": "work", "on": True}, None, None)
    assert n.working and n.working_since == 900.0                                 # still the same job
    n2 = types.SimpleNamespace(working=False, working_since=900.0, working_end=time_now() - 60, WORK_HOLD=avatar.Nova.WORK_HOLD)
    avatar.Nova.handle(n2, {"do": "work", "on": True}, None, None)
    assert n2.working_since > 900.0                                               # a new job: the 1.5 s wait applies


def time_now():
    import time
    return time.time()
