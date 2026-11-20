"""Command line for the child brain.

    python -m childbrain run sanity [--seed 0] [--device cpu|cuda] [--set steps=500 ...]
    python -m childbrain rerun childbrain/runs/<folder>
    python -m childbrain list
"""

import argparse
import json
import sys

from . import RUNS_DIR
from .runcard import CARD_FILE, RESULTS_FILE, rerun, run

EXPERIMENTS = {
    "sanity": "childbrain.measure.sanity:tiny_train",
    "random-play": "childbrain.measure.random_play:random_play",
}


def _parse_set(items: list[str]) -> dict:
    out = {}
    for item in items:
        key, eq, value = item.partition("=")
        if not eq:
            raise SystemExit(f"--set needs key=value, got {item!r}")
        try:
            out[key] = json.loads(value)
        except json.JSONDecodeError:
            out[key] = value
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m childbrain")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run an experiment and save its run card")
    r.add_argument("experiment", help=f"one of {sorted(EXPERIMENTS)} or childbrain.module:function")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--device", default="cpu")
    r.add_argument("--name", default="")
    r.add_argument("--set", nargs="*", default=[], metavar="KEY=VALUE")
    rr = sub.add_parser("rerun", help="run a saved card again and compare the numbers")
    rr.add_argument("folder")
    rr.add_argument("--tolerance", type=float, default=0.0)
    sub.add_parser("list", help="list saved runs")
    sub.add_parser("seal", help="(people only) re-fingerprint the sealed exam games after checking a change")
    sub.add_parser("verify", help="check the sealed exam games against their fingerprints")
    a = p.parse_args(argv)

    if a.cmd in ("seal", "verify"):
        from .worlds import sealed

        if a.cmd == "seal":
            for name, prints in sealed.seal().items():
                print(f"sealed {name}: code {prints['code'][:12]}..., exam {prints['exam'][:12]}...")
            return 0
        problems = sealed.verify()
        print("\n".join(problems) or "sealed games OK")
        return 1 if problems else 0

    if a.cmd == "run":
        folder = run(EXPERIMENTS.get(a.experiment, a.experiment), _parse_set(a.set), a.seed, a.device, a.name)
        results = json.loads((folder / RESULTS_FILE).read_text(encoding="utf-8"))
        print(f"saved {folder}")
        print(json.dumps({k: v for k, v in results.items() if not isinstance(v, list)}, indent=2))
        return 0
    if a.cmd == "rerun":
        report = rerun(a.folder, a.tolerance)
        print(report.summary())
        return 0 if report.same else 1
    for card in sorted(RUNS_DIR.glob(f"*/{CARD_FILE}")):
        done = (card.parent / RESULTS_FILE).exists()
        print(f"{card.parent.name}{'' if done else '  (no results)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
