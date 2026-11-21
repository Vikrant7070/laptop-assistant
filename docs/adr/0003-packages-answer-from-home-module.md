# 0003 Split packages answer `pkg.<name>` from the name's home module

Status: accepted (Part B, PRs #17 and #18, 2026-11-21)

## Context
`tools.py` (3.3k lines) and `app.py` (4.6k) were split into packages, but hundreds of callers and test patches used
`tools.<name>` / `app.<name>`. Re-exporting with `from .x import *` would make a patch on the package change a copy
that the real code never reads, so tests would pass while testing nothing.

## Decision
- The package's `__init__` answers `pkg.<name>` with a module `__getattr__` that looks the name up in its home
  module on every access (`tools.home_of(name)`, `app.home_of(name)`).
- Inside the package, a name used across modules is written `module.name` and read when the code runs.
- Patches and assignments go to the home module, never the package. The guard tests (`tests/test_tools_package.py`,
  `tests/test_app_package.py`) fail on a patch of the package, on a name that no longer resolves, and on a module
  over 800 lines.

## Consequences
- Callers didn't change; tests had to retarget their patches.
- The same method is used for later splits (`docs/file-split-plan.md`).
