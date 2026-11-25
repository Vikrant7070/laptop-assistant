"""Is the user in something that mustn't be interrupted? Windows' own answer, for Phase 8's suggestions.

    notification_state() -> "" (fine to pop up) / "full screen" / "a full-screen game" / "presentation mode" / "away"

SHQueryUserNotificationState is what Windows itself uses to hold back toasts: a full-screen app, a Direct3D game,
presentation mode, or the lock screen. Read only; any failure answers "" (and the caller has its own checks).
"""
import ctypes

STATES = {1: "away", 2: "full screen", 3: "a full-screen game", 4: "presentation mode"}   # QUNS_* values


def notification_state():
    try:
        n = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(n)) != 0:
            return ""
        return STATES.get(n.value, "")
    except Exception:
        from nova.log import log_exception
        log_exception("busy: SHQueryUserNotificationState")
        return ""
