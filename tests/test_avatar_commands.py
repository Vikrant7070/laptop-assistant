"""3D Nova's command table (nova/avatar/commands.py) as Nova.handle uses it, on a stand-in for her window."""
import threading
import types

import pytest

from nova.avatar import avatar, commands

# Every `do` the old 130-line if/elif in Nova.handle answered (roadmap R6 step 3 moved them into a table).
OLD = {"anim", "sleep", "work", "_doze", "say", "thought", "walk_to", "go_point", "_point", "finish_point", "happy",
       "wave", "react", "carry_to", "clickthrough", "show", "hide", "duck", "unduck", "quit", "move", "forget_move",
       "look", "speech", "listen", "_heard"}


def nova(**kw):
    calls = []
    n = types.SimpleNamespace(
        pos=[10.0, 20.0], asleep=False, base="idle", visible=True, oneshot=None, moves={"spin": {}}, calls=calls,
        sp=types.SimpleNamespace(anims={"idle": 1, "talk": 1}, tap=3), last_errand=0.0,
        say=lambda text, secs: calls.append(("say", text, secs)),
        think_aloud=lambda text, secs: calls.append(("think", text, secs)),
        start_oneshot=lambda name, **k: calls.append(("oneshot", name)) or threading.Event(),
        sound=lambda name: calls.append(("sound", name)),
        walk_to=lambda x, y: calls.append(("walk", x, y)) or threading.Event(),
        root=types.SimpleNamespace(deiconify=lambda: calls.append("shown"), withdraw=lambda: calls.append("hidden")),
    )
    for k, v in kw.items():
        setattr(n, k, v)
    return n


def handle(n, cmd, result=None):
    done = threading.Event()
    result = {} if result is None else result
    avatar.Nova.handle(n, cmd, done, result)
    return done, result


def test_every_old_command_is_in_the_table():
    assert set(commands.COMMANDS) == OLD


def test_a_quick_command_ends_with_her_position():
    n = nova()
    done, result = handle(n, {"do": "say", "text": "hi", "secs": 3})
    assert done.is_set() and result["pos"] == [10.0, 20.0] and n.calls == [("say", "hi", 3.0)]


def test_a_walk_finishes_later_and_counts_as_an_errand():
    n = nova()
    done, result = handle(n, {"do": "walk_to", "x": 5, "y": 6})
    assert n.calls == [("walk", 5.0, 6.0)] and "pos" not in result and n.last_errand > 0
    assert not done.is_set()          # set by her thread once she arrives


@pytest.mark.parametrize("name, expect", [("talk", "talk"), ("nope", "idle")])
def test_anim_only_known_names(name, expect):
    n = nova()
    done, _ = handle(n, {"do": "anim", "name": name})
    assert n.base == expect and done.is_set()


def test_anim_while_asleep_stays_napping():
    n = nova(asleep=True)
    handle(n, {"do": "anim", "name": "talk"})
    assert n.base == "nap"


def test_wave_is_happy_with_its_own_name_and_quiet_skips_the_sound():
    n = nova()
    handle(n, {"do": "wave"})
    handle(n, {"do": "happy", "quiet": True})
    assert n.calls == [("oneshot", "wave"), ("sound", "wave"), ("oneshot", "happy")]


def test_unknown_command_just_answers():
    done, result = handle(nova(), {"do": "fly"})
    assert done.is_set() and result == {"pos": [10.0, 20.0]}


def test_show_hide_and_commands_she_queues_for_herself():
    n = nova()
    handle(n, {"do": "hide"})
    assert n.visible is False and n.calls == ["hidden"]
    avatar.Nova.handle(n, {"do": "show"}, None, None)          # her own queue: no caller, no result
    assert n.visible is True and n.calls[-1] == "shown"


def test_move_with_an_error_still_answers(monkeypatch):
    monkeypatch.setattr(commands.body, "clean_move", lambda **k: (None, "bad keys"))
    done, result = handle(nova(), {"do": "move", "move": {"name": "x"}})
    assert result["error"] == "bad keys" and done.is_set() and "pos" in result


def test_clickthrough_uses_the_win32_helper(monkeypatch):
    got = []
    monkeypatch.setattr(commands.win32, "set_clickthrough", lambda hwnd, style, on: got.append((hwnd, style, on)))
    handle(nova(hwnd=7, base_style=0x80000), {"do": "clickthrough", "on": True})
    assert got == [(7, 0x80000, True)]
