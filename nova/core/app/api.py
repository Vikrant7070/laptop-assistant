"""The local web server: Flask app, the only-this-laptop guard, and the chat / setup routes."""


import time

from flask import Flask, abort, jsonify, request, send_from_directory

from nova.avatar import body as body_module
from nova.brain import cloud
from nova.core import characters
from nova.brain import llm
from nova.actions import presence
from nova.brain import router
from nova.actions import tools
from nova.shared import host_allowed
from nova.core.app import config


app = Flask(__name__, static_folder=str(config.ROOT / "static"))


@app.before_request
def only_this_laptop():
    """Nova's API can run commands on this laptop, so only her own pages may use it.
    - Host must be 127.0.0.1/localhost on our port: stops DNS rebinding (a web page whose domain points at 127.0.0.1).
    - A request sent by another website (its Origin) is refused: stops cross-site POSTs like /api/voice_typing."""
    port = request.environ.get("SERVER_PORT") or config.PORT
    if not (host_allowed(request.host, int(port)) or host_allowed(request.host, config.PORT)):
        abort(403)
    origin = request.headers.get("Origin")
    if origin and not host_allowed(origin.split("://", 1)[-1], int(port)) \
            and not host_allowed(origin.split("://", 1)[-1], config.PORT):
        abort(403)
    if request.method == "POST" and request.headers.get("Sec-Fetch-Site") == "cross-site":
        abort(403)


def _body():
    """The JSON object a POST carries ({} if none); anything else is a 400."""
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        abort(400, "expected a JSON object")
    return body


def _text(body, key, default=""):
    v = body.get(key, default)
    if v is None:
        return default
    if not isinstance(v, str):
        abort(400, f"'{key}' must be text")
    return v.strip()


@app.errorhandler(400)
@app.errorhandler(403)
def _json_error(e):
    return jsonify(error=getattr(e, "description", str(e))), e.code


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/status")
def status():
    s = config.settings()
    return jsonify(name=config.ASSISTANT_NAME, memories=tools.memories(), default_model=config.DEFAULT_MODEL,
                   autonomy=s["autonomy"], think=s["think"], reasoning=s.get("reasoning", "auto"), camera=s["camera"], screen=s["screen"],
                   notifications=s["notifications"], notif_sleep=s.get("notif_sleep", False), deep_sleep=s.get("deep_sleep", True), listen_pause=s.get("listen_pause", 0.7), listen_max=s.get("listen_max", 10), mic_level=s.get("mic_level", 5), phone_calls=s.get("phone_calls", True), email_cloud=bool(s.get("email_cloud")),
                   phone_quiet=s.get("phone_quiet", True), phone_notes_cloud=s.get("phone_notes_cloud", False), suggest=s.get("suggest", False), asleep=voice_io.nap["asleep"], deep_asleep=voice_io.nap.get("deep", False),
                   reaction_sounds=s.get("reaction_sounds", True), power_ok=s.get("power_ok", False),
                   presence=s.get("presence", False), presence_lock=s.get("presence_lock", False),
                   error_help=s.get("error_help", True), barge_in=s.get("barge_in", True), away=presence.state["away"],
                   hands_off_glow=s.get("hands_off_glow", True),
                   presence_note=presence.state["note"], presence_checks=presence.state["checks"],
                   autostart=host.STARTUP_LNK.exists(), character=characters.get(s.get("character"))["id"],
                   characters=characters.choices(),
                   voice=s["voice"], wake=s["wake"], wake_phrase=prompts.wake_phrase(), voice_name=voice_io.mouth.voice_name, ears=voice_io.ears.status, heard=voice_io.ears.heard,
                   vision_model=llm.vision_model(s["model"]), **conversation.ready_info())


@app.get("/api/mic_level")
def mic_level():
    """⚙ Senses live meter: the mic's loudness now and the level that starts a recording (both RMS). Read from
    the recording Whisper already runs - no second microphone stream. live is False while nothing is listening
    (ears off, Windows recognizer, or she is talking and her own voice is being skipped)."""
    if not voice_io.whisper:
        return jsonify(live=False, rms=0.0, start=0.0, why="the offline Whisper ears aren't installed")
    rms, start, at = voice_io.whisper.meter
    live = time.time() - at < 1.0
    return jsonify(live=live, rms=rms if live else 0.0, start=start, why="" if live else "not listening right now")


