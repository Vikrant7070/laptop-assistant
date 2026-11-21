"""Split one big module into a package, moving whole statements and keeping every line's text.

    python scripts/split_module.py nova/audio/voice.py --sections scripts/splits/voice.toml [--check]

The method Part B used for tools/ and app/ (docs/file-split-plan.md, docs/adr/0003), made general:

- Every top-level statement moves whole, with the comments and blank lines above it, to the module the section map
  gives it. Only names change: a name used in a module other than its home is written `home.name`, read when the code
  runs, so a patch on the home module reaches every caller. A `global X` for a name that now lives elsewhere becomes
  `home.X = ...`.
- Each module gets the original imports it uses. A sibling it needs while it loads (a base class, a decorator, a
  module-level call, a default value) is imported at the top; the rest at the bottom, so there are no import cycles.
- `__init__.py` answers `pkg.<name>` from the home module on every access (`__getattr__`) and has `home_of(name)`.
- The script refuses, rather than guess, when: a name is defined in two modules, a module name would be shadowed by
  a local variable or a global where it's needed (`text._clean(text)`), or load-time imports form a cycle.

Section map (TOML):

    package = "nova.audio.voice"
    doc = "First line of the package docstring."  # optional; the original docstring follows
    [ranges]                                       # statements starting at/after this line -> module
    "1" = "speak"
    "576" = "windows_ears"
    [names]                                        # top-level name -> module (wins over ranges)
    _clean_for_speech = "wording"
    [docs]                                         # module -> its docstring
    speak = "Speaking: ..."

--check parses, plans and reports without writing anything.
"""
from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

# ------------------------------------------------------------------ names a statement binds, and scopes


