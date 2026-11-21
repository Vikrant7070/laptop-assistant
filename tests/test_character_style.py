"""Each character reacts in her own way (characters/<id>/character.json "style"): her own moves, swaps for the
built-in reactions, idle habits, faces, how big and fast she moves, and what she does when clicked or poked.
The data lives in her folder; nova/avatar/body.py only reads it."""
import json
import types

from nova.avatar import avatar, body
from nova.core import app, characters

IDS = ("nova", "makima", "lucy")


def raw_style(cid):
    return json.loads((characters.ROOT / cid / "character.json").read_text(encoding="utf-8"))["style"]


def test_every_characters_style_is_valid():
    """No typos: every swap, fidget and touch names a built-in reaction or one of her own moves, every move is a
    valid new_move, and faces use real VRM expressions."""
    for cid in IDS:
        style, problems = body.clean_style(raw_style(cid), characters.get(cid)["name"])
        assert problems == [], (cid, problems)
        assert len(style["moves"]) == len(raw_style(cid).get("moves", {}))
        assert set(style["faces"]) <= set(body.FEELINGS)


def test_the_characters_react_differently():
    s = {cid: body.character_style(cid) for cid in IDS}
    assert s["makima"]["size"] < s["nova"]["size"] < s["lucy"]["size"]          # calm small moves .. big ones
    assert s["makima"]["speed"] < 1 < s["lucy"]["speed"]
    assert s["makima"]["talk_hands"] < s["nova"]["talk_hands"] < s["lucy"]["talk_hands"]
    assert len({st["touch"]["poke"] for st in s.values()}) == 3                 # each one's own answer to a poke
    assert len({json.dumps(st["fidgets"], sort_keys=True) for st in s.values()}) == 3
    assert s["makima"]["faces"]["happy"]["relaxed"] > s["makima"]["faces"]["happy"].get("happy", 0)  # a quiet smile
    assert body.styled(s["makima"], "laugh") == "quiet_chuckle" and body.styled(s["nova"], "laugh") == "laugh"
    assert body.styled(s["lucy"], "happy") == "cheer"
    assert all(st["touch"]["greeting"] and characters.get(cid)["name"] in st["touch"]["greeting"]
               for cid, st in s.items())


def test_nova_keeps_her_old_ways():
    """Nova is the baseline: full-size moves, two sentences in three with talking hands, the old right-click set."""
    st = body.character_style("nova")
    assert (st["size"], st["speed"]) == (1.0, 1.0)
    assert [body.talk_hands(n, st["talk_hands"]) for n in range(6)] == [True, True, False, True, True, False]
    assert {"laugh", "dance", "spin", "love", "clap"} <= set(st["touch"]["right_click"])


def test_style_is_read_fresh_from_her_folder(monkeypatch, tmp_path):
    d = tmp_path / "aria"
    d.mkdir()
    (d / "character.json").write_text(json.dumps({"name": "Aria", "voice": "af_bella"}), encoding="utf-8")
    monkeypatch.setattr(characters, "CHARACTERS", characters.load(tmp_path))
    monkeypatch.setattr(characters, "DEFAULT", "aria")
    assert body.character_style("aria")["fidgets"] == body.FIDGETS_DEFAULT     # no style: she reacts like Nova
    (d / "character.json").write_text(json.dumps({"name": "Aria", "voice": "af_bella", "style": {
        "size": 0.5, "fidgets": {"nod": 1}, "touch": {"greeting": "Hello, {NAME} here."}}}), encoding="utf-8")
    st = body.character_style("aria")                                         # an edit shows without a restart
    assert st["size"] == 0.5 and st["fidgets"] == {"nod": 1} and st["touch"]["greeting"] == "Hello, Aria here."


def test_bad_style_entries_are_dropped_and_reported():
    st, problems = body.clean_style({
        "size": 9, "swap": {"laugh": "moonwalk"}, "fidgets": {"nod": 3, "teleport": 5},
        "faces": {"happy": {"smug": 1, "relaxed": 2}, "bored": {"sad": 1}}, "touch": {"poke": "nope", "pokes": 50},
        "moves": {"wiggle": {"duration": 1, "keys": [{"at": 0.5, "right_elbow": 400}]}, "bad": {"keys": []}}})
    assert st["size"] == 1.6 and st["swap"] == {} and st["fidgets"] == {"nod": 3}
    assert st["faces"] == {"happy": {"relaxed": 1}} and st["touch"]["poke"] == body.TOUCH_DEFAULT["poke"]
    assert st["touch"]["pokes"] == 10 and st["moves"]["wiggle"]["keys"][0]["right_elbow"] == 150
    for word in ("moonwalk", "teleport", "smug", "bored", "nope", "move bad"):
        assert any(word in p for p in problems), word


