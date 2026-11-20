"""Every joint of every pose looks natural, on each character's real 3D model.

The measuring happens in the 3D page itself (static/nova3d.html, jointCheck), on the real VRM bones and on each
body's measured shape: it plays every built-in animation and each character's own moves and talking gestures frame
by frame and checks each joint against what a human body does:
  elbows / wrists / knees    bend no further than a person's, and the right way (no backwards forearm, no
                             forearm swinging straight out from a hanging arm, knees fold backwards)
  neck                       the head turns, nods and tilts within a neck's range
  body                       hands, forearms and elbows never pass into her body or head, forearms never
                             through each other
  touch                      a hand meant to rest on her chest, tummy, hip, back, chin, mouth or forehead is there
  feet                       never below the bottom of the screen
  speed                      no bone pops from one frame to the next (as seen on screen)
and runs a self-test (poses that break each rule on purpose) so a blind checker can't pass.
Results: tests/joint_report.json, made by `python -m nova.avatar.joint_check` (Nova running). When the report was
made from another version of the 3D page, a character's moves or a model, the first test makes a fresh one.
"""
import json

import pytest

from nova.avatar import body, joint_check
from nova.core import characters

ROOT = characters.ROOT.parent
PAGE = ROOT / "static" / "nova3d.html"
# The built-in animations the page has (its ANIMS table) - every one must have been checked.
PAGE_TEXT = PAGE.read_text(encoding="utf-8")
ANIMS = [a for a in body.BUILT_IN if f"{a}:" in PAGE_TEXT.split("const ANIMS = {", 1)[1].split("};", 1)[0]]


@pytest.fixture(scope="module")
def report():
    """The joint report, made fresh when the poses or models changed since the last one."""
    if not joint_check.current():
        if not joint_check.nova_running():
            pytest.fail("tests/joint_report.json was made from another version of the 3D page, a character or a model, "
                        "and Nova isn't running to make a new one. Start Nova, then: python -m nova.avatar.joint_check")
        import subprocess
        import sys
        subprocess.run([sys.executable, "-m", "nova.avatar.joint_check"], cwd=ROOT, timeout=300)
    assert joint_check.current(), "the joint check did not save a report for the current page, characters and models"
    return json.loads(joint_check.REPORT.read_text(encoding="utf-8"))


def by_character(report):
    return {c["character"]: c for c in report["characters"]}


def test_every_character_with_a_model_was_checked(report):
    want = {cid for cid in characters.CHARACTERS if characters.model_path(characters.get(cid))}
    assert want <= set(by_character(report))


def test_every_pose_was_checked(report):
    """All built-in animations, and each character's own moves and talking gestures, on her own model."""
    for cid, c in by_character(report).items():
        style = body.character_style(cid)
        want = set(ANIMS) | {"move:" + m for m in style["moves"]} | {"gesture:" + g for g in style["gestures"]}
        missing = want - set(c["poses"])
        assert not missing, f"{cid}: not checked: {sorted(missing)}"


def test_no_joint_looks_unnatural(report):
    lines = []
    for cid, c in by_character(report).items():
        seen = set()
        for p in c["problems"]:
            key = (cid, p["pose"], p["rule"], p["part"])
            if key not in seen:
                seen.add(key)
                lines.append(f"{cid} {p['pose']} @{p['t']}: {p['part']} - {p['rule']} ({p['value']}, limit {p['limit']})")
    assert not lines, f"{len(lines)} unnatural joints:\n" + "\n".join(lines[:25])


def test_the_checker_catches_each_kind_of_mistake(report):
    """Its self-test bends joints past human limits on purpose: each rule must notice, or the checker is blind."""
    for cid, c in by_character(report).items():
        missed = [rule for rule, caught in c["self_test"].items() if not caught]
        assert not missed, f"{cid}: the joint check missed its own broken poses: {missed}"
        assert len(c["self_test"]) >= 8


def test_limits_stay_human(report):
    """Nobody quietly loosens the limits to make a pose pass."""
    L = report["limits"]
    assert L["elbow"] <= 150 and L["wrist"] <= 80 and L["knee"] <= 150
    assert L["neckYaw"] <= 80 and L["neckPitch"] <= 55 and L["neckRoll"] <= 40
    assert L["step"] <= 40 and L["stepHand"] <= 55 and L["torso"] >= .85 and L["contact"] <= 1.5


def test_hands_really_touch_where_the_pose_says(report):
    """Hands on her heart, a hand on her tummy while laughing, her chin on her hand, behind her back, on her hip."""
    for cid, c in by_character(report).items():
        w = c["worst"]
        assert "touch_chest" in w["love"] and "touch_tummy" in w["laugh"] and "touch_chin" in w["think"]
        assert "touch_forehead" in w["facepalm"]
        style = body.character_style(cid)
        for name, m in style["moves"].items():
            keys = json.dumps(m["keys"])
            if "hand_behind" in keys or "hands_behind" in keys:
                assert "touch_back" in w["move:" + name], f"{cid} {name}"
            if "hand_on_hip" in keys:
                assert "touch_hip" in w["move:" + name], f"{cid} {name}"


def test_elbows_and_wrists_actually_move(report):
    """The old bug: straight arms and stiff wrists. Gestures bend the elbows, waves and shrugs bend the wrists."""
    for cid, c in by_character(report).items():
        w = c["worst"]
        assert w["clap"]["elbow"] > 30 and w["love"]["elbow"] > 60 and w["confused"]["elbow"] > 50
        assert w["wave"]["wrist"] > 15 and w["confused"]["wrist"] > 25
        assert w["walk"]["knee"] > 5


def test_new_move_keys_are_kept():
    move, err = body.clean_move("holdy", 2, [
        {"at": 0.3, "hands_behind": 1, "right_hand": "fist"},
        {"at": 0.6, "right_hand_on_chin": 1, "left_hand_on_hip": 1, "left_hand": "open"},
        {"at": 0.9, "right_hand_on_chest": 2, "left_hand": "claw"}])
    assert err is not None and "left_hand" in err          # unknown finger shape
    move, err = body.clean_move("holdy", 2, [
        {"at": 0.3, "hands_behind": 1, "right_hand": "fist"},
        {"at": 0.6, "right_hand_on_chin": 1, "left_hand_on_hip": 1, "left_hand": "open"},
        {"at": 0.9, "right_hand_on_chest": 2}])
    assert err is None
    assert move["keys"][0] == {"at": 0.3, "hands_behind": 1, "right_hand": "fist"}
    assert move["keys"][1]["right_hand_on_chin"] == 1 and move["keys"][1]["left_hand"] == "open"
    assert move["keys"][2]["right_hand_on_chest"] == 1                                         # clamped to 0..1
