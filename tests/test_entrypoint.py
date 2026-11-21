"""`python -m nova --version` works without the desktop stack, and the version is the packaged one."""
import sys
import tomllib
from pathlib import Path

from nova import __version__
from nova.__main__ import main

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == __version__


def test_version_flag_prints_and_does_not_start_the_app(capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "nova.core.desktop", None)   # importing it now would fail
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"Nova {__version__}"
