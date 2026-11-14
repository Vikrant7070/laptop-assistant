"""Talks to a local Ollama server (http://127.0.0.1:11434). Standard library only.

Models that support Ollama's native tool calling get the tools directly. Models without it
(some DeepSeek builds) get a text protocol instead: they answer with a small JSON object
whenever they want to use a tool.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

import cloud

HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if not HOST.startswith("http"):
    HOST = "http://" + HOST


# 8K context is the sweet spot on this 6 GB laptop GPU, which Whisper (large-v3-turbo, ~0.9 GB) shares. Measured with
# Whisper loaded: 12288 -> 8-10 tokens/s and ~12 s per screen look (the card is over-full); 8192 -> 13-14 tokens/s
# and ~3.4 s per screen look. 16384 crashed CUDA. The system prompt + tools are ~5300 tokens, so history must stay short.
NUM_CTX = int(os.environ.get("NOVA_NUM_CTX", 8192))
# Ollama decides by itself how much of the model goes on the graphics card (30 of 37 layers here, ~4.4 GB). Do NOT
# pass "num_gpu": tried 28 and 29 to leave more room next to Whisper - replies took twice as long and a screen look
# 30 s instead of ~8 s. The cost of leaving it alone: only ~400 MB of the 6 GB card stays free.
KEEP_ALIVE = os.environ.get("NOVA_KEEP_ALIVE", "30m")  # stay loaded between messages (a reload takes ~2 min)


class OllamaError(Exception):
    pass


def _post(path, body, timeout=600):
    req = urllib.request.Request(HOST + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")
        try:
            detail = json.loads(detail).get("error", detail)
        except ValueError:
            pass
        if e.code == 402:
            raise OllamaError("This cloud model needs Ollama credits or a plan (ollama.com/plans). "
                              "Pick a local model under ⚙ Model to keep going for free.") from e
        if e.code == 401:
            raise OllamaError("Cloud models need you to sign in to Ollama first: run  ollama signin") from e
        raise OllamaError(f"Ollama error {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise OllamaError("I can't reach Ollama. Is it running? (start the Ollama app)") from e


GPU_BROKEN = {"v": os.environ.get("NOVA_CPU_ONLY") == "1"}


def _run(path, body):
    """POST to Ollama; if the graphics card crashes (CUDA/driver problem), retry on the CPU from then on."""
    if GPU_BROKEN["v"]:
        body.setdefault("options", {})["num_gpu"] = 0
    try:
        return _post(path, body)
    except OllamaError as e:
        msg = str(e)
        if GPU_BROKEN["v"] or not any(k in msg for k in ("CUDA", "llama-server process has terminated", "cudaMalloc", "out of memory")):
            raise
        GPU_BROKEN["v"] = True
        body.setdefault("options", {})["num_gpu"] = 0
        return _post(path, body)


def list_models():
    """Installed model names, or None if Ollama isn't running."""
    try:
        with urllib.request.urlopen(HOST + "/api/tags", timeout=3) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return None


_caps = {}


def capabilities(model):
    if cloud.parse(model):
        return {"tools"}          # every cloud brain does native tool calling; images stay local (vision_model)
    if model not in _caps:
        try:
            _caps[model] = set(_post("/api/show", {"model": model}, timeout=10).get("capabilities") or [])
        except OllamaError:
            _caps[model] = set()
    return _caps[model]


def supports_tools(model):
    return "tools" in capabilities(model)


THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def clean(text):
    return THINK_RE.sub("", text or "").replace("<think>", "").strip()


def _to_ollama_tools(tools):
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in tools]


def text_protocol(tools):
    """Instructions for models without native tool calling."""
    lines = []
    for t in tools:
        props = t["input_schema"]["properties"]
        args = ", ".join(f'{k}{"" if k in t["input_schema"]["required"] else "?"}: '
                         f'{"|".join(v["enum"]) if "enum" in v else v["type"]}' for k, v in props.items())
        lines.append(f"- {t['name']}({args}): {t['description']}")
    return ("\n\nTOOLS. To use a tool, reply with ONLY one JSON object and nothing else, for example:\n"
            '{"tool": "get_system_status", "args": {}}\n'
            "You will then receive the result in a message starting with TOOL RESULT. Use one tool at a time. "
            "When you are done, reply normally in plain text (no JSON).\nAvailable tools:\n" + "\n".join(lines))


