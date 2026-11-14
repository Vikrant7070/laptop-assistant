"""Tiny client for the desktop Nova overlay (avatar.py). Every call is optional: if she isn't running, nothing breaks."""
import json
import urllib.request

URL = "http://127.0.0.1:5056"


def send(do, timeout=20, **args):
    try:
        req = urllib.request.Request(URL + "/cmd", data=json.dumps({"do": do, "timeout": timeout, **args}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        return json.loads(urllib.request.urlopen(req, timeout=timeout + 5).read())
    except Exception:
        return None


def position():
    """Where 3D Nova stands right now [x, y], or None if she isn't running."""
    try:
        return json.loads(urllib.request.urlopen(URL + "/", timeout=1).read())["pos"]
    except Exception:
        return None


def running():
    try:
        urllib.request.urlopen(URL + "/", timeout=1)
        return True
    except Exception:
        return False
