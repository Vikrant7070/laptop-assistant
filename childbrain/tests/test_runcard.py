"""WP 0.2: a saved run card, run again, gives the same numbers."""

import json

import pytest

torch = pytest.importorskip("torch")  # skipped under Nova's Python

from childbrain import ROOT
from childbrain.runcard import CARD_FILE, RESULTS_FILE, RunCard, compare, rerun, resolve, run

SANITY = "childbrain.measure.sanity:tiny_train"
QUICK = {"steps": 60}


def test_card_has_everything_needed_to_repeat(tmp_path):
    folder = run(SANITY, QUICK, seed=7, runs_dir=tmp_path)
    card = json.loads((folder / CARD_FILE).read_text(encoding="utf-8"))
    assert card["experiment"] == SANITY
    assert card["seed"] == 7
    assert card["device"] == "cpu"
    assert card["settings"]["steps"] == 60 and card["settings"]["width"] == 32  # defaults kept too
    assert len(card["code"]["fingerprint"]) == 64
    assert card["env"]["torch"] == torch.__version__
    assert (folder / RESULTS_FILE).exists()


def test_rerun_gives_same_numbers_cpu(tmp_path):
    folder = run(SANITY, QUICK, seed=3, runs_dir=tmp_path)
    report = rerun(folder)
    print(report.summary())
    assert report.same, report.differences
    assert not report.code_changed
    saved = json.loads((folder / RESULTS_FILE).read_text(encoding="utf-8"))
    assert report.results == saved  # exactly equal, not just close


@pytest.mark.skipif(not torch.cuda.is_available(), reason="no GPU")
def test_rerun_gives_same_numbers_gpu(tmp_path):
    folder = run(SANITY, QUICK, seed=3, device="cuda", runs_dir=tmp_path)
    assert RunCard.load(folder).env["gpu"]
    report = rerun(folder)
    print(report.summary())
    assert report.same, report.differences
    torch.cuda.empty_cache()


def test_seed_matters(tmp_path):
    a = json.loads((run(SANITY, QUICK, seed=1, runs_dir=tmp_path) / RESULTS_FILE).read_text())
    b = json.loads((run(SANITY, QUICK, seed=2, runs_dir=tmp_path) / RESULTS_FILE).read_text())
    assert compare(a, b), "different seeds should give different numbers"


def test_rerun_notices_changed_numbers(tmp_path):
    folder = run(SANITY, QUICK, seed=0, runs_dir=tmp_path)
    results = json.loads((folder / RESULTS_FILE).read_text())
    results["final_loss"] += 1e-9
    (folder / RESULTS_FILE).write_text(json.dumps(results))
    report = rerun(folder)
    assert not report.same
    assert any("final_loss" in d for d in report.differences)


def test_unknown_setting_is_refused():
    with pytest.raises(ValueError, match="unknown settings"):
        RunCard.new(SANITY, {"stepz": 10})


@pytest.mark.parametrize("path", ["os:system", "nova.core.app:main", "childbrain.runcard:run", "childbrain"])
def test_card_can_only_run_marked_childbrain_experiments(path):
    with pytest.raises((ValueError, ImportError)):
        resolve(path)


def test_runs_folder_is_ignored_by_git():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "childbrain/runs/" in gitignore
    assert ".venv-childbrain/" in gitignore


def test_childbrain_never_imports_nova():
    for py in (ROOT / "childbrain").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("import nova", "from nova")), f"{py} imports Nova: {line}"
