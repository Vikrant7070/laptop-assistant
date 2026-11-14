"""Cloud brains for Nova: Google Gemini, OpenAI (ChatGPT) and Anthropic (Claude).

A cloud model is named "cloud:<provider>:<model>", e.g. "cloud:google:gemini-3-flash". Everything else in Nova keeps
its chat history in Ollama's format; this module converts it for each provider and converts the reply back, so
Nova can switch brains (or fall back to the local model) in the middle of a conversation.

API keys are stored in data/keys.json, encrypted with Windows DPAPI (only this Windows user on this laptop can
read them). Environment variables GEMINI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY work too.
Images never go to a cloud model: screen and camera looks always use the local vision model (see llm.vision_model).
"""
import base64
import ctypes
import json
import os
import re
import urllib.error
import urllib.request
from ctypes import wintypes
from shared import DATA

PROVIDERS = {
    "google": {"label": "Google Gemini", "env": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
               "base": "https://generativelanguage.googleapis.com/v1beta/openai"},
    "openai": {"label": "OpenAI ChatGPT", "env": ("OPENAI_API_KEY",), "base": "https://api.openai.com/v1"},
    "anthropic": {"label": "Anthropic Claude", "env": ("ANTHROPIC_API_KEY",)},
}
DEFAULT_MODELS = {"anthropic": "claude-opus-5-5"}
KEYS_FILE = DATA / "keys.json"
MAX_TOKENS = 4096   # room for the model's own (hidden) reasoning; Nova's prompt keeps the spoken answer short
# A healthy reply takes 2-5 s. Overloaded models (Gemini answers 503 or just hangs) shouldn't keep the user waiting:
# after this long, give up and let the local model answer.
CHAT_TIMEOUT = 25


class CloudError(Exception):
    pass


# ------------------------------------------------------------------ names

def model_id(provider, name):
    return f"cloud:{provider}:{name}"


def parse(model):
    """'cloud:google:gemini-x' -> ('google', 'gemini-x'); anything else -> None."""
    parts = (model or "").split(":", 2) if (model or "").startswith("cloud:") else []
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


# ------------------------------------------------------------------ keys (Windows DPAPI)

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise CloudError("Windows couldn't encrypt/decrypt the saved key.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _load_keys():
    try:
        return json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_key(provider):
    enc = _load_keys().get(provider)
    if enc:
        try:
            return _dpapi(base64.b64decode(enc), False).decode()
        except Exception:
            pass
    for var in PROVIDERS.get(provider, {}).get("env", ()):
        if os.environ.get(var):
            return os.environ[var].strip()
    return ""


def set_key(provider, key):
    if provider not in PROVIDERS:
        raise CloudError(f"Unknown provider '{provider}'.")
    keys = _load_keys()
    key = str(key or "").strip()
    if key:
        keys[provider] = base64.b64encode(_dpapi(key.encode(), True)).decode()
    else:
        keys.pop(provider, None)
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = KEYS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(keys, indent=1), encoding="utf-8")
    tmp.replace(KEYS_FILE)


def has_keys():
    return {p: bool(get_key(p)) for p in PROVIDERS}


# ------------------------------------------------------------------ HTTP (Gemini + OpenAI speak the same API)

def _http(method, url, key, body=None, timeout=90):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")
        try:
            j = json.loads(detail)
            j = j[0] if isinstance(j, list) and j else j
            detail = (j.get("error") or {}).get("message") or detail
        except (ValueError, AttributeError):
            pass
        raise CloudError(_friendly(e.code, detail[:300])) from e
    except TimeoutError as e:
        raise CloudError(f"The cloud brain didn't answer within {timeout} s (busy or overloaded).") from e
    except (urllib.error.URLError, ConnectionError) as e:
        if isinstance(getattr(e, "reason", None), TimeoutError):
            raise CloudError(f"The cloud brain didn't answer within {timeout} s (busy or overloaded).") from e
        raise CloudError("I can't reach the cloud brain (no internet?).") from e


def _friendly(code, detail):
    if code in (401, 403) or (code == 400 and "api key" in detail.lower()):
        return f"The API key was refused ({code}): {detail}"
    if code == 503:
        return f"The cloud model is overloaded right now (503): {detail}"
    if code == 429:
        return f"The cloud brain's limit or credit ran out for now (429): {detail}"
    return f"Cloud error {code}: {detail}"


# ------------------------------------------------------------------ model lists

_SKIP = re.compile(r"embed|image|imagen|veo|tts|audio|realtime|transcribe|live|search|aqa|moderation|dall|whisper|"
                   r"babbage|davinci|instruct|computer|codex|robotics|learnlm", re.I)


