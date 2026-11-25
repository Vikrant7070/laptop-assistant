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


def test_close_without_saving_presses_dont_save_even_if_the_brain_forgets(brain, desktop, monkeypatch):
    # Final QA 2026-11-25: the brain called window_action(close) without discard_changes, and the prompt went back
    # to the user although they had said "without saving".
    import json
    from nova import store
    from nova.actions.tools import forms
    s = app.settings()
    s["autonomy"] = "trusted"
    store.current().settings.write_text(json.dumps(s), encoding="utf-8")
    desktop.set_windows([(7, "*Untitled - Notepad", "notepad.exe")], front=7)
    clicked = []
    monkeypatch.setattr(desktop, "close", lambda hwnd: None)
    monkeypatch.setattr(forms, "buttons_in", lambda hwnd: [("Save", 1, 1), ("Don't save", 2, 2), ("Cancel", 3, 3)]
                        if desktop.is_window(hwnd) else [])

    def click(x, y, *a, label="", **k):
        clicked.append(label)
        desktop.alive.discard(7)
        desktop.scene = []
    monkeypatch.setattr(forms, "_nova_click", click)
    real = app.tools.run_tool
    brain([("", [("window_action", {"title_or_app": "notepad", "action": "close"})]), ("Closed it.", [])])
    monkeypatch.setattr(app.tools, "run_tool", lambda n, a: real(n, a))
    app.answer("close notepad without saving")
    assert clicked == ["Don't save"] and not desktop.is_window(7)
