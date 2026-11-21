"""`python -m nova --doctor`: is this laptop ready to run Nova? One line per check, each with a fix.

    [ok]   Ollama is running (http://127.0.0.1:11434)
    [FAIL] Model qwen3-vl:4b-instruct is not pulled            -> run: ollama pull qwen3-vl:4b-instruct
    [warn] No microphone found                                  -> plug one in, or use typing only

FAIL = Nova can't work as set up; warn = an optional part (voice, GPU, a routing model) won't work.
`--quiet` prints only problems; the exit code is 1 if anything FAILed. `Start Nova.bat` runs it first and shows the
problems in a message box.

Standard library only at import time: the checks import their packages lazily, so the doctor runs (and says what's
missing) on a machine where the optional extras aren't installed.
"""
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "qwen3-vl:4b-instruct"          # = nova.core.app.config.DEFAULT_MODEL (a test keeps them equal)
NEURAL_DIR = Path.home() / ".cache" / "nova-voice"
NEURAL_FILES = ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin")    # = nova.audio.voice NEURAL_MODEL, NEURAL_VOICES
WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_KEYS = [
    ("HKEY_LOCAL_MACHINE", rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
    ("HKEY_LOCAL_MACHINE", rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
    ("HKEY_CURRENT_USER", rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
]

OK, WARN, FAIL = "ok", "warn", "FAIL"


@dataclass
class Result:
    status: str          # OK / WARN / FAIL
    what: str
    fix: str = ""

    def line(self):
        head = f"[{self.status}]".ljust(7) + self.what
        return f"{head:<62} -> {self.fix}" if self.fix else head


# ------------------------------------------------------------------ the checks (each returns a list of Results)


def _settings():
    from nova import store
    try:
        return json.loads(Path(store.current().settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _routing():
    from nova import store
    try:
        return json.loads(Path(store.current().routing).read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        return {}


def check_ollama():
    from nova.brain import llm
    models = llm.list_models()
    if models is None:
        return [Result(FAIL, f"Ollama isn't answering ({llm.HOST})",
                       "start Ollama (install it from https://ollama.com/download)")]
    out = [Result(OK, f"Ollama is running ({llm.HOST})")]
    have = set(models) | {m.removesuffix(":latest") for m in models}
    model = _settings().get("model") or DEFAULT_MODEL
    if not llm.is_cloud(model):
        out.append(Result(OK, f"Model {model} is pulled") if model in have else
                   Result(FAIL, f"Model {model} is not pulled", f"run: ollama pull {model}"))
    if DEFAULT_MODEL not in have and model != DEFAULT_MODEL:   # the eyes: screen and camera looks stay local
        out.append(Result(WARN, f"Vision model {DEFAULT_MODEL} is not pulled (screen/camera looks)",
                          f"run: ollama pull {DEFAULT_MODEL}"))
    r = _routing()
    if r.get("auto_route", True):
        for key in ("coder_model", "deep_model", "heavy_model"):
            m = r.get(key)
            if m and not llm.is_cloud(m) and m not in have:
                out.append(Result(WARN, f"Routing {key} {m} is not pulled",
                                  f"run: ollama pull {m}  (or set it in config/model_routing.json)"))
    return out


def _cuda_dlls():
    """Where the pip-installed CUDA maths libraries are, or the names that are missing."""
    try:
        import nvidia
    except ImportError:
        return None, ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"]
    found = {}
    for base in getattr(nvidia, "__path__", []):
        for dll in Path(base).glob("*/bin/*.dll"):
            found.setdefault(dll.name.lower(), dll)
    missing = [pkg for pkg, prefix in (("nvidia-cublas-cu12", "cublas64_"), ("nvidia-cudnn-cu12", "cudnn64_"))
               if not any(n.startswith(prefix) for n in found)]
    return found, missing


def check_gpu():
    _, missing = _cuda_dlls()
    if missing:
        return [Result(WARN, "CUDA libraries for Whisper are missing (it will use the CPU)",
                       "run: pip install -e .[gpu]")]
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
    except Exception as e:                       # intentional: a missing extra is reported, not raised
        return [Result(WARN, f"Can't ask ctranslate2 about the GPU ({type(e).__name__})", "run: pip install -e .[voice]")]
    if n < 1:
        return [Result(WARN, "No NVIDIA GPU visible to Whisper (it will use the CPU)", "update the NVIDIA driver")]
    return [Result(OK, f"GPU ready for Whisper ({n} CUDA device{'s' * (n > 1)})")]


def check_kokoro():
    missing = [f for f in NEURAL_FILES if not (NEURAL_DIR / f).is_file()]
    out = []
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        out.append(Result(WARN, "kokoro-onnx isn't installed (Windows voice instead)", "run: pip install -e .[voice]"))
    if missing:
        out.append(Result(WARN, f"Kokoro voice files missing in {NEURAL_DIR}: {', '.join(missing)}",
                          "download them (README: Personality and voice)"))
    return out or [Result(OK, "Kokoro voice is installed")]


def check_microphone():
    try:
        import sounddevice as sd
    except Exception as e:                       # intentional: ImportError, or PortAudio missing (OSError)
        return [Result(WARN, f"sounddevice can't load ({type(e).__name__}): no voice input", "run: pip install -e .[voice]")]
    try:
        dev = sd.query_devices(kind="input")
    except Exception:                            # intentional: no default input device raises
        return [Result(WARN, "No microphone found", "plug one in, or check Windows privacy settings for the mic")]
    return [Result(OK, f"Microphone: {dev.get('name', '?')}")]


def _webview2_version():
    import winreg
    for hive, key in WEBVIEW2_KEYS:
        try:
            with winreg.OpenKey(getattr(winreg, hive), key) as k:
                v = winreg.QueryValueEx(k, "pv")[0]
                if v and v != "0.0.0.0":
                    return v
        except OSError:
            continue
    return None


def check_webview2():
    try:
        v = _webview2_version()
    except ImportError:
        return [Result(WARN, "Not Windows: can't check WebView2")]
    if not v:
        return [Result(FAIL, "WebView2 runtime not found (the chat window and 3D Nova need it)",
                       "install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703")]
    return [Result(OK, f"WebView2 runtime {v}")]


def check_data_folder():
    from nova import store
    root = Path(store.current().root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root, prefix=".doctor-", delete=True) as f:
            f.write(b"ok")
    except OSError as e:
        return [Result(FAIL, f"Can't write to the data folder {root} ({e.strerror or e})",
                       "check the folder's permissions, or point NOVA_DATA somewhere writable")]
    return [Result(OK, f"Data folder is writable ({root})")]


CHECKS = [check_ollama, check_gpu, check_kokoro, check_microphone, check_webview2, check_data_folder]


def run(checks=None):
    out = []
    for check in checks or CHECKS:
        try:
            out += check()
        except Exception as e:                   # intentional: one broken check must not hide the others
            out.append(Result(WARN, f"{check.__name__} crashed: {type(e).__name__}: {e}"))
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    quiet = "--quiet" in argv
    results = run()
    shown = [r for r in results if not quiet or r.status != OK]
    for r in shown:
        print(r.line())
    failed = sum(r.status == FAIL for r in results)
    if not quiet:
        warned = sum(r.status == WARN for r in results)
        print(f"\n{len(results) - failed - warned} ok, {warned} warning(s), {failed} problem(s).")
    return 1 if failed else 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