def list_models(provider, key=None):
    """Chat model ids the key (default: the saved one) can use, best guesses first."""
    key = key or get_key(provider)
    if not key:
        raise CloudError("No API key saved for " + PROVIDERS[provider]["label"])
    if provider == "anthropic":
        client = _anthropic_client(key)
        try:
            ids = [m.id for m in client.models.list(limit=100)]
        except Exception as e:
            raise CloudError(_anthropic_error(e)) from e
        return ids
    data = _http("GET", PROVIDERS[provider]["base"] + "/models", key, timeout=20).get("data", [])
    ids = [m["id"].removeprefix("models/") for m in data]
    if provider == "google":
        ids = [i for i in ids if i.startswith("gemini") and not _SKIP.search(i)]
    else:
        ids = [i for i in ids if re.match(r"(gpt-|o\d|chatgpt)", i) and not _SKIP.search(i)]
    return sorted(set(ids), key=_rank)


def _rank(i):
    """Newest-looking, general-purpose models first; previews and dated snapshots later."""
    version = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)", i)[:1]] or [0]
    return (bool(re.search(r"preview|exp|\d{4}-\d{2}|\d{6,}", i)), -version[0], len(i), i)


def suggest(provider, ids):
    """A sensible default for a voice assistant: fast and good at tools."""
    if provider == "anthropic":
        return DEFAULT_MODELS["anthropic"] if DEFAULT_MODELS["anthropic"] in ids or not ids else ids[0]
    pref = r"^gemini-[\d.]+-flash$" if provider == "google" else r"^gpt-[\d.]+-mini$"
    return next((i for i in ids if re.match(pref, i)), ids[0] if ids else "")


# ------------------------------------------------------------------ history: Ollama format -> neutral turns

def _neutral(messages):
    """Returns (system_text, turns). Turn kinds: user(text) | assistant(text, calls[id,name,args,extra]) |
    tool(id, name, content). Tool-call ids are made fresh here, so they always match their results."""
    system, turns, waiting, n = [], [], [], 0

    def close_waiting():
        for c in waiting:   # a call whose result went missing: every provider insists on one
            turns.append({"kind": "tool", "id": c["id"], "name": c["name"], "content": "(no result)"})
        waiting.clear()

    for m in messages:
        role, content = m.get("role"), m.get("content") or ""
        if role == "system":
            system.append(content)
        elif role == "assistant":
            close_waiting()
            calls = []
            for c in m.get("tool_calls") or []:
                fn = c.get("function", {})
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                n += 1
                calls.append({"id": f"call_{n}", "name": fn.get("name", ""), "args": args,
                              "extra": c.get("extra_content")})
            waiting.extend(calls)
            turns.append({"kind": "assistant", "text": content, "calls": calls})
        elif role == "tool":
            name = m.get("tool_name", "")
            c = next((w for w in waiting if w["name"] == name), waiting[0] if waiting else None)
            if c:
                waiting.remove(c)
                turns.append({"kind": "tool", "id": c["id"], "name": c["name"], "content": content})
            else:
                turns.append({"kind": "user", "text": f"TOOL RESULT ({name}):\n{content}"})
        else:
            close_waiting()
            turns.append({"kind": "user", "text": content})
    close_waiting()
    return "\n\n".join(system), turns


def _stored(text, calls):
    """The reply in Ollama's format (what Nova keeps in its history)."""
    msg = {"role": "assistant", "content": text}
    if calls:
        msg["tool_calls"] = []
        for c in calls:
            tc = {"id": c["id"], "function": {"name": c["name"], "arguments": c["args"]}}
            if c.get("extra"):
                tc["extra_content"] = c["extra"]
            msg["tool_calls"].append(tc)
    return msg


# ------------------------------------------------------------------ Gemini + OpenAI (chat completions)

_plain = set()    # models that refused an optional setting (e.g. reasoning_effort): sent without extras from then on


def _schema(t, provider):
    params = dict(t["input_schema"])
    if provider == "google":
        params.pop("additionalProperties", None)
        if not params.get("properties"):
            return None
    return params


