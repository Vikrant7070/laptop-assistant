"""The nightly cycle, steps 2-4 of the plan (sleep, step 1, comes in WP 2.2):

  1. Check the version in use: are its files unchanged, does it still pass the exam, and with exactly the
     same score as when it was kept? (The exam is exactly repeatable, so any change means damage.)
     If not, ROLL BACK to the newest earlier kept version that is still fine.
  2. Examine each new candidate. Keep it only if it broke no rules and its exam is no worse than the
     version in use (within a small tolerance). Otherwise throw it away; nothing changes.
  3. A night with a rollback or a thrown-away version counts as a failed night. Two failed nights in a row
     drop the trust level by one (noted; level 0 can't go lower).
  4. Write one line for the morning, and log every decision (hash-chained).

Run by the guard, never by the brain:  python -m childbrain.guard nightly
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import policy, seals
from .exam import run_exam
from .log import ChainLog, check_chain
from .versions import VersionStore

RULE_BREAKS = {"too many blocked actions", "request too large or broken pipe"}


def tolerance(total: float) -> float:
    return max(0.01, 0.02 * abs(total))


def broken_rules(source: str) -> str | None:
    """Did the guarded run that made this version break any rule?"""
    if not source:
        return None
    folder = Path(source)
    summary_file = folder / "guard_summary.json"
    if not summary_file.exists():
        return "the run that made it has no guard summary"
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    if summary.get("stopped") in RULE_BREAKS:
        return f"the run that made it broke a guard rule ({summary['stopped']})"
    if check_chain(folder / "guard_log.jsonl"):
        return "the run that made it has a tampered log"
    return None


def nightly(store: VersionStore | None = None, exam=run_exam) -> dict:
    store = store or VersionStore()
    log = ChainLog(store.root / "nightly_log.jsonl")
    night = time.strftime("%Y-%m-%d %H:%M")
    problems = policy.verify() + seals.verify()
    if problems:
        log.write(who="guard", event="nightly refused", problems=problems)
        return _report(store, night, {"refused": problems})

    state = store.state()
    done = {"examined": [], "kept": [], "thrown_away": [], "rolled_back": None, "notes": []}
    current = state["current"]
    current_exam = None

    # 1. the version in use
    if current:
        reason, current_exam = _health(store, current, exam)
        if reason:
            store.update_card(current, status="damaged", damage=reason)
            log.write(who="guard", event="version in use is damaged", version=current, reason=reason)
            back, current_exam = None, None
            for older in reversed([v for v in state["kept"] if v != current]):
                why, result = _health(store, older, exam)
                if why is None:
                    back, current_exam = older, result
                    break
            state["current"] = back
            done["rolled_back"] = {"from": current, "to": back, "why": reason}
            log.write(who="guard", event="rolled back", frm=current, to=back, why=reason)
            current = back

    # 2. new candidates
    for vid in store.candidates():
        done["examined"].append(vid)
        card = store.card(vid)
        result, why = None, None
        if not store.intact(vid):
            why = "its files changed after it was submitted"
        else:
            why = broken_rules(card.get("source", ""))
        if why is None:
            result = exam(store.path(vid))
            if result["broken"]:
                why = f"it fails the exam: {result['broken']}"
            elif current_exam and result["total"] < current_exam["total"] - tolerance(current_exam["total"]):
                why = f"its exam score {result['total']:.3f} is worse than the version in use ({current_exam['total']:.3f})"
        if why:
            store.update_card(vid, status="thrown away", why=why, exam=result, decided=night)
            done["thrown_away"].append({"version": vid, "why": why})
            log.write(who="guard", event="thrown away", version=vid, why=why, exam=result and result.get("total"))
        else:
            store.update_card(vid, status="kept", exam=result, decided=night)
            state["current"], current, current_exam = vid, vid, result
            state["kept"].append(vid)
            done["kept"].append(vid)
            log.write(who="guard", event="kept", version=vid, exam=result["total"])

    # 3. failed nights and trust level
    failed = bool(done["rolled_back"] or done["thrown_away"])
    state["fails_in_a_row"] = state.get("fails_in_a_row", 0) + 1 if failed else 0
    if state["fails_in_a_row"] >= 2:
        state["level_drops"] = state.get("level_drops", 0) + 1
        state["fails_in_a_row"] = 0
        done["notes"].append("failed two nights in a row: trust level drops by one (already at the lowest, 0)")
        log.write(who="guard", event="trust level drop", level=policy.load()["level"])
    state["last_night"] = night
    store.save_state(state)
    done["current"] = state["current"]
    return _report(store, night, done)


def _health(store: VersionStore, vid: str, exam) -> tuple[str | None, dict | None]:
    """(what's wrong with this version or None, its exam result)."""
    if not store.intact(vid):
        return "its files changed after it was saved", None
    result = exam(store.path(vid))
    if result["broken"]:
        return f"it fails the exam: {result['broken']}", result
    recorded = store.card(vid).get("exam") or {}
    if recorded.get("suite") == result["suite"] and recorded.get("total") != result["total"]:
        return f"its exam score changed ({recorded.get('total')} -> {result['total']})", result
    return None, result


def _report(store: VersionStore, night: str, done: dict) -> dict:
    if "refused" in done:
        line = f"{night}: no exam tonight, the guard's checks failed: {'; '.join(done['refused'])}"
    else:
        rb = done["rolled_back"]
        rolled = "no" if not rb else f"yes ({rb['from']} -> {rb['to'] or 'nothing left'})"
        kept = ", ".join(done["kept"]) or "none"
        thrown = ", ".join(t["version"] for t in done["thrown_away"]) or "none"
        line = (f"{night}: examined {len(done['examined'])} new, kept {kept}, threw away {thrown}, "
                f"rolled back: {rolled}, in use: {done['current'] or 'none'}")
        if done["notes"]:
            line += ". " + " ".join(done["notes"])
    with (store.root / "report.txt").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return {**done, "line": line}


def rollback(vid: str, store: VersionStore | None = None) -> str:
    """A person chooses a version to go back to."""
    store = store or VersionStore()
    if vid not in store.ids() or not store.intact(vid):
        raise ValueError(f"version {vid} doesn't exist or its files changed")
    state = store.state()
    old, state["current"] = state["current"], vid
    if vid not in state["kept"]:
        state["kept"].append(vid)
    store.save_state(state)
    ChainLog(store.root / "nightly_log.jsonl").write(who="person", event="rolled back", frm=old, to=vid)
    return f"in use: {vid} (was {old})"