def _parse_text_call(text, tool_names):
    """Find a {"tool": ..., "args": ...} object in a reply."""
    for m in re.finditer(r"\{", text):
        depth = 0
        for i in range(m.start(), len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                try:
                    obj = json.loads(text[m.start():i + 1])
                except ValueError:
                    break
                if isinstance(obj, dict) and obj.get("tool") in tool_names:
                    args = obj.get("args") or {}
                    return obj["tool"], args if isinstance(args, dict) else {}
                break
    return None


def _post_stream(path, body, on_text, timeout=600, on_think=None):
    """Like _post, but hands each piece of the reply to on_text() the moment the model writes it
    (and each piece of its own reasoning, for a model that thinks out loud, to on_think())."""
    body = dict(body, stream=True)
    if GPU_BROKEN["v"]:
        body.setdefault("options", {})["num_gpu"] = 0
    req = urllib.request.Request(HOST + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    msg = {"content": "", "tool_calls": []}
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            if not line.strip():
                continue
            part = json.loads(line)
            if part.get("error"):
                raise OllamaError(str(part["error"]))
            m = part.get("message") or {}
            if m.get("thinking") and on_think:
                on_think(m["thinking"])
            if m.get("content"):
                msg["content"] += m["content"]
                on_text(m["content"])
            if m.get("tool_calls"):
                msg["tool_calls"] += m["tool_calls"]
            if part.get("done"):
                break
    if not msg["tool_calls"]:
        del msg["tool_calls"]
    return {"message": msg}


# Set after every chat(): why the cloud brain wasn't used this time (None = it was, or none was chosen).
LAST = {"fallback": None}
_cloud_down = {"until": 0.0, "why": ""}
CLOUD_RETRY_SECS = 60   # after a cloud failure, use the local model for this long before trying the cloud again


def reset_cloud():
    _cloud_down.update(until=0.0, why="")


def chat(model, messages, tools, think=False, on_text=None, fallback=None, on_think=None, num_predict=200,
         reasoning="low"):
    """One model turn. Returns (assistant_message_to_store, text, [ {id,name,args} ]).
    on_text(piece) gets the reply piece by piece while it is being written, so Nova can start speaking the first
    sentence before the last one exists. on_think(piece) gets the model's own reasoning, when it shows any (a local
    thinking model, or a summary from Gemini / Claude), for the chat's "what Nova is thinking" log.
    reasoning ("low" | "high" | "max") sets how hard a cloud model thinks first.
    For a cloud model ("cloud:provider:name"), any failure (no internet, bad key, credit ran out...) makes this
    turn run on the local Ollama model `fallback` instead; LAST["fallback"] then says why."""
    LAST["fallback"] = None
    if cloud.parse(model):
        if time.time() >= _cloud_down["until"]:
            try:
                stored, text, calls = cloud.chat(model, messages, tools, reasoning=reasoning, on_think=on_think)
                if on_text and text:
                    on_text(text)
                return stored, text, calls
            except cloud.CloudError as e:
                _cloud_down.update(until=time.time() + CLOUD_RETRY_SECS, why=str(e))
                if not fallback:
                    raise OllamaError(str(e)) from e
        elif not fallback:
            raise OllamaError(_cloud_down["why"])
        LAST["fallback"] = _cloud_down["why"]
        model = fallback
    native = supports_tools(model)
    body = {"model": model, "messages": messages, "stream": False, "keep_alive": KEEP_ALIVE,
            "options": {"num_ctx": NUM_CTX, "temperature": 0.6,
                        "num_predict": num_predict,  # replies are spoken: short (200). Also stops a small model rambling in loops
                        "repeat_penalty": 1.15}}
    if native:
        body["tools"] = _to_ollama_tools(tools)
    if "thinking" in capabilities(model):
        body["think"] = think
    msg = None
    thought = []
    if (on_text or on_think) and native:
        emitted = []

        def got_text(piece):
            if on_text:
                emitted.append(piece)
                on_text(piece)

        def got_thought(piece):
            thought.append(piece)
            if on_think:
                on_think(piece)
        try:
            msg = _post_stream("/api/chat", body, got_text, on_think=got_thought)["message"]
        except Exception:
            if emitted:        # part of it has been spoken already; don't say it twice
                raise OllamaError("Nova's brain stopped in the middle of a reply.")
            msg = None         # nothing said yet: do it the plain way (which also handles a GPU crash)
    if msg is None:
        msg = _run("/api/chat", body)["message"]
        if on_think and not thought:    # not streamed: hand over the reasoning in one piece
            inline = "\n".join(re.findall(r"<think>(.*?)</think>", msg.get("content") or "", re.S))
            if msg.get("thinking") or inline.strip():
                on_think(msg.get("thinking") or inline)
    text = clean(msg.get("content", ""))
    calls = []
    if native:
        for i, c in enumerate(msg.get("tool_calls") or []):
            fn = c.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            calls.append({"id": c.get("id") or f"call_{i}", "name": fn.get("name", ""), "args": args})
        stored = {"role": "assistant", "content": msg.get("content", "")}
        if msg.get("tool_calls"):
            stored["tool_calls"] = msg["tool_calls"]
    else:
        found = _parse_text_call(text, {t["name"] for t in tools})
        if found:
            calls.append({"id": "call_0", "name": found[0], "args": found[1]})
            text = ""  # the JSON itself isn't meant to be shown
        stored = {"role": "assistant", "content": msg.get("content", "")}
    return stored, text, calls


# ------------------------------------------------------------------ eyes: images go to a vision model

VISION_HINTS = ("qwen3-vl", "qwen2.5vl", "gemma3", "llava", "llama3.2-vision", "minicpm-v", "moondream", "granite3.2-vision", "mistral-small3")


def is_cloud(model):
    return bool(model) and (model.endswith(":cloud") or "-cloud" in model or model.startswith("cloud:"))


def vision_model(preferred=None):
    """The chat model if it can see, otherwise any installed model that can. None if there is none.
    Cloud models are never used for images: camera photos stay on this laptop."""
    models = [m for m in (list_models() or []) if not is_cloud(m)]
    if preferred and preferred in models and "vision" in capabilities(preferred):
        return preferred
    for m in models:
        if "vision" in capabilities(m):
            return m
    for m in models:  # older Ollama versions don't report capabilities
        if any(h in m for h in VISION_HINTS):
            return m
    return None


def see(image_b64, question, preferred=None, kind="camera"):
    model = vision_model(preferred)
    if not model:
        return ("I have no eyes yet - no vision model is installed. Ask the user to run: ollama pull qwen3-vl:4b-instruct")
    if kind == "check":      # Nova checking her own work: the caller wrote the whole prompt
        prompt = question
    elif kind == "screen":
        prompt = (f"This is a screenshot of the user's computer screen. {question or 'What is on the screen?'}\n"
                  "Answer in 2-5 short sentences. Name the apps/windows you see, and quote any important text "
                  "(like error messages) exactly. Never repeat passwords, API keys, tokens or other secrets - "
                  "just say a secret is visible.")
    else:
        prompt = (question or "Describe what you see.") + \
            "\nAnswer in 2-4 short sentences. Describe only what is visible; don't guess who a person is."
    body = {"model": model, "stream": False, "keep_alive": KEEP_ALIVE, "options": {"num_ctx": NUM_CTX, "temperature": 0.3},
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}]}
    if kind == "check":
        body["options"]["num_predict"] = 120   # a check is three short lines; keep it quick
    if "thinking" in capabilities(model):
        body["think"] = False
    return clean(_run("/api/chat", body)["message"].get("content", "")) or "(I couldn't make anything out.)"


def tool_result_message(model, call, output):
    if len(output) > 1600:  # keep the small context window for the conversation
        output = output[:1600] + "\n…(cut short)"
    if supports_tools(model):
        return {"role": "tool", "content": output, "tool_name": call["name"]}
    return {"role": "user", "content": f"TOOL RESULT ({call['name']}):\n{output}"}
