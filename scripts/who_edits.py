"""Who else is editing these files? A collision check for parallel sessions.

    python scripts/who_edits.py                     # every worktree: branch, ahead/behind master, uncommitted files
    python scripts/who_edits.py nova/actions/tools/ # only the OTHER worktrees changing files there; exit 1 if any

Run it before editing a file (see AGENTS.md). A worktree "changes" a file if the file is uncommitted there, or
committed on its branch but not yet on master (both end in a merge conflict). The worktree you run it from is left
out of the path check: your own edits are not a collision. Paths are relative to the repository root; a folder
matches every file in it.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, field


def git(*args: str, cwd: str | None = None) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


@dataclass
class Worktree:
    path: str
    branch: str = "(detached)"
    head: str = ""
    missing: bool = False
    ahead: int | None = None
    behind: int | None = None
    dirty: list[str] = field(default_factory=list)       # uncommitted
    unmerged: list[str] = field(default_factory=list)    # committed on the branch, not on the base yet


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def list_worktrees(cwd: str | None = None) -> list[Worktree]:
    """Parse `git worktree list --porcelain`."""
    trees, cur = [], None
    for line in git("worktree", "list", "--porcelain", cwd=cwd).splitlines():
        if line.startswith("worktree "):
            cur = Worktree(path=line[len("worktree "):])
            trees.append(cur)
        elif cur is None:
            continue
        elif line.startswith("HEAD "):
            cur.head = line[5:]
        elif line.startswith("branch "):
            cur.branch = line[len("branch "):].removeprefix("refs/heads/")
        elif line == "bare":
            cur.branch = "(bare)"
        elif line.startswith("prunable"):
            cur.missing = True
    for t in trees:
        if not os.path.isdir(t.path):
            t.missing = True
    return trees


def uncommitted(path: str) -> list[str]:
    """Changed, staged and untracked files in one worktree, repo-relative with forward slashes."""
    raw = git("status", "--porcelain=v1", "-z", "--untracked-files=all", cwd=path)
    files, parts, i = [], raw.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        status, name = entry[:2], entry[3:]
        files.append(name)
        if "R" in status or "C" in status:   # a rename/copy is followed by its old name
            if i < len(parts) and parts[i]:
                files.append(parts[i])
            i += 1
    return sorted(set(files))


def base_ref(cwd: str | None = None) -> str:
    for ref in ("origin/master", "master"):
        try:
            git("rev-parse", "--verify", "--quiet", ref, cwd=cwd)
            return ref
        except RuntimeError:
            continue
    return "HEAD"


def fill(t: Worktree, base: str) -> None:
    if t.missing:
        return
    try:
        counts = git("rev-list", "--left-right", "--count", f"{base}...HEAD", cwd=t.path).split()
        t.behind, t.ahead = int(counts[0]), int(counts[1])
    except (RuntimeError, ValueError, IndexError):
        pass
    if t.ahead:
        try:
            t.unmerged = sorted(f for f in git("diff", "--name-only", f"{base}...HEAD", cwd=t.path).splitlines() if f)
        except RuntimeError:
            pass
    try:
        t.dirty = uncommitted(t.path)
    except RuntimeError:
        t.missing = True


def touches(files: list[str], paths: list[str]) -> list[str]:
    """The files that are, or are inside, one of `paths`."""
    wanted = [p.replace("\\", "/").strip("/").removeprefix("./") for p in paths]
    hits = []
    for f in files:
        for w in wanted:
            if not w or f == w or f.startswith(w + "/"):
                hits.append(f)
                break
    return hits


def _list(title: str, files: list[str]) -> list[str]:
    if not files:
        return []
    out = [f"    {title}:"] + [f"      {f}" for f in files[:40]]
    if len(files) > 40:
        out.append(f"      ... and {len(files) - 40} more")
    return out


def describe(t: Worktree, base: str, dirty: list[str] | None = None, unmerged: list[str] | None = None) -> str:
    if t.missing:
        return f"{t.path}\n    [{t.branch}] folder missing (git worktree prune)"
    where = "" if t.ahead is None else f" {t.ahead} ahead / {t.behind} behind {base}"
    lines = [f"{t.path}", f"    [{t.branch}]{where}, {len(t.dirty)} uncommitted, {len(t.unmerged)} unmerged file(s)"]
    lines += _list("uncommitted", t.dirty if dirty is None else dirty)
    lines += _list(f"committed, not on {base}", t.unmerged if unmerged is None else unmerged)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="files or folders, relative to the repository root")
    ap.add_argument("--base", help="branch to count ahead/behind against (default: origin/master)")
    args = ap.parse_args(argv)

    here = _norm(git("rev-parse", "--show-toplevel").strip())
    base = args.base or base_ref()
    trees = list_worktrees()
    for t in trees:
        fill(t, base)

    if not args.paths:
        for t in trees:
            mark = "  (this worktree)" if _norm(t.path) == here else ""
            print(describe(t, base) + mark)
        return 0

    found = 0
    for t in trees:
        if t.missing or _norm(t.path) == here:
            continue
        dirty, unmerged = touches(t.dirty, args.paths), touches(t.unmerged, args.paths)
        if dirty or unmerged:
            found += 1
            print(describe(t, base, dirty, unmerged))
    if found:
        print(f"\n{found} other worktree(s) are changing files there. Stop and ask before editing.")
        return 1
    print("No other worktree is changing files there.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
