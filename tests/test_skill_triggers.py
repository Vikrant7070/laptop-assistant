"""Does the right skill start for the right words - and nothing start for everything else? (tests/skill_triggers.jsonl)

Positives must start their skill; near misses and chit-chat must start NO skill. Both rates matter: a matcher that
never fires would pass the second half, one that always fires the first. Add a line to the file whenever a real
request starts the wrong skill (or no skill) - `python -m nova.skill_eval` also reads the same file.
"""
import json
import shutil
from pathlib import Path

import pytest

from nova.actions import skills

HERE = Path(__file__).resolve().parent
LINES = [json.loads(l) for l in (HERE / "skill_triggers.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.fixture
def shipped():
    skills.DIR.mkdir(parents=True, exist_ok=True)
    for f in (HERE.parent / "skills").glob("*.md"):
        shutil.copy(f, skills.DIR / f.name)


@pytest.mark.parametrize("line", LINES, ids=[f"{l['kind']}: {l['text'][:40]}" for l in LINES])
def test_trigger(shipped, line):
    s, how = skills.pick(line["text"])
    got = s["name"] if s else None
    assert got == line["skill"], f"{line['text']!r} -> {got} ({skills.last_decision})"


def test_rates(shipped):
    wrong = sum(1 for l in LINES if l["skill"] is None and skills.pick(l["text"])[0])
    missed = sum(1 for l in LINES if l["skill"] and (skills.pick(l["text"])[0] or {}).get("name") != l["skill"])
    assert wrong == 0 and missed == 0