def _openai_chat(provider, name, key, messages, tools):
    system, turns = _neutral(messages)
    msgs = [{"role": "system", "content": system}] if system else []
    for t in turns:
        if t["kind"] == "user":
            msgs.append({"role": "user", "content": t["text"]})
        elif t["kind"] == "tool":
            msgs.append({"role": "tool", "tool_call_id": t["id"], "content": t["content"]})
        else:
            m = {"role": "assistant", "content": t["text"] or None}
            if t["calls"]:
                m["tool_calls"] = []
                for c in t["calls"]:
                    tc = {"id": c["id"], "type": "function",
                          "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)}}
                    if provider == "google":   # Gemini 3 wants its "thought signature" back; a call it didn't
                        tc["extra_content"] = c["extra"] or {"google": {"thought_signature": "skip_thought_signature_validator"}}
                    m["tool_calls"].append(tc)
            msgs.append(m)
    defs = []
    for t in tools:
        fn = {"name": t["name"], "description": t["description"]}
        params = _schema(t, provider)
        if params is not None:
            fn["parameters"] = params
        defs.append({"type": "function", "function": fn})
    body = {"model": name, "messages": msgs, "tools": defs}
    body["max_completion_tokens" if provider == "openai" else "max_tokens"] = MAX_TOKENS
    url = PROVIDERS[provider]["base"] + "/chat/completions"
    full = dict(body, reasoning_effort="low")       # quicker replies for a voice assistant
    try:
        r = _http("POST", url, key, body if name in _plain else full, timeout=CHAT_TIMEOUT)
    except CloudError as e:
        if name in _plain or "400" not in str(e) or not re.search(r"reason|effort|thinking|unknown|unsupported|unrecognized", str(e), re.I):
            raise
        _plain.add(name)
        r = _http("POST", url, key, body, timeout=CHAT_TIMEOUT)
    choice = (r.get("choices") or [{}])[0]
    m = choice.get("message") or {}
    calls = []
    for i, c in enumerate(m.get("tool_calls") or []):
        fn = c.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        calls.append({"id": c.get("id") or f"call_{i}", "name": fn.get("name", ""),
                      "args": args if isinstance(args, dict) else {}, "extra": c.get("extra_content")})
    text = m.get("content") or ""
    if isinstance(text, list):   # some models return content parts
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    return text, calls


# ------------------------------------------------------------------ Anthropic (official SDK)

_FALLBACK_MODELS = ("claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
_clients = {}


def _anthropic_client(key):
    if key not in _clients:
        try:
            import anthropic
        except ImportError as e:
            raise CloudError("The Claude brain needs the anthropic package: pip install anthropic") from e
        _clients.clear()
        _clients[key] = anthropic.Anthropic(api_key=key, max_retries=1, timeout=CHAT_TIMEOUT * 2)
    return _clients[key]


def _anthropic_error(e):
    import anthropic
    if isinstance(e, anthropic.AuthenticationError):
        return "The Claude API key was refused."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "This Claude API key isn't allowed to use that model."
    if isinstance(e, anthropic.NotFoundError):
        return "Claude doesn't know that model name."
    if isinstance(e, anthropic.RateLimitError):
        return "Claude's rate limit or credit ran out for now."
    if isinstance(e, anthropic.APIStatusError):
        return f"Claude error {e.status_code}: {getattr(e, 'message', e)}"[:300]
    if isinstance(e, anthropic.APIConnectionError):
        return "I can't reach Claude (no internet?)."
    return f"Claude error: {e}"[:300]


def _anthropic_chat(name, key, messages, tools):
    import anthropic
    system, turns = _neutral(messages)
    msgs = []

    def add(role, blocks):
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"].extend(blocks)
        else:
            msgs.append({"role": role, "content": blocks})

    for t in turns:
        if t["kind"] == "user":
            add("user", [{"type": "text", "text": t["text"] or "(empty)"}])
        elif t["kind"] == "tool":
            add("user", [{"type": "tool_result", "tool_use_id": t["id"], "content": t["content"] or "(empty)"}])
        else:
            blocks = [{"type": "text", "text": t["text"]}] if t["text"].strip() else []
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]} for c in t["calls"]]
            add("assistant", blocks or [{"type": "text", "text": "…"}])
    # Within one user turn the tool results must come before any text.
    for m in msgs:
        if m["role"] == "user":
            m["content"].sort(key=lambda b: b["type"] != "tool_result")

    client = _anthropic_client(key)
    base = {"model": name, "max_tokens": MAX_TOKENS, "messages": msgs,
            "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools]}
    if system:
        base["system"] = system
    extra_body = {"output_config": {"effort": "low"}}           # quick replies for a voice assistant
    extra_headers = {}
    if name in _FALLBACK_MODELS:      # if Claude declines a request, the API retries it on another Claude model
        extra_body["fallbacks"] = "default"
        extra_headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
    try:
        if name in _plain:
            r = client.messages.create(**base)
        else:
            try:
                r = client.messages.create(**base, extra_body=extra_body, extra_headers=extra_headers)
            except anthropic.BadRequestError:
                _plain.add(name)      # an older model that doesn't take effort/fallbacks
                r = client.messages.create(**base)
    except Exception as e:
        raise CloudError(_anthropic_error(e)) from e
    if r.stop_reason == "refusal":
        return "Sorry yaar, I can't help with that one.", []
    text = "".join(b.text for b in r.content if b.type == "text")
    calls = [{"id": b.id, "name": b.name, "args": b.input if isinstance(b.input, dict) else {}}
             for b in r.content if b.type == "tool_use"]
    return text, calls


# ------------------------------------------------------------------ one turn

def chat(model, messages, tools):
    """Returns (assistant_message_in_ollama_format, text, calls[{id,name,args}])."""
    provider, name = parse(model)
    key = get_key(provider)
    if not key:
        raise CloudError(f"No API key saved for {PROVIDERS[provider]['label']}.")
    if provider == "anthropic":
        text, calls = _anthropic_chat(name, key, messages, tools)
    elif provider in PROVIDERS:
        text, calls = _openai_chat(provider, name, key, messages, tools)
    else:
        raise CloudError(f"Unknown provider '{provider}'.")
    return _stored(text, calls), text, [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in calls]
