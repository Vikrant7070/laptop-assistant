"""Run the 3D page's joint check and save its report: every pose of every character, measured on her real model.

    python -m nova.avatar.joint_check          (Nova must be running: it serves the page and the models)

It opens static/nova3d.html?check=1 in a hidden window (the same WebView2 that draws desktop Nova), which plays each
built-in animation, each character's own moves and talking gestures frame by frame, measures every joint against a
human body (elbows, wrists, knees, neck, hands outside her body, hands that should touch her chest/chin/hip really do,
no popping between frames) and POSTs the result to /api/dev/joint_report -> data/joint_report.json.
tests/test_joints.py reads that report (and runs this when the report is older than the poses or models).
"""
import hashlib
import os
import sys
import time
import urllib.request

from nova import store
from nova.shared import ROOT

URL = "http://127.0.0.1:5055/static/nova3d.html?check=1"
REPORT = ROOT / "tests" / "joint_report.json"      # kept with the tests: they read it (no Nova needed when it is current)


def inputs_digest():
    """A fingerprint of what the poses depend on: the 3D page, every character's character.json and model.vrm.
    The report carries the one it was made with; a different one now means it must be made again."""
    from nova.core import characters
    h = hashlib.sha1((ROOT / "static" / "nova3d.html").read_bytes())
    for d in sorted(p for p in characters.ROOT.iterdir() if p.is_dir()):
        for name in ("character.json", "model.vrm"):
            f = d / name
            if not f.is_file():
                continue
            if name == "model.vrm":        # big: its size and both ends are enough to notice a new export
                with open(f, "rb") as fh:
                    head = fh.read(1 << 20)
                    fh.seek(max(0, f.stat().st_size - (1 << 20)))
                    h.update(str(f.stat().st_size).encode() + head + fh.read())
            else:
                h.update(f.read_bytes())
    return h.hexdigest()


def current():
    """Is the saved report made from the page, characters and models as they are now?"""
    import json
    try:
        return json.loads(REPORT.read_text(encoding="utf-8")).get("inputs") == inputs_digest()
    except (OSError, ValueError):
        return False
ARGS = ("--disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows "
        "--disable-features=ElasticOverscroll")


def nova_running():
    try:
        urllib.request.urlopen("http://127.0.0.1:5055/api/dev/character_ids", timeout=3)
        return True
    except Exception:
        return False


def run(timeout=240):
    """-> True when a fresh report arrived."""
    import webview
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = ARGS   # a hidden page would run its timers at 1 fps
    start = time.time()
    before = REPORT.stat().st_mtime if REPORT.exists() else 0      # (the server stamps the fingerprint when it saves)
    done = {"ok": False}
    window = webview.create_window("Nova joint check", URL, width=520, height=700, hidden=True)

    def wait():
        while time.time() - start < timeout:
            if REPORT.exists() and REPORT.stat().st_mtime > before:
                done["ok"] = True
                break
            time.sleep(1)
        window.destroy()

    # its own browser profile: Nova's running windows keep theirs
    webview.start(wait, gui="edgechromium", private_mode=False, storage_path=str(store.current().joint_webview))
    return done["ok"]


def main():
    if not nova_running():
        print("Nova isn't running (http://127.0.0.1:5055) - start her first; the check needs her page and models.")
        return 2
    ok = run()
    print(f"joint report {'saved to ' + str(REPORT) if ok else 'did not arrive (timed out)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
