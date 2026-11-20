"""Child brain: a research project that learns how to learn, inside a guard.

Kept apart from Nova until Phase 5: nothing in this package imports `nova`, and Nova doesn't import it.
Plans: docs/child-brain-plan.md (the idea) and docs/child-brain-dev-plan.md (the build steps).

Uses its own Python setup:
    py -3.12 -m venv .venv-childbrain
    .venv-childbrain\\Scripts\\python -m pip install -r requirements-childbrain.txt
"""

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent
RUNS_DIR = PACKAGE / "runs"  # saved brains, logs and results; git ignores it
