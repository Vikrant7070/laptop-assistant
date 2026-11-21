"""Finding what matters for one request: the closest facts, lessons and tools to what the user just said.

A small sentence-embedding model (bge-small-en-v1.5, int8 ONNX, ~34 MB) runs on the CPU through onnxruntime -
the same runtime Nova's voice already uses, so no new native libraries (Smart App Control) and no GPU memory,
which is full (Ollama + Whisper). One sentence takes a few milliseconds.

The model is downloaded once into ~/.cache/nova-embed and loaded in the background at start-up. Until it is
ready - or if it can't be loaded at all (offline on first run) - similarity falls back to shared words, so Nova
never waits for it.
"""
import os
import re
import threading
from pathlib import Path

REPO = "Xenova/bge-small-en-v1.5"
FILES = ("onnx/model_quantized.onnx", "tokenizer.json")
CACHE = Path(os.environ.get("NOVA_EMBED_DIR", Path.home() / ".cache" / "nova-embed"))
MAX_TOKENS = 128          # facts, lessons and tool descriptions are all short

_lock = threading.Lock()
_model = {"session": None, "tok": None, "failed": None, "loading": False}
_vectors = {}             # text -> unit vector (texts repeat every request: tool descriptions, facts)
STOP = {"the", "and", "for", "that", "this", "with", "was", "when", "then", "not", "you", "are", "her", "she", "his",
        "user", "can", "please", "what", "how", "its", "it's", "my", "me", "is", "a", "an", "to", "of", "in", "on"}


def _files():
    paths = [CACHE / REPO.replace("/", "--") / f for f in FILES]
    if all(p.exists() for p in paths):
        return paths
    from huggingface_hub import hf_hub_download
    out = []
    for f, p in zip(FILES, paths):
        p.parent.mkdir(parents=True, exist_ok=True)
        got = Path(hf_hub_download(REPO, f, local_dir=str(CACHE / REPO.replace("/", "--"))))
        out.append(got)
    return out


def load():
    """Load the model (downloading it the first time). Safe to call many times; returns True when usable."""
    with _lock:
        if _model["session"] is not None:
            return True
        if _model["failed"]:
            return False
        _model["loading"] = True
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
        onnx_path, tok_path = _files()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2          # stay light next to Whisper and the voice
        opts.log_severity_level = 3
        session = ort.InferenceSession(str(onnx_path), opts, providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(str(tok_path))
        tok.enable_truncation(MAX_TOKENS)
        tok.enable_padding()
        with _lock:
            _model.update(session=session, tok=tok, loading=False)
        return True
    except Exception as e:
        with _lock:
            _model.update(failed=f"{type(e).__name__}: {e}", loading=False)
        return False


def load_in_background():
    threading.Thread(target=load, daemon=True).start()


def ready():
    return _model["session"] is not None


def status():
    return "ready" if ready() else ("loading" if _model["loading"] else (_model["failed"] or "not loaded"))


def embed(texts):
    """Unit vectors (numpy, one row per text). Only call when ready()."""
    import numpy as np
    todo = [t for t in dict.fromkeys(texts) if t not in _vectors]
    if todo:
        enc = _model["tok"].encode_batch(todo)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in {i.name for i in _model["session"].get_inputs()}:
            feed["token_type_ids"] = np.zeros_like(ids)
        out = _model["session"].run(None, feed)[0][:, 0]          # bge: the [CLS] vector
        out = out / np.linalg.norm(out, axis=1, keepdims=True)
        for t, v in zip(todo, out):
            _vectors[t] = v
        if len(_vectors) > 5000:                                   # don't grow forever
            for k in list(_vectors)[:2000]:
                _vectors.pop(k, None)
    return np.stack([_vectors[t] for t in texts])


def words(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def scores(query, texts):
    """Similarity of each text to the query, roughly 0..1. Embeddings when the model is ready, shared words otherwise."""
    if not texts:
        return []
    if query.strip() and ready():
        try:
            v = embed([query] + list(texts))
            return [float(x) for x in v[1:] @ v[0]]
        except Exception:
            from nova.log import log_exception
            log_exception("embeddings")      # fall back to shared words below
    q = words(query)
    return [len(q & words(t)) / max(1, len(q)) if q else 0.0 for t in texts]


# Below this, a fact is not about the request (bge-small puts unrelated short sentences at ~0.3-0.5).
RELEVANT = 0.6


def relevant_bar():
    return RELEVANT if ready() else 0.34      # word overlap: at least a third of the request's words
