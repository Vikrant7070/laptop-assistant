"""Manager plan 4e-2: a level-1 change that can be undone keeps an undo record; "undo that" puts it back. Nothing is
ever deleted for good (taken-away things go to the Recycle Bin - faked here); a record is used once."""
import json

import pytest

from nova import platform, store, task_state
from nova.actions import tools, undo
from nova.actions.tools import common
from nova.core import app
from nova.core.app import resume, tasks_page

REAL_RUN_TOOL = tools.run_tool


@pytest.fixture
def recycled(monkeypatch):
    """The Recycle Bin, faked: the path is moved out of the way and remembered."""
    seen = []

    def recycle(path):
        from pathlib import Path
        import shutil
        p = Path(path)
        seen.append(p.name)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        return f"Moved {p} to the Recycle Bin."
    monkeypatch.setattr(undo, "_recycle", recycle)
    return seen


def _run(name, **args):
    call = {"name": name, "args": args}
    snap = undo.before(call)
    out = tools.run_tool(name, args)
    return undo.after(call, out, not out.startswith(("Error", "Not")), snap, "task-1"), out


def test_a_move_is_put_back(tmp_path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    rec, _ = _run("move_or_rename", source=str(tmp_path / "a.txt"), destination=str(tmp_path / "b.txt"))
    assert rec["what"] == f"moved a.txt to {tmp_path / 'b.txt'}" and rec["task"] == "task-1"
    assert undo.undo().startswith("Undone: moved a.txt")
    assert (tmp_path / "a.txt").exists() and not (tmp_path / "b.txt").exists()
    assert undo.undo() == "There's nothing I can undo."                     # used once


def test_a_move_onto_something_that_was_there_keeps_no_record(tmp_path):
    (tmp_path / "a.txt").write_text("new", encoding="utf-8")
    (tmp_path / "b.txt").write_text("old", encoding="utf-8")
    rec, _ = _run("move_or_rename", source=str(tmp_path / "a.txt"), destination=str(tmp_path / "b.txt"))
    assert rec is None                                                       # putting it back could lose b.txt


def test_a_move_into_a_folder(tmp_path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "box").mkdir()
    rec, _ = _run("move_or_rename", source=str(tmp_path / "a.txt"), destination=str(tmp_path / "box"))
    assert rec["snap"]["final"] == str(tmp_path / "box" / "a.txt")
    undo.undo(str(rec["id"]))
    assert (tmp_path / "a.txt").exists()


def test_a_changed_file_gets_its_old_text_back_and_a_new_file_goes_to_the_bin(tmp_path, recycled):
    f = tmp_path / "notes.txt"
    f.write_text("old text", encoding="utf-8")
    rec, _ = _run("write_text_file", path=str(f), content="new text")
    assert f.read_text(encoding="utf-8") == "new text" and rec["what"] == f"changed {f}"
    assert "put the old text of notes.txt back" in undo.undo("notes")
    assert f.read_text(encoding="utf-8") == "old text"
    g = tmp_path / "fresh.txt"
    _run("write_text_file", path=str(g), content="x")
    assert "moved the new file fresh.txt to the Recycle Bin" in undo.undo()
    assert recycled == ["fresh.txt"] and not g.exists()


def test_a_new_folder_goes_to_the_bin_only_while_empty(tmp_path, recycled):
    _run("create_folder", path=str(tmp_path / "Taxes"))
    (tmp_path / "Taxes" / "keep.pdf").write_text("x", encoding="utf-8")
    out = undo.undo()
    assert "has things in it now, so I left it" in out and (tmp_path / "Taxes" / "keep.pdf").exists()
    assert undo.records()[-1]["state"] == "can't undo"
    _run("create_folder", path=str(tmp_path / "Empty"))
    assert "Recycle Bin" in undo.undo() and recycled == ["Empty"]


def test_a_copy_goes_to_the_bin(tmp_path, recycled):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    _run("copy_path", source=str(tmp_path / "a.txt"), destination=str(tmp_path / "a copy.txt"))
    assert "moved the copy to the Recycle Bin" in undo.undo()
    assert (tmp_path / "a.txt").exists() and recycled == ["a copy.txt"]


def test_settings_go_back_to_their_old_value(monkeypatch):
    desk = platform.current()
    desk.dark = False
    _run("set_dark_mode", enabled=True)
    assert undo.records()[-1]["snap"] == {"dark": False}
    undo.undo()
    assert desk.log[-1] == ("dark_mode", False)
    ran = []
    monkeypatch.setitem(tools.FUNCS, "set_volume", lambda action, steps=5: ran.append((action, steps)) or "ok")
    _run("set_volume", action="up", steps=4)
    undo.undo()
    assert ran == [("up", 4), ("down", 4)]
    monkeypatch.setattr(common, "_ps", lambda cmd: "40\n")
    monkeypatch.setitem(tools.FUNCS, "set_brightness", lambda percent: ran.append(("bright", percent)) or "ok")
    _run("set_brightness", percent=90)
    undo.undo()
    assert ran[-2:] == [("bright", 90), ("bright", 40)]


def test_failed_steps_and_reads_keep_no_record(tmp_path):
    assert undo.before({"name": "read_text_file", "args": {"path": "x"}}) is None
    rec, out = _run("move_or_rename", source=str(tmp_path / "missing.txt"), destination=str(tmp_path / "b.txt"))
    assert rec is None and undo.records() == []


def test_in_a_task_the_strip_and_tasks_page_offer_undo_and_she_can_undo_it(brain, monkeypatch, tmp_path):
    s = app.settings()
    s["autonomy"] = "trusted"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")
    (tmp_path / "report.txt").write_text("r", encoding="utf-8")
    fake, ran = brain([("", [("move_or_rename", {"source": str(tmp_path / "report.txt"),
                                                  "destination": str(tmp_path / "final.txt")})]),
                       ("Renamed it.", []),
                       ("", [("undo_last_change", {"which": "latest"})]),
                       ("Put it back.", [])])
    monkeypatch.setattr(tools, "run_tool", lambda n, a: (ran.append((n, a)), REAL_RUN_TOOL(n, a))[1])
    app.answer("rename report.txt to final.txt")
    card = task_state.of(app.state["task"])
    assert resume.strip_for(card)["undo"][0]["what"] == f"moved report.txt to {tmp_path / 'final.txt'}"
    detail = tasks_page.detail("t:" + card["id"])
    assert detail["undo"][0]["state"] == "can undo"
    out, _ = app.answer("undo that")
    assert (tmp_path / "report.txt").exists() and not (tmp_path / "final.txt").exists()
    assert out["reply"] == "Put it back." and undo.records()[-1]["state"] == "undone"
    assert resume.strip_for(card)["undo"] == []


def test_every_undoable_tool_has_a_level_1():
    from nova.actions import risk
    assert all(risk.level(t) == 1 for t in undo.UNDOABLE) and risk.level("undo_last_change") == 1


def test_settings_page_explains_the_levels():
    from pathlib import Path
    html = (Path(app.ROOT) / "static" / "index.html").read_text(encoding="utf-8")
    assert "Level 2 - hard to undo, or leaves the laptop" in html and "<b>always asks</b>" in html
    assert 'name="aut" value="careful"' in html