def test_fidgets_follow_her_weights():
    st = {"fidgets": {"look": 1, "fold_hands": 3}}
    picks = [body.pick_fidget(st, lambda x=x: x) for x in (0.0, 0.24, 0.3, 0.99)]     # look = the first quarter
    assert picks == ["look", "look", "fold_hands", "fold_hands"]
    assert body.pick_fidget(st, lambda: 0.1, sleepy=True) == "yawn"


def test_talking_hands_follow_her_style():
    plain = "The file is saved in the documents folder now"
    assert body.speech_gesture(plain, 2.0, 0, hands=0) is None
    assert body.speech_gesture(plain, 2.0, 0, hands=1)["name"].startswith("gesture_")
    assert body.speech_gesture("haha that was funny", 2.0, 0, hands=0) == "laugh"   # reactions still play
    you = "It is in your documents folder"
    assert body.speech_gesture(you, 2.0, 0, hands=0.3) is None                     # calm Makima: still hands
    assert body.speech_gesture(you, 2.0, 0)["name"] == "gesture_point_you"         # Nova still points at you


def test_moves_can_bend_elbows_and_look_angry():
    move, err = body.clean_move("cross_arms", 2, [{"at": 0.5, "right_elbow": 100, "left_elbow": 160, "eyes": "angry"}])
    assert err is None and move["keys"][0]["right_elbow"] == 100 and move["keys"][0]["left_elbow"] == 150


def fake_nova(cid, live=True):
    n = types.SimpleNamespace(style=body.character_style(cid), moves={}, carrying=False, target=None, played=[],
                              sounds=[], sp=avatar.Rig(), pokes=[],
                              said=[])
    n.live_now = lambda: live
    n.sound = n.sounds.append
    n.start_oneshot = lambda name, **k: n.played.append((name, k.get("speed"))) or True
    n.react = lambda name, repeat=None, sound=True: avatar.Nova.react(n, name, repeat, sound)
    n.say = lambda text, secs=6: n.said.append(text)
    return n


def test_react_plays_her_own_version_at_her_speed():
    m = fake_nova("makima")
    avatar.Nova.react(m, "laugh")
    assert m.played == [("move:quiet_chuckle", m.style["speed"])] and m.sounds == ["laugh"]
    n = fake_nova("nova")
    avatar.Nova.react(n, "laugh")
    assert n.played == [("laugh", 1.0)]
    waiting = fake_nova("makima", live=False)        # renderer not drawing her yet: the built-in laugh
    avatar.Nova.react(waiting, "laugh")
    assert waiting.played == [("laugh", waiting.style["speed"])]


def test_clicks_and_pokes_get_her_own_reaction():
    lucy = fake_nova("lucy")
    t = lucy.style["touch"]
    avatar.Nova.touched(lucy, 100.0)
    assert lucy.played[-1][0] == "move:" + body.styled(lucy.style, t["click"]) == "move:big_wave" and not lucy.said
    for i in range(t["pokes"] - 1):
        avatar.Nova.touched(lucy, 101.0 + i * .5)
    assert lucy.played[-1][0] == "move:" + t["poke"] and lucy.said == [t["poke_say"]]
    slow = fake_nova("makima")
    for i in range(6):                                # clicks far apart are never a poke
        avatar.Nova.touched(slow, 100.0 + i * 10)
    assert {p[0] for p in slow.played} == {"move:" + slow.style["touch"]["click"]} and not slow.said


def test_renderer_gets_her_faces_and_size(client, monkeypatch):
    monkeypatch.setattr(app, "settings", lambda: {"character": "makima"})
    j = client.get("/api/model").get_json()
    assert j["style"]["size"] == body.character_style("makima")["size"]
    assert j["style"]["faces"]["happy"]["relaxed"] > 0.5
    assert j["fit"]["floor"] > 0                       # space under her shoes at the bottom of the screen


def test_3d_page_reads_style_and_floor():
    page = (characters.ROOT.parent / "static" / "nova3d.html").read_text(encoding="utf-8")
    for bit in ("applyStyle(j.style)", "STYLE.faces", "f.floor", "FLOOR - box.min.y * k", "elbows[i]", "_elbow"):
        assert bit in page, bit