def bound_at_top(stmt):
    """Names a top-level statement binds in the module namespace."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    names = set()

    def visit(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            return
        if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        for child in ast.iter_child_nodes(node):
            visit(child)
    visit(stmt)
    return names


def _locals_of(fn):
    """Names local to a function (params + stores), minus those declared global/nonlocal."""
    local, declared = set(), set()
    args = fn.args
    for a in args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]:
        if a is not None:
            local.add(a.arg)
    body = fn.body if isinstance(fn.body, list) else [fn.body]

    def visit(node):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local.add(node.name)
            for d in getattr(node, "decorator_list", []):
                visit(d)
            return                               # its body is its own scope
        elif isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            local.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                local.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            local.add(node.name)
        for child in ast.iter_child_nodes(node):
            visit(child)
    for stmt in body:
        visit(stmt)
    return local - declared, declared


def _comp_locals(node):
    out = set()
    for gen in node.generators:
        for n in ast.walk(gen.target):
            if isinstance(n, ast.Name):
                out.add(n.id)
    return out


class Refs:
    """Every use of a module-level name in one statement: (node, kind, at_load_time, scope_locals)."""

    def __init__(self, stmt):
        self.uses = []          # (ast.Name, at_load_time, frozenset of every local name visible there)
        self.globals = []       # ast.Global nodes, with their function's locals
        self._visit(stmt, scopes=[], load=True, in_class=False)

    def _visit(self, node, scopes, load, in_class):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for d in getattr(node, "decorator_list", []):
                self._visit(d, scopes, load, in_class)
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                self._visit(default, scopes, load, in_class)
            for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                if a.annotation is not None:
                    self._visit(a.annotation, scopes, load, in_class)
            if getattr(node, "returns", None) is not None:
                self._visit(node.returns, scopes, load, in_class)
            local, _declared = _locals_of(node)
            inner = [s for s in scopes if s[0] != "class"] + [("func", local)]
            for stmt in (node.body if isinstance(node.body, list) else [node.body]):
                self._visit(stmt, inner, False, False)
            return
        if isinstance(node, ast.ClassDef):
            for b in node.bases + [k.value for k in node.keywords] + node.decorator_list:
                self._visit(b, scopes, load, in_class)
            local = set()
            for stmt in node.body:
                if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    local |= {n.id for n in ast.walk(stmt) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
                else:
                    local.add(stmt.name)
            for stmt in node.body:
                self._visit(stmt, scopes + [("class", local)], load, True)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            inner = scopes + [("comp", _comp_locals(node))]
            for child in ast.iter_child_nodes(node):
                self._visit(child, inner, load, in_class)
            return
        if isinstance(node, ast.Global):
            self.globals.append(node)
        if isinstance(node, ast.Name):
            visible = frozenset().union(*[s[1] for s in scopes]) if scopes else frozenset()
            self.uses.append((node, load, visible))
        for child in ast.iter_child_nodes(node):
            self._visit(child, scopes, load, in_class)


# ------------------------------------------------------------------ text edits (ast columns are UTF-8 bytes)


def _apply(lines, edits):
    """edits: (lineno, col_start, col_end, text) with byte columns; applied right to left on each line."""
    by_line = defaultdict(list)
    for e in edits:
        by_line[e[0]].append(e)
    out = list(lines)
    for ln, es in by_line.items():
        b = out[ln - 1].encode("utf-8")
        for _, start, end, text in sorted(es, key=lambda e: -e[1]):
            b = b[:start] + text.encode("utf-8") + b[end:]
        out[ln - 1] = b.decode("utf-8")
    return out


# ------------------------------------------------------------------ the split


DROP = "# (split_module: drop this line)"


class SplitError(Exception):
    pass


def plan(source_path, cfg):
    src = Path(source_path).read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)
    body = list(tree.body)
    doc = ast.get_docstring(tree, clean=False) if body and isinstance(body[0], ast.Expr) and isinstance(
        getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str) else None
    if doc is not None:
        doc_stmt = body.pop(0)
    imports = [s for s in body if isinstance(s, (ast.Import, ast.ImportFrom))]
    stmts = [s for s in body if not isinstance(s, (ast.Import, ast.ImportFrom))]

    ranges = sorted((int(k), v) for k, v in (cfg.get("ranges") or {}).items())
    names_map = cfg.get("names") or {}
    if not ranges:
        raise SplitError("the section map needs at least one [ranges] entry")

    def by_range(line):
        mod = None
        for start, m in ranges:
            if line >= start:
                mod = m
        if mod is None:
            raise SplitError(f"line {line} is before the first range")
        return mod

    # Each statement's chunk: from just after the previous top-level statement (comments/blank lines above it).
    prev_end = doc_stmt.end_lineno if doc is not None else 0
    chunks = []                                   # (stmt, module, first_line, names)
    everything = sorted(body, key=lambda s: s.lineno)
    ends = {}
    for s in everything:
        ends[id(s)] = prev_end
        prev_end = s.end_lineno
    for s in stmts:
        names = bound_at_top(s)
        owners = {names_map[n] for n in names if n in names_map}
        if len(owners) > 1:
            raise SplitError(f"line {s.lineno}: names {sorted(names)} are mapped to different modules {sorted(owners)}")
        mod = owners.pop() if owners else by_range(s.lineno)
        first = ends[id(s)] + 1
        while first < s.lineno and not lines[first - 1].strip():
            first += 1
        chunks.append((s, mod, first, names))

    home = {}
    for s, mod, _, names in chunks:
        for n in names:
            if n in home and home[n] != mod:
                raise SplitError(f"{n!r} is defined in both {home[n]!r} and {mod!r} (line {s.lineno}): map both to one")
            home[n] = mod
    modules = list(dict.fromkeys(m for _, m, _, _ in chunks))
    clash = set(modules) & set(home)
    if clash:
        raise SplitError(f"module names {sorted(clash)} are also top-level names; pick other module names")

    # Rewrite names, and record which siblings each module needs (at load time or later).
    out = {m: [] for m in modules}
    load_deps, run_deps = defaultdict(set), defaultdict(set)
    used_names = defaultdict(set)
    for s, mod, first, _ in chunks:
        refs = Refs(s)
        edits = []
        for g in refs.globals:
            moved = [n for n in g.names if home.get(n) not in (None, mod)]
            if moved:
                if g.lineno != g.end_lineno:
                    raise SplitError(f"line {g.lineno}: a multi-line global statement")
                keep = [n for n in g.names if n not in moved]
                new = ("global " + ", ".join(keep)) if keep else DROP      # nothing left: the line goes
                edits.append((g.lineno, g.col_offset, g.end_col_offset, new))
        for node, at_load, visible in refs.uses:
            used_names[mod].add(node.id)
            target = home.get(node.id)
            if target is None or target == mod or node.id in visible:
                continue                          # defined here, a builtin/import, or a local of the same name
            # Left: a read of another module's name, or a store to it under `global` (globals aren't in `visible`).
            if target in visible:
                raise SplitError(f"line {node.lineno}: `{target}.{node.id}` would be shadowed by a local named "
                                 f"{target!r} in module {mod!r}; rename that module in the section map")
            edits.append((node.lineno, node.col_offset, node.end_col_offset, f"{target}.{node.id}"))
            (load_deps if at_load else run_deps)[mod].add(target)
        out[mod].append([ln for ln in _apply(lines, edits)[first - 1:s.end_lineno] if ln.strip() != DROP])

    # A sibling module's name must not also be a global (or import) of the module that refers to it.
    imported = set().union(*(bound_at_top(i) for i in imports)) if imports else set()
    for m in modules:
        for dep in load_deps[m] | run_deps[m]:
            if home.get(dep) == m or (dep in imported and dep in used_names[m]):
                raise SplitError(f"module {m!r} already uses the name {dep!r}; rename module {dep!r} in the map")

    order = _topo(modules, load_deps)
    return {"doc": doc, "imports": imports, "lines": lines, "modules": modules, "order": order, "out": out,
            "home": home, "load_deps": load_deps, "run_deps": run_deps, "used": used_names}


def _topo(modules, deps):
    order, state = [], {}

    def visit(m, path):
        if state.get(m) == "done":
            return
        if state.get(m) == "busy":
            raise SplitError("load-time import cycle: " + " -> ".join(path + [m]))
        state[m] = "busy"
        for d in sorted(deps.get(m, ())):
            visit(d, path + [m])
        state[m] = "done"
        order.append(m)
    for m in modules:
        visit(m, [])
    return order


def _imports_for(p, mod):
    """The original import statements a module uses, trimmed to the names it uses."""
    used, lines, out = p["used"][mod], p["lines"], []
    for imp in p["imports"]:
        keep = [a for a in imp.names if (a.asname or a.name).split(".")[0] in used or
                (isinstance(imp, ast.ImportFrom) and imp.module == "__future__")]
        if not keep:
            continue
        if len(keep) == len(imp.names):
            out.append("\n".join(lines[imp.lineno - 1:imp.end_lineno]))
        elif isinstance(imp, ast.ImportFrom):
            names = ", ".join(a.name + (f" as {a.asname}" if a.asname else "") for a in keep)
            out.append(f"from {'.' * imp.level}{imp.module or ''} import {names}")
        else:
            out.append("import " + ", ".join(a.name + (f" as {a.asname}" if a.asname else "") for a in keep))
    return out


def render(p, cfg):
    pkg = cfg["package"]
    docs = cfg.get("docs") or {}
    files = {}
    for m in p["modules"]:
        top = sorted(p["load_deps"][m])
        bottom = sorted(p["run_deps"][m] - p["load_deps"][m])
        parts = [f'"""{docs.get(m, f"Part of {pkg} (split from one module; see __init__).")}"""']
        imps = _imports_for(p, m)
        if top:
            imps.append(f"from {pkg} import {', '.join(top)}")
        if imps:
            parts.append("\n".join(imps))
        text = "\n".join(parts) + "\n\n\n" + "\n\n\n".join("\n".join(chunk) for chunk in p["out"][m]) + "\n"
        if bottom:
            text += f"\n\nfrom {pkg} import {', '.join(bottom)}  # noqa: E402  (used when the code runs; no cycles)\n"
        files[f"{m}.py"] = text
    names = defaultdict(list)
    for n, m in p["home"].items():
        names[m].append(n)
    home_lines = "\n".join(f"    {m!r}: {sorted(names[m])!r}," for m in p["order"])
    head = cfg.get("doc", "")
    doc = ((head + "\n\n") if head else "") + (p["doc"] or "").strip("\n")
    files["__init__.py"] = f'''"""{doc}

