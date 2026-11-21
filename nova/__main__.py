"""`python -m nova` starts Nova; `python -m nova --version` prints the version without starting her.

The desktop import is inside main() on purpose: --version (and anything that only wants the package)
must work on a machine where pywebview/pystray are not installed.
"""
import sys

from nova import __version__


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        print(f"Nova {__version__}")
        return 0
    from nova.core.desktop import main as run_desktop
    run_desktop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
