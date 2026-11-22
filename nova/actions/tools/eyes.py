"""Nova's eyes: the camera and the screen."""


import re
import time

from nova import platform
from nova import store
from nova.actions.registry import tool
from nova.actions.tools import common, forms, system


LAST_IMAGE = {"jpg_b64": None}  # shown in the chat so the user always sees what Nova looked at


def _setting(name, default=None):
    return common._load(store.current().settings, {}).get(name, default)


def _camera_allowed():
    return bool(_setting("camera"))


def _camera_users():
    """Other apps holding the webcam right now (Windows records this in its privacy settings)."""
    import winreg
    users = []
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"

    def scan(path):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    sub = winreg.EnumKey(k, i)
                    if sub == "NonPackaged":
                        scan(path + "\\" + sub)
                        continue
                    try:
                        with winreg.OpenKey(k, sub) as s:
                            if winreg.QueryValueEx(s, "LastUsedTimeStop")[0] == 0:
                                users.append(sub)
                    except OSError:
                        pass
        except OSError:
            pass
    scan(base)
    nice = []
    for u in users:
        name = u.replace("#", "\\").split("\\")[-1]
        if "python" in name.lower():
            continue  # that's Nova herself
        name = re.sub(r"_[a-z0-9]{13}$", "", name)          # store-app suffix
        name = name.split(".")[-1] if "." in name and not name.endswith(".exe") else name
        nice.append(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).removesuffix(".exe"))
    return nice


def _grab_camera_jpeg(max_width=768):
    import base64
    import cv2
    busy = _camera_users()
    if busy:
        raise RuntimeError(f"The camera is being used by another app ({', '.join(busy)}). "
                           "Windows lets only one app use it at a time - close that app and ask me again.")
    from nova.actions import presence
    with presence._cam:                     # the 👋 presence check uses the camera too: one at a time
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened():
            raise RuntimeError("I can't open the camera - another app may be using it, or it's switched off.")
        ok, frame = False, None
        for _ in range(10):  # let the camera adjust its exposure for a moment
            ok, frame = cap.read()
        cap.release()
    if not ok or frame is None:
        raise RuntimeError("The camera didn't give me a picture.")
    if frame.mean() < 8:
        raise RuntimeError("The camera picture is completely black - the lens may be covered, the privacy shutter "
                           "closed, or another app is holding the camera.")
    h, w = frame.shape[:2]
    if w > max_width:
        frame = cv2.resize(frame, (max_width, int(h * max_width / w)))
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return base64.b64encode(buf.tobytes()).decode()


@tool("Look at the user through the webcam and answer a question about what you see "
      "(e.g. 'how do I look?', 'what am I holding?', 'do I look tired?'). Only works if the user switched the camera on.",
      friendly="Looked through the camera", hints="how do I look, what am I holding, see me", props={"question": common.S},
      required=["question"])
def look_through_camera(question="What do you see?"):
    if not _camera_allowed():
        return "The camera is switched off. The user can allow it in Nova's settings (⚙ Model → Camera)."
    from nova.avatar import avatar_client
    from nova.brain import llm
    avatar_client.send("say", text="Let me take a look 👀", secs=3, timeout=2)
    img = _grab_camera_jpeg()  # kept in memory only, never saved to disk
    LAST_IMAGE["jpg_b64"] = img
    model = _setting("model")
    return "Through the camera: " + llm.see(img, question, preferred=model)


SCREEN_HOOKS = {"hide": None, "show": None}  # desktop.py hides Nova's chat window while she looks


def _screen_allowed():
    return bool(_setting("screen"))


def _grab_screen_jpeg(what="screen", window="", max_width=1400):
    import base64
    import io
    from PIL import ImageGrab
    from nova.avatar import avatar_client
    bbox = None
    if what == "window":
        w = forms._target_window(window)
        if not w:
            raise RuntimeError(f"No open window matches '{window}'.")
        left, top, right, bottom = platform.current().window_rect(w[0])
        bbox = (max(left, 0), max(top, 0), right, bottom)
    # Step out of the way so the picture shows the user's stuff, not Nova.
    avatar_client.send("duck", timeout=2)
    if SCREEN_HOOKS["hide"]:
        SCREEN_HOOKS["hide"]()
    time.sleep(0.45)
    try:
        img = ImageGrab.grab(bbox=bbox, all_screens=bbox is None)
    finally:
        if SCREEN_HOOKS["show"]:
            SCREEN_HOOKS["show"]()
        avatar_client.send("unduck", timeout=2)
    if img.width > max_width:
        img = img.resize((max_width, int(img.height * max_width / img.width)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


@tool("Look at the user's screen (or one window) and answer a question about it - read an error, "
      "explain what's open, summarize a page, find something. Only works if the user switched screen viewing on.",
      friendly="Looked at your screen", hints="what's on my screen, read this error, look at this",
      props={"question": common.S, "what": {"type": "string", "enum": ["screen", "window"]}, "window": common.S},
      required=["question"])
def look_at_screen(question="What's on my screen?", what="screen", window=""):
    if not _screen_allowed():
        return "Screen viewing is switched off. The user can allow it in Nova's settings (⚙ Model → Screen)."
    from nova.brain import llm
    img = _grab_screen_jpeg(what, window)  # kept in memory only, never saved to disk
    LAST_IMAGE["jpg_b64"] = img
    model = _setting("model")
    return "On the screen: " + llm.see(img, question, preferred=model, kind="screen")


@tool("Turn the Wi-Fi adapter on or off (needs admin approval).", friendly="Switched Wi-Fi",
      risky="Switch Wi-Fi (admin)", dangerous=True, hints="turn wifi on off", props={"on": common.B}, required=["on"])
def toggle_wifi(on):
    state = "enabled" if on in (True, "true", "on", 1) else "disabled"
    return system.run_powershell(f'netsh interface set interface name="Wi-Fi" admin={state}', as_admin=True)
