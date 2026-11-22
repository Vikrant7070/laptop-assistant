"""How well do Nova's skills start on the right words? Run it on the laptop, with Nova closed:

    python -m nova.skill_eval

It loads the real embedding model (the tests only use the simple word-overlap fallback), replays every line of
tests/skill_triggers.jsonl plus the requests the skills denied for you (skills/.stats), and prints

  * false triggers  (a skill started that shouldn't have)  and  missed triggers (the right one didn't start),
  * the same for a range of fuzzy cut-offs, and the lowest cut-off with no false trigger - put that in
    skills.FUZZY_FLOOR (and tell me if the margin needs to move too),
  * the last 40 decisions Nova made on real requests (the "SKILL MATCH / SKILL NO MATCH" log events in data/nova.db).

Add a line to tests/skill_triggers.jsonl for every request that started the wrong skill.
"""
import json
import re
import sys
from pathlib import Path

from nova import db

ROOT = Path(__file__).resolve().parent.parent


def load_lines():
    out = []
    f = ROOT / "tests" / "skill_triggers.jsonl"
    for l in f.read_text(encoding="utf-8").splitlines():
        if l.strip():
            out.append(json.loads(l))
    return out


def rates(skills, lines):
    wrong = [(l["text"], skills.pick(l["text"])[0]["name"]) for l in lines
             if l["skill"] is None and skills.pick(l["text"])[0]]
    missed = [(l["text"], l["skill"]) for l in lines
              if l["skill"] and (skills.pick(l["text"])[0] or {}).get("name") != l["skill"]]
    return wrong, missed


def main():
    from nova.actions import skills
    from nova.brain import retrieval
    retrieval.load()
    print(f"matching with: {retrieval.status()}  (floor {skills.floor():.2f}, margin {skills.FUZZY_MARGIN})")
    lines = load_lines()
    for s in skills.load():                                  # requests the user said "no" to count as negatives
        for text in skills.stats_get(s["name"]).get("learned_not_for", []):
            lines.append({"skill": None, "text": text, "kind": f"denied for {s['name']}"})
    wrong, missed = rates(skills, lines)
    print(f"\n{len(lines)} lines: {len(wrong)} false triggers, {len(missed)} missed triggers")
    for text, name in wrong:
        print(f"  FALSE  {text!r} started {name}   [{skills.pick(text) and skills.last_decision}]")
    for text, name in missed:
        print(f"  MISSED {text!r} should start {name}   [{skills.last_decision}]")
    print("\nfuzzy floor sweep (false / missed):")
    keep = skills.matching.FUZZY_FLOOR, skills.matching.FUZZY_FLOOR_WORDS
    best = None
    for f in [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9]:
        skills.matching.FUZZY_FLOOR = skills.matching.FUZZY_FLOOR_WORDS = f
        w, m = rates(skills, lines)
        print(f"  {f:.2f}: {len(w)} / {len(m)}")
        if not w and best is None:
            best = f
    skills.matching.FUZZY_FLOOR, skills.matching.FUZZY_FLOOR_WORDS = keep
    print(f"\nlowest floor with no false trigger: {best if best is not None else 'none in range'}")
    try:
        recent = [e["text"] for e in db.find_events(kind="log", like="SKILL ", limit=5000)
                  if re.search(r"SKILL (NO )?MATCH|SKILL DENIED", e["text"] or "")]
    except Exception as e:
        print(f"\n(no request history: {e})")
        return
    print(f"\nlast decisions on real requests ({len(recent)} in the log):")
    for line in recent[-40:]:
        print("  " + line[:200])


if __name__ == "__main__":
    sys.exit(main())
