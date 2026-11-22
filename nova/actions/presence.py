"""👋 Is the user at the laptop? Nova greets them when they come back and (if they want) locks it when they leave.

Cheap first, camera last:
  - keyboard / mouse used in the last 20 s  -> they're here (no camera at all)
  - the screen is locked                     -> they're away
  - no input for a minute                    -> a quick camera check (~1 s, a few frames, then the camera is released):
    a face (front or side) -> still here (reading, watching a video); none twice in a row -> away
Frames stay in memory and are thrown away straight after the check; only OpenCV's built-in face finder looks at
them (OpenCV's small YuNet face detector on the CPU, nothing leaves the laptop). It finds A face, it doesn't recognise
WHO it is.
"""
import ctypes
import os
import threading
from pathlib import Path

from nova import runtime

IDLE_HERE = 20          # input this recent = here, no camera needed
IDLE_CHECK = 60         # no input this long -> look with the camera
CHECK_EVERY = 30        # seconds between camera checks while unsure / away
CHECK_SEEN = 120        # ...and while a face was just seen (watching a film): fewer checks, less camera light
MISSES_AWAY = 2         # empty chair this many checks in a row = away
BACK_MIN = 120          # gone at least this long (from their last input) before "welcome back" is worth saying
LOCK_GRACE = 20         # seconds between "locking soon" and locking (move the mouse to stop it)
MAX_MISSED = 8
AWAY_NO_CAMERA = 600   # the camera can't tell (no face model, dark room): this long without input = away (no lock)

state = {"away": False, "since": 0.0, "misses": 0, "last_check": 0.0, "last_face": 0.0, "locking_at": 0.0,
         "missed": [], "note": "", "checks": 0}
_cam = threading.Lock()


def reset():
    state.update(away=False, since=0.0, misses=0, last_check=0.0, last_face=0.0, locking_at=0.0, missed=[],
                 note="", checks=0)


def missed(line):
    """Something she said while the user was away: kept for the 'welcome back' summary."""
    if state["away"]:
        state["missed"] = (state["missed"] + [str(line)[:200]])[-MAX_MISSED:]


# ------------------------------------------------------------------ Windows: locked screen

def screen_locked():
    """True while the Windows lock screen is up (the input desktop isn't the user's 'Default' desktop)."""
    try:
        u = ctypes.windll.user32
        h = u.OpenInputDesktop(0, False, 0x0001)          # DESKTOP_READOBJECTS
        if not h:
            return True
        try:
            buf = ctypes.create_unicode_buffer(64)
            need = ctypes.c_ulong()
            if not u.GetUserObjectInformationW(h, 2, buf, ctypes.sizeof(buf), ctypes.byref(need)):   # UOI_NAME
                return False
            return buf.value.lower() != "default"
        finally:
            u.CloseDesktop(h)
    except Exception:
        return False


# ------------------------------------------------------------------ the camera: is there a face?
# YuNet: OpenCV's own small face detector (an ONNX file of ~230 KB, Apache-2.0, from the OpenCV model zoo) run by
# cv2.FaceDetectorYN on the CPU in a few milliseconds. OpenCV 5 no longer ships the old Haar face finders, so the
# file is downloaded once, the first time presence is switched on, into ~/.cache/nova-face. Until it's there (or if
# the download fails) the camera can't tell, and only the lock screen and a long time without input count as away.

FACE_DIR = Path(os.environ.get("NOVA_FACE_DIR", Path.home() / ".cache" / "nova-face"))
YUNET = "face_detection_yunet_2023mar.onnx"
YUNET_SOURCES = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/" + YUNET,
    "https://huggingface.co/opencv/face_detection_yunet/resolve/main/" + YUNET,
)
FACE_SCORE = 0.75        # YuNet's confidence for "that's a face"
_det = {"yunet": None, "haar": None, "failed": "", "loading": False}
_det_lock = threading.Lock()


def _haar():
    """The old Haar face finders - only on OpenCV builds that still have them."""
    import cv2
    if _det["haar"] is None:
        found = []
        if hasattr(cv2, "CascadeClassifier"):
            for f in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
                c = cv2.CascadeClassifier(cv2.data.haarcascades + f)
                if not c.empty():
                    found.append(c)
        _det["haar"] = found
    return _det["haar"]


def download_model():
    """Fetch YuNet once. Returns the file, or None (the reason is in _det['failed'])."""
    import urllib.request
    target = FACE_DIR / YUNET
    if target.exists():
        return target
    FACE_DIR.mkdir(parents=True, exist_ok=True)
    errors = []
    for url in YUNET_SOURCES:
        tmp = target.with_name("partial_" + YUNET)     # must end in .onnx: OpenCV picks the reader by extension
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = r.read(4_000_000)
            if not (50_000 < len(data) < 3_000_000) or b"<html" in data[:200].lower():
                raise ValueError(f"unexpected file ({len(data)} bytes)")
            tmp.write_bytes(data)
            _make_yunet(tmp)                      # must load as a face detector, or it's thrown away
            tmp.replace(target)
            return target
        except Exception as e:
            errors.append(f"{url.split('/')[2]}: {type(e).__name__}: {e}"[:160])
            tmp.unlink(missing_ok=True)
    _det["failed"] = "couldn't download the face model (" + "; ".join(errors) + ")"
    return None


def _make_yunet(path):
    import cv2
    return cv2.FaceDetectorYN.create(str(path), "", (320, 240), FACE_SCORE, 0.3, 50)


