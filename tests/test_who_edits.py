"""scripts/who_edits.py on a throwaway repository with two extra worktrees."""
import importlib.util
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("who_edits", ROOT / "scripts" / "who_edits.py")
who_edits = importlib.util.module_from_spec(spec)
sys.modules["who_edits"] = who_edits
spec.loader.exec_module(who_edits)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def run(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    run("init", "-q", "-b", "master", cwd=main)
    (main / "nova").mkdir()
    (main / "nova" / "a.py").write_text("a = 1\n")
    (main / "nova" / "b.py").write_text("b = 1\n")
    (main / "README.md").write_text("hi\n")
    run("add", ".", cwd=main)
    run("commit", "-q", "-m", "first", cwd=main)
    dirty, ahead = tmp_path / "wt-dirty", tmp_path / "wt-ahead"
    run("worktree", "add", "-q", "-b", "feat/dirty", str(dirty), cwd=main)
    run("worktree", "add", "-q", "-b", "feat/ahead", str(ahead), cwd=main)
    (dirty / "nova" / "a.py").write_text("a = 2\n")          # uncommitted edit
    (dirty / "nova" / "new.py").write_text("x = 1\n")        # untracked file
    (ahead / "README.md").write_text("changed\n")            # committed, not merged
    run("commit", "-q", "-am", "readme", cwd=ahead)
    return main, dirty, ahead


def test_lists_every_worktree(repo, monkeypatch, capsys):
    main, dirty, ahead = repo
    monkeypatch.chdir(main)
    assert who_edits.main([]) == 0
    out = capsys.readouterr().out
    assert "feat/dirty" in out and "feat/ahead" in out and "[master]" in out
    assert "nova/a.py" in out and "nova/new.py" in out
    assert "1 ahead / 0 behind master" in out


def test_path_hit_on_uncommitted_file_exits_1(repo, monkeypatch, capsys):
    main, *_ = repo
    monkeypatch.chdir(main)
    assert who_edits.main(["nova/"]) == 1
    out = capsys.readouterr().out
    assert "feat/dirty" in out and "feat/ahead" not in out
    assert "nova/a.py" in out and "nova/b.py" not in out


def test_path_hit_on_committed_unmerged_file(repo, monkeypatch, capsys):
    main, *_ = repo
    monkeypatch.chdir(main)
    assert who_edits.main(["README.md"]) == 1
    out = capsys.readouterr().out
    assert "feat/ahead" in out and "committed, not on master" in out


def test_no_hit_exits_0(repo, monkeypatch, capsys):
    main, *_ = repo
    monkeypatch.chdir(main)
    assert who_edits.main(["nova/b.py"]) == 0
    assert "No other worktree" in capsys.readouterr().out


def test_own_worktree_is_not_a_collision(repo, monkeypatch, capsys):
    _, dirty, _ = repo
    monkeypatch.chdir(dirty)
    assert who_edits.main(["nova\\a.py"]) == 0   # backslashes work too


def test_touches_matches_folders_not_prefixes():
    files = ["nova/actions/tools/x.py", "nova/actions/toolsmith.py"]
    assert who_edits.touches(files, ["nova/actions/tools/"]) == ["nova/actions/tools/x.py"]
    assert who_edits.touches(files, ["./nova/actions/toolsmith.py"]) == ["nova/actions/toolsmith.py"]
