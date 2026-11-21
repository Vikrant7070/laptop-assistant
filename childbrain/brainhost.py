"""Started by the guard (never by a person) inside the brain's own process:
    python -m childbrain.brainhost childbrain.<module>:<function>
It connects to the guard, loads the brain and hands it the link.
"""

import importlib
import sys

from .link import GuardLink


def main(target: str) -> int:
    module, _, name = target.partition(":")
    if not module.startswith("childbrain.") or module.startswith("childbrain.guard") or not name:
        print(f"not a brain: {target}")
        return 2
    fn = getattr(importlib.import_module(module), name, None)
    if not getattr(fn, "is_brain", False):
        print(f"{target} is not marked @brain")
        return 2
    link = GuardLink()
    try:
        fn(link)
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