def detector(download=False):
    """('yunet', detector) / ('haar', [finders]) / (None, None). download=True starts fetching YuNet in the background."""
    with _det_lock:
        if _det["yunet"] is not None:
            return "yunet", _det["yunet"]
        path = FACE_DIR / YUNET
        if path.exists():
            try:
                _det["yunet"] = _make_yunet(path)
                return "yunet", _det["yunet"]
            except Exception as e:
                _det["failed"] = f"the face model didn't load: {e}"[:200]
        if download and not _det["loading"] and not path.exists():
            _det["loading"] = True

            def fetch():
                try:
                    download_model()
                finally:
                    _det["loading"] = False
            runtime.spawn("nova-face-download", fetch)
    haar = _haar()
    return ("haar", haar) if haar else (None, None)


def faces_in(frame):
    """Number of faces in one camera frame (BGR or grayscale), or None if no face detector is available."""
    import cv2
    kind, det = detector()
    if not kind:
        return None
    h, w = frame.shape[:2]
    if w > 640:
        frame = cv2.resize(frame, (640, int(h * 640 / w)))
        h, w = frame.shape[:2]
    if kind == "yunet":
        bgr = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        with _det_lock:
            det.setInputSize((w, h))
            _, faces = det.detect(bgr)
        return 0 if faces is None else int(sum(1 for f in faces if f[-1] >= FACE_SCORE))
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    n = 0
    for i, c in enumerate(det):
        n += len(c.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(48, 48)))
        if i == 1 and not n:                              # the profile finder only knows one side: flip for the other
            n += len(c.detectMultiScale(cv2.flip(gray, 1), scaleFactor=1.1, minNeighbors=6, minSize=(48, 48)))
        if n:
            break
    return n


def look(frames=6):
    """True = a face is in front of the camera, False = nobody, None = can't tell (camera busy, dark, no model)."""
    import cv2
    from nova.actions import tools
    if tools._camera_users():                  # a video call has the camera: they're clearly here
        state["note"] = "camera busy (another app) - counted as here"
        return True
    kind, _ = detector(download=True)
    if not kind:
        state["note"] = ("downloading the face model…" if _det["loading"] else _det["failed"] or "no face model yet")
        return None
    if not _cam.acquire(timeout=5):
        return None
    try:
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened():
            state["note"] = "can't open the camera"
            return None
        got = []
        try:
            for i in range(frames + 4):        # the first frames are dark while the camera adjusts
                ok, f = cap.read()
                if ok and f is not None and i >= 4:
                    got.append(f)
        finally:
            cap.release()
    finally:
        _cam.release()
    if not got:
        state["note"] = "the camera gave no picture"
        return None
    if max(g.mean() for g in got) < 12:
        state["note"] = "camera picture is dark (lens covered or lights off)"
        return None
    seen = sum(1 for g in got if faces_in(g))
    state["note"] = f"face in {seen}/{len(got)} frames ({kind})"
    return seen > 0


# ------------------------------------------------------------------ the decision, once every few seconds

def tick(now, idle, locked, see, lock_on=False):
    """One step. idle = seconds since the last keyboard/mouse input; see() = look() (True/False/None).
    Returns a list of events: 'away', 'back', 'lock_warn', 'lock', 'lock_cancel'."""
    ev = []

    def go_away():
        if not state["away"]:
            # Gone since their last input, not since we decided (that's ~1.5 min later: live test 2026-11-17).
            state.update(away=True, since=now - (0 if locked else idle))   # missed stays until a welcome says it
            ev.append("away")

    def come_back():
        if state["locking_at"]:
            state["locking_at"] = 0.0
            ev.append("lock_cancel")
        if state["away"]:
            gone = now - state["since"]
            state["away"] = False
            ev.append("back" if gone >= BACK_MIN else "back_quiet")
        state["misses"] = 0

    if locked:
        state["locking_at"] = 0.0
        go_away()
        return ev
    if idle < IDLE_HERE:
        come_back()
        return ev
    if state["locking_at"] and now >= state["locking_at"]:
        state["locking_at"] = 0.0
        if idle >= LOCK_GRACE and see() is False:      # one last look: still nobody
            ev.append("lock")
            return ev
        ev.append("lock_cancel")
    if idle < IDLE_CHECK:
        return ev
    every = CHECK_SEEN if (state["last_face"] and now - state["last_face"] < CHECK_SEEN + 10) else CHECK_EVERY
    if now - state["last_check"] < every:
        return ev
    state["last_check"] = now
    state["checks"] += 1
    face = see()
    if face is True:
        state["last_face"] = now
        come_back()
    elif face is None and idle >= AWAY_NO_CAMERA:
        go_away()                                       # can't see: a long time without input will do (never locks)
    elif face is False:
        state["misses"] += 1
        state["last_face"] = 0.0
        if state["misses"] >= MISSES_AWAY and not state["away"]:
            go_away()
            if lock_on:
                state["locking_at"] = now + LOCK_GRACE
                ev.append("lock_warn")
    return ev


def welcome(name=""):
    """What she says when the user is back."""
    who = f", {name}" if name else ""
    m = state["missed"]
    state["missed"] = []
    if not m:
        return f"Welcome back{who}! 👋"
    if len(m) == 1:
        return f"Welcome back{who}! While you were away: {m[0]}"
    return f"Welcome back{who}! {len(m)} things happened while you were away: " + " • ".join(m)
