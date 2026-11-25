"""R15: the long prompts live in nova/prompts/*.md. This pins each one, so a prompt can't change by accident.

A prompt change is a behaviour change: run the task exam (`python -m nova.exam`, the user starts it) before and
after, then update the hash here in the same PR.
"""
import ast
import hashlib
import string
from pathlib import Path

import pytest

from nova import prompts

ROOT = Path(__file__).resolve().parent.parent

LOCKED = {   # prompt -> first 16 hex of sha256 of its text
    "check": "b37e41a362596ffa",
    "coder_note": "c0c002d22f0a3e5d",
    "email": "1cc5c99f099b1379",
    "hear_mixed": "434577fb65c3fb53",
    "phone_notification": "3e1759a4c952170d",
    "plan": "2df3b4cc47d251da",
    "propose_checks": "3c4418df3f9c47d5",
    "research": "936cb9d90dab6654",
    "review": "246dc8729bcdca23",
    "see_screen": "71a95e67a3549270",
    "skill_hints": "e804eb5026c84c7a",
    "skill_step": "f97a46c49472fd91",
    "suggest": "0023b58644ccf43f",
    "system": "f3c62be26098a861",     # QA 2026-11-25: "Nova" is her too; no asides / one emoji (exam before + after)
    "talk_english": "3863d31ffffc5129",
    "talk_mixed": "8154b21b957dba82",
    "text_protocol": "c2dc39548e963c9f",
}

FILLED = {   # prompts filled with str.format, and their placeholders (the others are used as they are)
    "check": {"goal", "step", "result", "windows"},
    "email": None, "research": None, "propose_checks": None, "plan": None, "review": None,   # read from the file
    "see_screen": {"question"},
    "skill_hints": {"goal", "listing"},
    "skill_step": {"name", "instruction", "sofar"},
}


def _fields(text):
    return {f for _, f, _, _ in string.Formatter().parse(text) if f is not None}


def test_every_prompt_is_locked():
    assert set(prompts.names()) == set(LOCKED), "a prompt file was added or removed: update LOCKED"


@pytest.mark.parametrize("name", sorted(LOCKED))
def test_prompt_text_is_unchanged(name):
    got = hashlib.sha256(prompts.text(name).encode("utf-8")).hexdigest()[:16]
    assert got == LOCKED[name], f"{name}.md changed: run the task exam before and after, then update LOCKED"


@pytest.mark.parametrize("name", sorted(FILLED))
def test_filled_prompts_render(name):
    fields = _fields(prompts.text(name))
    if FILLED[name] is not None:
        assert fields == FILLED[name]
    prompts.load(name, **{f: "x" for f in fields})        # no stray single brace


def test_line_endings_and_final_newline_dont_matter(tmp_path, monkeypatch):
    (tmp_path / "t.md").write_bytes(b"one\r\ntwo {x}\r\n")
    monkeypatch.setattr(prompts, "HERE", tmp_path)
    prompts.text.cache_clear()
    try:
        assert prompts.load("t", x="!") == "one\ntwo !"
    finally:
        prompts.text.cache_clear()


def test_every_prompt_file_is_used():
    code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "nova").rglob("*.py"))
    for name in LOCKED:
        assert f'load("{name}"' in code, f"{name}.md isn't loaded anywhere"


def test_no_long_prompt_constants_left_in_code():
    """A multi-line string assigned to a *_PROMPT / *_NOTE name belongs in nova/prompts/."""
    for path in (ROOT / "nova").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id.endswith(("PROMPT", "NOTE")):
                lines = node.end_lineno - node.lineno + 1
                assert lines <= 3, f"{path.name}: {node.targets[0].id} is {lines} lines - move it to nova/prompts/"