def test_each_character_walks_talks_and_dances_her_own_way():
    s = {cid: body.character_style(cid) for cid in IDS}
    assert s["makima"]["walk"]["hands"] == "behind" and s["makima"]["walk"]["pace"] < 1      # slow, hands behind
    assert s["lucy"]["walk"]["bounce"] > s["nova"]["walk"]["bounce"] > s["makima"]["walk"]["bounce"]
    assert s["lucy"]["walk"]["sway"] > 0 and s["nova"]["walk"] == body.WALK_DEFAULT
    for cid in ("makima", "lucy"):                        # her own talking hands for every shared gesture
        assert set(s[cid]["gestures"]) == set(body.GESTURES)
        own = body.styled(s[cid], "dance")
        assert own in s[cid]["moves"] and s[cid]["moves"][own]["duration"] == 2.0     # 4 beats, for music
    assert s["makima"]["gestures"]["point_you"] != s["lucy"]["gestures"]["point_you"]
    g = body.gesture_move("explain", 2.0, s["lucy"]["gestures"])
    assert g["keys"] == s["lucy"]["gestures"]["explain"]["keys"] and body.gesture_move("explain", 2.0)["keys"] == body.GESTURES["explain"]


def test_wrists_and_forearm_curl_are_move_keys():
    move, err = body.clean_move("hand_wave", 1, [{"at": 0.5, "right_wrist": -90, "right_wrist_side": 30, "left_elbow_side": -75}])
    assert err is None and move["keys"][0] == {"at": 0.5, "right_wrist": -70, "right_wrist_side": 30, "left_elbow_side": -75}
    st, problems = body.clean_style({"walk": {"hands": "pockets", "bounce": 9}, "gestures": {"dab": {"keys": []}}})
    assert st["walk"]["bounce"] == 0.25 and st["walk"]["hands"] == "swing"
    assert any("pockets" in p for p in problems) and any("dab" in p for p in problems)


def test_music_plays_her_own_dance_on_the_beat():
    m = fake_nova("makima")
    m.ears = types.SimpleNamespace(beat=types.SimpleNamespace(music={"bpm": 120, "last_beat": 99.0, "period": 0.5},
                                                              loud_since=80.0))
    m.next_dance, m.asleep, m.oneshot, m.base = 0, False, None, "idle"
    avatar.Nova.music_dance(m, 100.0)
    assert m.played == [("move:makima_sway", None)] or m.played[0][0] == "move:makima_sway"


def test_renderer_gets_her_walk(client, monkeypatch):
    monkeypatch.setattr(app, "settings", lambda: {"character": "lucy"})
    assert client.get("/api/model").get_json()["style"]["walk"]["sway"] == body.character_style("lucy")["walk"]["sway"]
    page = (characters.ROOT.parent / "static" / "nova3d.html").read_text(encoding="utf-8")
    for bit in ('set(side + "Hand"', "STYLE.walk", '"behind"', "_wrist_side", "_elbow_side"):
        assert bit in page, bit


def test_she_never_greets_the_user_with_her_own_name(monkeypatch):
    """The small brain wrote "Hey Lucy! What's your vibe?" as Lucy (2 in 12 answers): her name is dropped."""
    assert app.no_self_greeting("Hey Lucy! What's your vibe tonight?", "Lucy") == "Hey! What's your vibe tonight?"
    assert app.no_self_greeting("Hey Nova, what's the plan?", "Nova") == "Hey, what's the plan?"
    assert app.no_self_greeting("Good morning Makima.", "Makima") == "Good morning."
    for fine in ("Lucy here - what's up?", "Hey Vikrant! Done.", "I'm Lucy, hi!", "Hey! Lucy is ready."):
        assert app.no_self_greeting(fine, "Lucy") == fine
    monkeypatch.setattr(app, "settings", lambda: {"character": "lucy"})
    said = []
    monkeypatch.setattr(app, "speak", said.append)
    s = app.SpeechStream()
    for piece in ("Hey ", "Lu", "cy! What", "'s your vibe tonight? ", "A cozy read?"):
        s.feed(piece)
    s.flush()
    assert said[0].startswith("Hey!") and "Lucy" not in " ".join(said)


def test_she_stands_above_an_auto_hidden_taskbar():
    """With the taskbar on auto-hide Windows calls the whole screen usable, and the popping-up taskbar covered her
    shoes (2026-11-20, 60 px). She stands on top of where it appears; a normal taskbar is already left out."""
    from nova.shared import stand_area
    assert stand_area((0, 0, 1920, 1200), (3, (0, 1140, 1920, 1200))) == (0, 0, 1920, 1140)
    assert stand_area((0, 0, 1536, 960), (3, (0, 1140, 1920, 1200))) == (0, 0, 1536, 912)     # not DPI-aware: scaled
    assert stand_area((0, 0, 1920, 1152), None) == (0, 0, 1920, 1152)                          # not auto-hidden
    assert stand_area((0, 0, 1920, 1200), (0, (0, 0, 60, 1200))) == (60, 0, 1920, 1200)        # on the left
    assert stand_area((0, 0, 1920, 1200), (1, (0, 0, 1920, 50))) == (0, 50, 1920, 1200)        # on top
