"""A tripwire on the brain's code, run before the guard starts a brain.

It reads (never runs) the brain's files and refuses ones that import tools for reaching outside the
sandbox: files, processes, the network, raw memory, the guard itself, or Nova. It is a tripwire, not a
wall: Python has other ways round it, so real isolation (a restricted Windows account) is later work.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .. import PACKAGE

FORBIDDEN_MODULES = {
    "os", "sys", "io", "pathlib", "shutil", "glob", "tempfile", "subprocess", "multiprocessing", "threading",
    "socket", "ssl", "select", "asyncio", "urllib", "http", "requests", "ftplib", "smtplib", "webbrowser",
    "ctypes", "winreg", "_winapi", "msvcrt", "signal", "importlib", "pickle", "marshal", "shelve",
    "builtins", "inspect", "gc", "code", "codeop", "runpy", "nova", "childbrain.guard", "childbrain.runcard",
}
FORBIDDEN_CALLS = {"open", "exec", "eval", "compile", "__import__", "breakpoint", "input", "globals", "vars"}
# Brain-side folders: everything a brain is made of or plays with.
BRAIN_DIRS = ("brain", "learn", "skills", "evolve", "worlds")
# Brain-side files that are allowed their tools, because they ARE the link to the guard (both are tested).
TRUSTED = {"link.py", "brainhost.py"}


def _resolve(module: str | None, level: int, file: Path) -> str:
    """Turn a relative import (from ..x import y) into its full name."""
    if not level:
        return module or ""
    pkg = list(file.relative_to(PACKAGE.parent).with_suffix("").parts[:-1])
    base = pkg[: len(pkg) - (level - 1)] if level > 1 else pkg
    return ".".join(base + ([module] if module else []))


def _bad_module(name: str) -> str | None:
    for bad in FORBIDDEN_MODULES:
        if name == bad or name.startswith(bad + "."):
            return bad
    return None


def check_file(path: Path) -> list[str]:
    path = Path(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        return [f"{path.name}: can't be read ({e.msg})"]
    where = path.name
    problems = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(node.module, node.level, path) if path.is_relative_to(PACKAGE.parent) else node.module or ""
            names = [base] + [f"{base}.{a.name}" for a in node.names]
        for name in names:
            bad = _bad_module(name)
            if bad:
                problems.append(f"{where}:{node.lineno}: imports {bad}")
                break
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            problems.append(f"{where}:{node.lineno}: calls {node.func.id}()")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr not in ("__init__", "__name__"):
            problems.append(f"{where}:{node.lineno}: uses {node.attr}")
    return sorted(set(problems))


def has_brain(path: Path, name: str) -> bool:
    """Does the file define `name` at top level, marked @brain? (Read, not run.)"""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return any(isinstance(d, ast.Name) and d.id == "brain" for d in node.decorator_list)
    return False


def brain_files(target_file: Path | None = None) -> list[Path]:
    files = [p for d in BRAIN_DIRS for p in (PACKAGE / d).rglob("*.py")]
    if target_file is not None:
        files.append(Path(target_file))
    return sorted(set(files))


def check(target_file: Path | None = None) -> list[str]:
    problems = []
    for p in brain_files(target_file):
        if p.name in TRUSTED and p.parent == PACKAGE:
            continue
        problems += check_file(p)
    return problems
