"""The guard's commands.

    python -m childbrain.guard run childbrain.<module>:<function>   start a brain inside the guard
    python -m childbrain.guard stop        OFF SWITCH: stop every guarded brain within a second
    python -m childbrain.guard clear       turn the off switch back off (a person decides)
    python -m childbrain.guard check       policy lock, exam seals and brain code check
    python -m childbrain.guard lock        (people only) re-lock policy.json after reading the change
    python -m childbrain.guard seal        (people only) re-seal the exam games after checking the change
    python -m childbrain.guard log <run folder>   show a run's log and check it wasn't tampered with
    python -m childbrain.guard nightly     exam + keep or roll back + the morning line
    python -m childbrain.guard versions    list saved versions and which one is in use
    python -m childbrain.guard rollback <version>   (a person) go back to an earlier version
"""

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m childbrain.guard")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").add_argument("target")
    for name in ("stop", "clear", "check", "lock", "seal", "nightly", "versions"):
        sub.add_parser(name)
    sub.add_parser("log").add_argument("folder")
    sub.add_parser("rollback").add_argument("version")
    a = p.parse_args(argv)

    if a.cmd == "nightly":
        from .nightly import nightly

        print(nightly()["line"])
        return 0
    if a.cmd == "rollback":
        from .nightly import rollback

        print(rollback(a.version))
        return 0
    if a.cmd == "versions":
        from .versions import VersionStore

        store = VersionStore()
        current = store.current
        for vid in store.ids():
            card = store.card(vid)
            exam = (card.get("exam") or {}).get("total")
            mark = "  <- in use" if vid == current else ""
            print(f"{vid}  {card['status']:<11} exam {exam if exam is not None else '-':<10} {card.get('why', '')}{mark}")
        return 0

    if a.cmd == "stop":
        from .server import off_switch

        print(f"off switch ON ({off_switch()}). Guarded brains stop within a second.")
        return 0
    if a.cmd == "clear":
        from .server import clear_off_switch

        print("off switch cleared" if clear_off_switch() else "off switch was already off")
        return 0
    if a.cmd == "lock":
        from . import policy

        print(f"policy locked: {policy.lock()[:16]}...")
        return 0
    if a.cmd == "seal":
        from . import seals

        for name, prints in seals.seal().items():
            print(f"sealed {name}: code {prints['code'][:12]}..., exam {prints['exam'][:12]}...")
        return 0
    if a.cmd == "check":
        from . import codecheck, policy, seals

        problems = policy.verify() + seals.verify() + codecheck.check()
        print("\n".join(problems) or "policy lock, exam seals and brain code: all OK")
        return 1 if problems else 0
    if a.cmd == "log":
        from .log import check_chain, read

        path = Path(a.folder) / "guard_log.jsonl"
        for e in read(path):
            print(json.dumps({k: v for k, v in e.items() if k != "hash"}))
        problems = check_chain(path)
        print("\n".join(problems) or "log intact")
        return 1 if problems else 0

    from .server import Guard

    summary = Guard().run(a.target)
    print(json.dumps(summary.__dict__, indent=2))
    return 0 if summary.stopped == "brain finished" else 1


if __name__ == "__main__":
    sys.exit(main())
