"""The speech bubble sits just above her head (where the live renderer says it is), not above her whole frame."""
import types

from PIL import Image, ImageFont

from nova.avatar import avatar


def bubble(head, monkeypatch, fw=260, fh=340):
    monkeypatch.setattr(avatar, "stand_area", lambda: (0, 0, 4000, 3000))
    ns = types.SimpleNamespace(font=ImageFont.load_default())
    return avatar.Nova.compose_bubble(ns, Image.new("RGBA", (fw, fh)), "Hello there", False, 1000, head)


def tail_tip(canvas):
    """Lowest painted pixel of the bubble (the frame itself is empty in these tests)."""
    return canvas.getchannel("A").getbbox()[3]


def test_tail_points_just_above_her_head(monkeypatch):
    canvas, xoff, yoff = bubble((130, 80), monkeypatch)          # her head starts 80 px down her frame
    assert abs(tail_tip(canvas) - (yoff + 80)) <= 6               # tip right above it
    assert yoff < 80                                               # the bubble overlaps the empty space above her


def test_bubble_follows_her_head_sideways(monkeypatch):
    left, xl, _ = bubble((60, 80), monkeypatch)
    right, xr, _ = bubble((200, 80), monkeypatch)
    box_l, box_r = left.getchannel("A").getbbox(), right.getchannel("A").getbbox()
    assert (box_r[0] - xr) - (box_l[0] - xl) > 100                # moved right with her head


def test_no_head_known_keeps_the_old_place(monkeypatch):
    canvas, xoff, yoff = bubble(None, monkeypatch)
    assert tail_tip(canvas) <= yoff + 3                            # at the top of her frame, as before (line width)


def test_anchor_glides_and_jumps(monkeypatch):
    n = types.SimpleNamespace(live_head=(130, 80), head_s=None)
    img = Image.new("RGBA", (260, 340))
    assert avatar.Nova.bubble_anchor(n, img) == (130, 80)
    n.live_head = (130, 90)
    x, y = avatar.Nova.bubble_anchor(n, img)
    assert 80 < y < 90                                             # small moves are smoothed
    n.live_head = (40, 250)                                        # lying down: straight there
    assert avatar.Nova.bubble_anchor(n, img) == (40, 250)