@app.post("/api/setup")
def setup():
    body = _body()
    s = config.settings()
    model, cloud_model = _text(body, "model"), _text(body, "cloud_model")
    if model:
        s["model"] = model
    if body.get("provider") in ("ollama", *cloud.PROVIDERS):
        s["provider"] = body["provider"]
    if cloud_model and s["provider"] in cloud.PROVIDERS:
        s["cloud_models"] = dict(s.get("cloud_models") or {}, **{s["provider"]: cloud_model})
    if body.get("avatar_roam") in body_module.ROAM_MODES:
        s["avatar_roam"] = body["avatar_roam"]
    if body.get("character") in characters.CHARACTERS and body["character"] != s.get("character"):
        s["character"] = body["character"]
        conversation.task_log(f"CHARACTER switch asked by {request.remote_addr} ({(request.user_agent.string or '?')[:60]})")
        voice_io.apply_character(s["character"])
    if "think" in body:
        s["think"] = bool(body["think"])
    if body.get("reasoning") in prompts.REASONING_CHOICES:
        s["reasoning"] = body["reasoning"]
    if "camera" in body:
        s["camera"] = bool(body["camera"])
    if "screen" in body:
        s["screen"] = bool(body["screen"])
    if "voice" in body:
        s["voice"] = bool(body["voice"])
        if not s["voice"]:
            voice_io.mouth.stop()
    if "wake" in body:
        s["wake"] = bool(body["wake"])
        voice_io.set_ears(s["wake"])
    if body.get("autonomy") in ("careful", "trusted", "full"):
        s["autonomy"] = body["autonomy"]
    if "suggest" in body:
        s["suggest"] = bool(body["suggest"])
    if "notifications" in body:
        s["notifications"] = bool(body["notifications"])
    if "power_ok" in body:
        s["power_ok"] = bool(body["power_ok"])
    if "reaction_sounds" in body:
        s["reaction_sounds"] = bool(body["reaction_sounds"])    # avatar.py reads it through sfx.enabled()
    if isinstance(body.get("routing"), dict):       # 🔀 the Model dialog's smart-switching choices -> config/model_routing.json
        try:
            router.save(body["routing"])
        except (TypeError, ValueError):
            pass
        for k in router.ROUTING_KEYS:               # a copy in settings.json would override the file
            s.pop(k, None)
    try:
        if "listen_pause" in body:
            s["listen_pause"] = round(min(max(float(body["listen_pause"]), voice_io.LISTEN_PAUSE[0]), voice_io.LISTEN_PAUSE[1]), 2)
        if "listen_max" in body:
            s["listen_max"] = min(max(int(body["listen_max"]), voice_io.LISTEN_MAX[0]), voice_io.LISTEN_MAX[1])
        if "mic_level" in body:
            s["mic_level"] = min(max(int(round(float(body["mic_level"]))), voice_io.MIC_LEVEL[0]), voice_io.MIC_LEVEL[1])
    except (TypeError, ValueError):
        pass
    voice_io.apply_listening(s)
    for k in ("presence", "presence_lock", "error_help", "notif_sleep", "deep_sleep", "email_cloud", "phone_calls", "phone_notes_cloud", "phone_quiet", "barge_in", "hands_off_glow"):
        if k in body:
            s[k] = body[k] is True
    config.save_settings(s)
    voice_io.refresh_barge(s)
    if "notifications" in body:
        integrations.notif_watcher.start() if s["notifications"] else integrations.notif_watcher.stop()
    if "notifications" in body or "phone_quiet" in body:
        integrations.sync_phone_banners()
    if "autostart" in body:
        host.set_autostart(bool(body["autostart"]))
    return jsonify(ok=True, **conversation.ready_info())


@app.post("/api/cloud/key")
def cloud_key():
    """Save (or with an empty key, forget) a provider's API key, then prove it works by listing its models."""
    body = _body()
    provider = body.get("provider")
    if provider not in cloud.PROVIDERS:
        return jsonify(error="Pick Google, OpenAI or Anthropic."), 400
    key = _text(body, "key")
    try:
        ids = cloud.list_models(provider, key) if key else []   # check it works before saving it
        cloud.set_key(provider, key)
        llm.reset_cloud()
    except cloud.CloudError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, models=ids, suggested=cloud.suggest(provider, ids) if ids else "")


@app.get("/api/cloud/models")
def cloud_models():
    provider = request.args.get("provider", "")
    if provider not in cloud.PROVIDERS:
        return jsonify(error="Unknown provider."), 400
    try:
        ids = cloud.list_models(provider)
    except cloud.CloudError as e:
        return jsonify(error=str(e), models=[]), 400
    return jsonify(models=ids, suggested=cloud.suggest(provider, ids))


# Used only when a function runs; imported last so each module is complete before its siblings load.
from nova.core.app import conversation, host, integrations, prompts, voice_io  # noqa: E402
