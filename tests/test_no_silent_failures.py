"""Failures that must not take a turn down still leave a breadcrumb, or say why they are silent."""
import ast
from pathlib import Path

from nova import store
from nova import log

ROOT = Path(__file__).resolve().parent.parent
NOVA = ROOT / "nova"


def _silent_handlers():
    """Every `except Exception: pass` (and `except BaseException: pass`) in nova/."""
    for path in sorted(NOVA.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            if "Exception" not in ast.unparse(node.type):
                continue
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                segment = "\n".join(lines[node.lineno - 1:node.end_lineno])
                yield path.relative_to(ROOT), node.lineno, segment


def test_every_silent_handler_says_why():
    offenders = [f"{path}:{line}" for path, line, segment in _silent_handlers() if "# intentional:" not in segment]
    assert not offenders, ("these handlers swallow an exception with no reason; call log_exception() "
                           "or add '# intentional: <why>':\n  " + "\n  ".join(offenders))


def test_log_writes_a_line_and_a_traceback(tmp_path, monkeypatch):
    monkeypatch.setattr(store.current(), "root", tmp_path)
    log.reset()
    try:
        raise ValueError("boom")
    except ValueError as e:
        assert log.log_exception("demo", e)
    text = (tmp_path / "task_log.txt").read_text(encoding="utf-8")
    assert "ERROR demo: ValueError: boom" in text
    assert "ValueError: boom" in text            # the traceback is there, not just the message
    assert "Traceback (most recent call last)" in text


def test_log_is_rate_limited_per_place(tmp_path, monkeypatch):
    monkeypatch.setattr(store.current(), "root", tmp_path)
    log.reset()
    assert log.log_exception("same", RuntimeError("first")) is True
    assert log.log_exception("same", RuntimeError("second")) is False     # one a minute is enough
    assert log.log_exception("elsewhere", RuntimeError("other")) is True
    log.reset()
    assert log.log_exception("same", RuntimeError("after reset")) is True
    text = (tmp_path / "task_log.txt").read_text(encoding="utf-8")
    assert "first" in text and "second" not in text and "after reset" in text


def test_log_never_raises_when_the_log_cannot_be_written(tmp_path, monkeypatch):
    (tmp_path / "task_log.txt").mkdir()        # opening a directory fails
    monkeypatch.setattr(store.current(), "root", tmp_path)
    log.reset()
    assert log.log_exception("unwritable", RuntimeError("x")) is False


def test_log_uses_the_current_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(store.current(), "root", tmp_path)
    log.reset()
    try:
        raise KeyError("missing")
    except KeyError:
        assert log.log_exception("implicit") is True
    assert "KeyError" in (tmp_path / "task_log.txt").read_text(encoding="utf-8")
