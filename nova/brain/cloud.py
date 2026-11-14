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
from nova.shared import DATA

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

# How hard the cloud brain thinks before answering (settings "reasoning"; "auto" is resolved in app.py).
# low = quick voice replies; high/max = real step-by-step reasoning for hard tasks - slower, more tokens.
LEVELS = ("low", "high", "max")
LEVEL_TOKENS = {"low": MAX_TOKENS, "high": 16000, "max": 32000}
# Healthy deep replies took 8-35 s (gemini-4-argon on max); a stuck one hung the full 180 s before the local model
# took over. Past these limits, waiting longer rarely ends in an answer, so the local brain steps in.
LEVEL_TIMEOUT = {"low": CHAT_TIMEOUT, "high": 50, "max": 75}
# reasoning_effort values to try, best first (Gemini's OpenAI endpoint has no "xhigh"; OpenAI's newer models do)
_OPENAI_EFFORT = {"low": ["low"], "high": ["high"], "max": ["xhigh", "high"]}


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
_works = {}       # (model, level, show) -> index of the first request extra that model accepted


def _schema(t, provider):
    params = dict(t["input_schema"])
    if provider == "google":
        params.pop("additionalProperties", None)
        if not params.get("properties"):
            return None
    return params


def _openai_chat(provider, name, key, messages, tools, level="low", show=False):
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
    body = {"model": name, "messages": msgs}
    if defs:                       # a plain writing job has no tools, and an empty list is rejected
        body["tools"] = defs
    body["max_completion_tokens" if provider == "openai" else "max_tokens"] = LEVEL_TOKENS[level]
    url = PROVIDERS[provider]["base"] + "/chat/completions"
    r = None
    tries = _openai_tries(provider, name, level, show)
    for i, extra in enumerate(tries):
        try:
            r = _http("POST", url, key, dict(body, **extra), timeout=LEVEL_TIMEOUT[level])
        except CloudError as e:
            last = i == len(tries) - 1
            if last or "400" not in str(e) or not re.search(
                    r"reason|effort|thinking|thought|extra_body|unknown|unsupported|unrecognized|invalid", str(e), re.I):
                raise
            continue
        _works[(name, level, show)] = i        # next time start with what worked
        if not extra:
            _plain.add(name)                   # takes no reasoning settings at all
        break
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
    # Gemini (include_thoughts) puts a summary of its reasoning in <thought> tags; some models use reasoning_content.
    thoughts = re.findall(r"<thought>(.*?)</thought>", text, re.S)
    text = re.sub(r"<thought>.*?</thought>", "", text, flags=re.S).strip()
    thoughts += [m.get("reasoning_content") or m.get("reasoning") or ""]
    return text, calls, "\n".join(t.strip() for t in thoughts if isinstance(t, str) and t.strip())


def _openai_tries(provider, name, level, show):
    """Request extras to try in order, from the full reasoning setup down to none (an older model rejects them)."""
    if name in _plain:
        return [{}]
    tries = []
    for effort in _OPENAI_EFFORT[level]:
        if provider == "google" and show:     # ask Gemini for a summary of its thoughts, for the thinking log
            tries.append({"reasoning_effort": effort,
                          "extra_body": {"google": {"thinking_config": {"include_thoughts": True}}}})
        tries.append({"reasoning_effort": effort})
    tries.append({})
    start = _works.get((name, level, show), 0)
    return tries[start:] if start < len(tries) else tries


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


def _anthropic_chat(name, key, messages, tools, level="low", show=False):
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

    client = _anthropic_client(key).with_options(timeout=LEVEL_TIMEOUT[level])
    # Non-streaming requests over ~21k max_tokens are refused by the SDK, so high and max both get 16k.
    base = {"model": name, "max_tokens": min(LEVEL_TOKENS[level], 16000), "messages": msgs}
    if tools:
        base["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
                         for t in tools]
    if system:
        base["system"] = system
    extra_body = {"output_config": {"effort": level}}           # low = quick voice replies; high/max = deep reasoning
    if show:   # adaptive thinking is on by default on current models; this only makes its summary readable
        extra_body["thinking"] = {"type": "adaptive", "display": "summarized"}
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
        return "Sorry yaar, I can't help with that one.", [], ""
    text = "".join(b.text for b in r.content if b.type == "text")
    calls = [{"id": b.id, "name": b.name, "args": b.input if isinstance(b.input, dict) else {}}
             for b in r.content if b.type == "tool_use"]
    # Shown in the thinking log only - never stored (Nova edits her history, which preserved thinking forbids).
    thoughts = "\n".join((getattr(b, "thinking", "") or "") for b in r.content if b.type == "thinking").strip()
    return text, calls, thoughts


# ------------------------------------------------------------------ one turn

def chat(model, messages, tools, reasoning="low", on_think=None):
    """Returns (assistant_message_in_ollama_format, text, calls[{id,name,args}]).
    reasoning: "low" | "high" | "max" - how hard the model thinks first. on_think(text) gets a readable summary of
    that reasoning when the provider returns one (Gemini and Claude do; OpenAI's chat endpoint keeps it hidden)."""
    provider, name = parse(model)
    level = reasoning if reasoning in LEVELS else "low"
    show = on_think is not None
    key = get_key(provider)
    if not key:
        raise CloudError(f"No API key saved for {PROVIDERS[provider]['label']}.")
    if provider == "anthropic":
        text, calls, thoughts = _anthropic_chat(name, key, messages, tools, level, show)
    elif provider in PROVIDERS:
        text, calls, thoughts = _openai_chat(provider, name, key, messages, tools, level, show)
    else:
        raise CloudError(f"Unknown provider '{provider}'.")
    if thoughts and on_think:
        on_think(thoughts)
    return _stored(text, calls), text, [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in calls]
