"""Shared constants and utilities used by multiple Nova modules.

Avoids duplicating definitions across tools.py, avatar.py, desktop.py, knowledge.py and cloud.py.
"""
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).parent
# Where Nova keeps settings, notes, knowledge and keys. NOVA_DATA points it elsewhere (the tests use a temp folder).
DATA = Path(os.environ.get("NOVA_DATA") or ROOT / "data")

# ── Reactions that Nova's 3D body can perform ──
REACTIONS = (
    "laugh", "dance", "spin", "clap", "surprised", "sad", "confused", "nod", "shake",
    "facepalm", "bow", "love", "yawn", "look", "happy", "wave",
)


def work_area():
    """Usable screen rectangle (left, top, right, bottom) excluding the taskbar."""
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


def ps_escape(s):
    """Escape a string for safe interpolation inside a PowerShell single-quoted string.

    PowerShell single-quoted strings treat everything literally except ``'`` (and its typographic
    variants, which PowerShell also accepts as quotes), which must be doubled. This prevents shell
    injection when tool arguments (which come from the LLM) are passed to PowerShell commands.
    Always use it as  f"'{ps_escape(x)}'".
    """
    return "".join(c * 2 if c in "'‘’‚‛" else c for c in str(s))


LOCAL_HOSTS = ("127.0.0.1", "localhost")


def host_allowed(host, port):
    """Is an HTTP Host header one of ours? (Stops DNS-rebinding pages from talking to Nova's local servers.)"""
    host = (host or "").lower()
    return host in {f"{h}:{port}" for h in LOCAL_HOSTS} | (set(LOCAL_HOSTS) if port == 80 else set())
