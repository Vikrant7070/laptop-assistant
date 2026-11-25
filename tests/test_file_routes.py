"""Exams 2026-11-23/25 and the QA: "rename X to Y" and "type 'X' and save it as PATH" failed every time - the brain
clicked through File Explorer instead of using its file tools. Code does both now."""
from nova.core import app


def test_rename_uses_the_file_tool_and_stays_in_the_folder(brain, tmp_path):
    (tmp_path / "old.txt").write_text("x", encoding="utf-8")
    fake, ran = brain([("Renamed it to new.txt.", [])])
    app.answer(f"rename {tmp_path / 'old.txt'} to new.txt")
    assert ran[0] == ("move_or_rename", {"source": str(tmp_path / "old.txt"), "destination": str(tmp_path / "new.txt")})


def test_rename_of_something_that_isnt_there_is_left_to_the_brain(brain, tmp_path):
    fake, ran = brain([("There's no nope.txt there.", [])])
    out, _ = app.answer(f"rename {tmp_path / 'nope.txt'} to new.txt")
    assert ran == [] and "no nope.txt" in out["reply"]


def test_type_and_save_writes_the_file_then_opens_it(brain, tmp_path):
    target = tmp_path / "multi.txt"
    fake, ran = brain([("Saved 'step one done' in multi.txt and opened it.", [])])
    app.answer(f"open Notepad, type 'step one done', and save it as {target}")
    assert ran[:2] == [("write_text_file", {"path": str(target), "content": "step one done"}),
                       ("open_path", {"path": str(target)})]
