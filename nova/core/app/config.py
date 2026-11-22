"""Settings (data/settings.json), the brain to use, and the constants every part of the server shares."""


import copy
import json
import os
import typing
from dataclasses import dataclass, field, fields

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


SCHEMA = 1   # bump it when a key is renamed or its meaning changes, and add a step to MIGRATIONS


@dataclass
class Settings:
    """Every key of data/settings.json, with its type and default, in one place (roadmap R7). Loading never drops a
    key: one this class doesn't know is kept in `extra` (the model-routing overrides live there on purpose - a key in
    settings.json overrides data/model_routing.json, so they must not get a default here). A value of the wrong type
    becomes the default, and the bad value is logged."""
    model: str = DEFAULT_MODEL
    think: bool = False
    autonomy: typing.Literal["careful", "trusted", "full"] = "careful"
    camera: bool = False
    screen: bool = False
    voice: bool = True
    wake: bool = False
    provider: str = "ollama"
    cloud_models: dict = field(default_factory=dict)
    notifications: bool = False
    notif_sleep: bool = False
    deep_sleep: bool = True
    listen_pause: float = 0.7
    listen_max: int = 10
    mic_level: int = 5
    notif_muted: list = field(default_factory=list)
    phone_calls: bool = True
    email_cloud: bool = False
    mobile: bool = False
    phone_quiet: bool = True
    phone_notes_cloud: bool = False
    suggest: bool = False
    reaction_sounds: bool = True
    power_ok: bool = False
    reasoning: str = "auto"
    react_to_sound: bool = True
    avatar_roam: str = "corner"
    presence: bool = False
    presence_lock: bool = False
    error_help: bool = True
    skill_screen_checks: bool = False
    skill_teach: bool = True
    barge_in: bool = True
    character: str = characters.DEFAULT
    phone_banners_hidden: bool = False   # Nova hid Phone Link's own pop-ups, so she turns them back on
    schema: int = SCHEMA
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw):
        """A dict (from the file or a caller) -> Settings: migrated, each known key checked against its type."""
        raw = _migrate(dict(raw))
        known, extra = {}, {}
        for k, v in raw.items():
            (known if k in _FIELDS else extra)[k] = v
        out = cls(extra=copy.deepcopy(extra))
        for name, v in known.items():
            f = _FIELDS[name]
            ok, value = _typed(v, f.type)
            if ok:
                setattr(out, name, copy.deepcopy(value))
            else:
                log_exception(f"settings.json: {name}", TypeError(
                    f"{name}={v!r} isn't {_type_name(f.type)}; using the default {getattr(out, name)!r}"))
        out.schema = SCHEMA
        return out

    def to_dict(self):
        """The plain dict every caller reads (`settings()["x"]`): the known keys, then the unknown ones."""
        d = {f.name: copy.deepcopy(getattr(self, f.name)) for f in fields(self) if f.name != "extra"}
        return {**d, **{k: copy.deepcopy(v) for k, v in self.extra.items() if k not in d}}


_FIELDS = {f.name: f for f in fields(Settings) if f.name != "extra"}

MIGRATIONS = {}   # {old schema: function(raw dict) -> raw dict for old schema + 1}


def _migrate(raw):
    v = raw.get("schema", SCHEMA)
    v = v if isinstance(v, int) and not isinstance(v, bool) else SCHEMA
    while v < SCHEMA:
        raw = MIGRATIONS[v](raw)
        v += 1
    return raw


def _type_name(tp):
    if typing.get_origin(tp) is typing.Literal:
        return "one of " + ", ".join(map(repr, typing.get_args(tp)))
    return tp.__name__


def _typed(v, tp):
    """(fits, value): whether v fits the field type, and the value to keep (an int for a float field becomes float)."""
    if typing.get_origin(tp) is typing.Literal:
        return v in typing.get_args(tp), v
    if tp is float:
        ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        return ok, float(v) if ok else v
    if tp is int:
        return isinstance(v, int) and not isinstance(v, bool), v
    return isinstance(v, tp), v


def settings():
    """The settings as a plain dict, every known key present and of its type (Settings.to_dict)."""
    raw = {}
    try:
        raw = json.loads(store.current().settings.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"settings.json holds a {type(raw).__name__}, not an object")
    except Exception:
        raw = {}
        log_exception("settings.json")     # keep the defaults; the broken file is reported in the task log
    return Settings.from_dict(raw).to_dict()


def save_settings(s):
    """Validate (wrong types -> defaults, unknown keys kept), then write atomically: every request reads this file,
    and must never see it half-written."""
    data = (s if isinstance(s, Settings) else Settings.from_dict(s)).to_dict()
    tmp = store.current().settings.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
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