This was one module; it is now a package, one module per job (scripts/split_module.py). `{pkg.rsplit(".", 1)[-1]}.<name>`
still works: it is read from the name's home module on every access. Patch or assign a name in its home module
(`home_of(name)`), never on the package - tests/test_packages.py checks that.
"""
# Import order: each module finds what it needs at import time already there.
from {pkg} import {", ".join(p["order"])}  # noqa: F401

_MODULES = {{m.__name__.rsplit(".", 1)[1]: m for m in ({", ".join(p["order"])},)}}
_HOME = {{name: _MODULES[mod] for mod, names in {{
{home_lines}
}}.items() for name in names}}


def __getattr__(name):
    """{pkg.rsplit(".", 1)[-1]}.<name> -> the current value in its home module (so a patch there is seen by every caller)."""
    mod = _HOME.get(name)
    if mod is None:
        for m in _MODULES.values():          # a module one of them imports (avatar_client, ...)
            if name in vars(m):
                return vars(m)[name]
        raise AttributeError(f"module {pkg!r} has no attribute {{name!r}}")
    return getattr(mod, name)


def home_of(name):
    """The module that owns `name`, or None."""
    return _HOME.get(name)
'''
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description="Split a module into a package (see the docstring).")
    ap.add_argument("source")
    ap.add_argument("--sections", required=True, help="TOML section map")
    ap.add_argument("--check", action="store_true", help="plan and report only; write nothing")
    args = ap.parse_args(argv)
    src = Path(args.source)
    cfg = tomllib.loads(Path(args.sections).read_text(encoding="utf-8"))
    try:
        p = plan(src, cfg)
    except SplitError as e:
        print(f"split_module: {e}", file=sys.stderr)
        return 1
    files = render(p, cfg)
    for name, text in files.items():
        compile(text, name, "exec")
        print(f"{name:<22} {len(text.splitlines()):>5} lines")
    print("import order:", " -> ".join(p["order"]))
    if args.check:
        return 0
    out = src.with_suffix("")
    out.mkdir(exist_ok=False)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8", newline="\n")
    src.unlink()
    print(f"wrote {out}/ and removed {src}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
