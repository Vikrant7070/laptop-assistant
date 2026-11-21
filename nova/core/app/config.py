"""Settings (data/settings.json), the brain to use, and the constants every part of the server shares."""


import json
import os

from nova import store
from nova.brain import cloud
from nova.core import characters
from nova import shared
from nova import protocol
from nova.log import log_exception


ROOT = shared.ROOT


PORT = int(os.environ.get("PORT", 5055))


DEFAULT_MODEL = "qwen3-vl:4b-instruct"  # fits a 6 GB laptop GPU; tools + vision, no hidden thinking


ASSISTANT_NAME = "Nova"


MAX_HISTORY = 12  # the 8K context holds ~5300 tokens of prompt + tools, so only ~2900 are left for these (see history_limit)


MAX_STORED = 120  # messages kept in memory at all (Nova runs for days in the tray; only history_limit() reach the model)


PROMPT_TOKENS = 5300   # measured: persona + tool schemas + knowledge summary, before a single conversation turn


CLOUD_HISTORY_MAX = 60  # a cloud window is huge, but a long prompt still costs money and time


MAX_EVENTS = 50   # voice events waiting for the chat page


def _unspoken(text):
    """The user's words without the "[spoken; ...]" marker that voice requests carry."""
    return protocol.unspoken(text)


def settings():
    s = {"model": DEFAULT_MODEL, "think": False, "autonomy": "careful", "camera": False, "screen": False, "voice": True, "wake": False,
         "provider": "ollama", "cloud_models": {}, "notifications": False, "notif_sleep": False, "deep_sleep": True, "listen_pause": 0.7, "listen_max": 10, "mic_level": 5, "notif_muted": [], "phone_calls": True, "email_cloud": False, "mobile": False, "phone_quiet": True, "phone_notes_cloud": False, "suggest": False,
         "reaction_sounds": True, "power_ok": False, "reasoning": "auto",
         "react_to_sound": True, "avatar_roam": "corner", "presence": False, "presence_lock": False, "error_help": True,
         "skill_screen_checks": False, "skill_teach": True, "barge_in": True, "character": characters.DEFAULT}
    try:
        s.update(json.loads(store.current().settings.read_text(encoding="utf-8")))
    except Exception:
        log_exception("settings.json")     # keep the defaults; the broken file is reported in the task log
    return s


def save_settings(s):
    """Atomic write: every request reads this file, and must never see it half-written."""
    tmp = store.current().settings.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    tmp.replace(store.current().settings)


def brain(s=None):
    """The model that thinks for Nova: the chosen cloud model if it has a key, otherwise the local Ollama model
    (s["model"]), which is also the fallback whenever the cloud fails."""
    s = s or settings()
    p = s.get("provider", "ollama")
    name = (s.get("cloud_models") or {}).get(p) or cloud.DEFAULT_MODELS.get(p)
    if p in cloud.PROVIDERS and name and cloud.get_key(p):
        return cloud.model_id(p, name)
    return s["model"]
