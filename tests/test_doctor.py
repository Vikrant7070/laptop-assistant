"""`python -m nova --doctor` (roadmap R4): every check faked as passing and as failing."""
import json
import sys
import types

import pytest

from nova import __main__ as entry, doctor, store
from nova.brain import llm


@pytest.fixture
def all_good(monkeypatch, tmp_path):
    """A laptop where everything is installed."""
    monkeypatch.setattr(llm, "list_models", lambda: ["qwen3-vl:4b-instruct", "qwen2.5-coder:7b", "deepseek-r1:8b",
                                                     "qwen3:8b"])
    monkeypatch.setattr(doctor, "_cuda_dlls", lambda: ({"cublas64_12.dll": 1, "cudnn64_9.dll": 1}, []))
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))
    monkeypatch.setitem(sys.modules, "kokoro_onnx", types.ModuleType("kokoro_onnx"))
    monkeypatch.setattr(doctor, "NEURAL_DIR", tmp_path)
    for f in doctor.NEURAL_FILES:
        (tmp_path / f).write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "sounddevice",
                        types.SimpleNamespace(query_devices=lambda kind=None: {"name": "Test mic"}))
    monkeypatch.setattr(doctor, "_webview2_version", lambda: "140.0.1")


def _status(results):
    return {r.what: r.status for r in results}


def test_everything_green(all_good, capsys):
    results = doctor.run()
    assert all(r.status == doctor.OK for r in results), [r.line() for r in results]
    assert doctor.main([]) == 0
    out = capsys.readouterr().out
    assert "0 problem(s)" in out and "Test mic" in out


def test_quiet_prints_nothing_when_all_is_well(all_good, capsys):
    assert doctor.main(["--quiet"]) == 0
    assert capsys.readouterr().out == ""


def test_ollama_down_is_a_failure(all_good, monkeypatch, capsys):
    monkeypatch.setattr(llm, "list_models", lambda: None)
    assert doctor.main(["--quiet"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] Ollama isn't answering" in out and "ollama.com" in out


def test_missing_model_says_how_to_pull_it(all_good, monkeypatch):
    s = store.current().settings
    s.parent.mkdir(parents=True, exist_ok=True)
    s.write_text(json.dumps({"model": "llama3.2:3b"}), encoding="utf-8")
    r = {x.what: x for x in doctor.check_ollama()}
    assert r["Model llama3.2:3b is not pulled"].fix == "run: ollama pull llama3.2:3b"
    assert r["Model llama3.2:3b is not pulled"].status == doctor.FAIL


def test_cloud_brain_needs_no_local_model(all_good, monkeypatch):
    s = store.current().settings
    s.parent.mkdir(parents=True, exist_ok=True)
    s.write_text(json.dumps({"model": "cloud:google:gemini-x"}), encoding="utf-8")
    assert all(x.status == doctor.OK for x in doctor.check_ollama())


def test_missing_routing_model_is_a_warning(all_good, monkeypatch):
    monkeypatch.setattr(llm, "list_models", lambda: ["qwen3-vl:4b-instruct"])
    monkeypatch.setattr(doctor, "_routing", lambda: {"auto_route": True, "coder_model": "qwen2.5-coder:7b"})
    st = _status(doctor.check_ollama())
    assert st["Routing coder_model qwen2.5-coder:7b is not pulled"] == doctor.WARN


def test_no_cuda_libraries_is_a_warning(all_good, monkeypatch):
    monkeypatch.setattr(doctor, "_cuda_dlls", lambda: (None, ["nvidia-cublas-cu12"]))
    [r] = doctor.check_gpu()
    assert r.status == doctor.WARN and "[gpu]" in r.fix


def test_no_gpu_device_is_a_warning(all_good, monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 0))
    assert doctor.check_gpu()[0].status == doctor.WARN


def test_kokoro_files_missing(all_good, monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "NEURAL_DIR", tmp_path / "empty")
    [r] = doctor.check_kokoro()
    assert r.status == doctor.WARN and "kokoro-v1.0.int8.onnx" in r.what


def test_no_microphone(all_good, monkeypatch):
    def none(kind=None):
        raise ValueError("No input device matching")
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(query_devices=none))
    assert doctor.check_microphone()[0].status == doctor.WARN


def test_no_webview2_is_a_failure(all_good, monkeypatch):
    monkeypatch.setattr(doctor, "_webview2_version", lambda: None)
    [r] = doctor.check_webview2()
    assert r.status == doctor.FAIL and "fwlink" in r.fix


def test_unwritable_data_folder(all_good, monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(store, "_current", store.Store(blocker / "data"), raising=False)
    monkeypatch.setattr(store, "current", lambda: store.Store(blocker / "data"))
    [r] = doctor.check_data_folder()
    assert r.status == doctor.FAIL and "NOVA_DATA" in r.fix


def test_a_crashing_check_does_not_hide_the_others(all_good):
    def boom():
        raise RuntimeError("bad")
    results = doctor.run([boom, doctor.check_webview2])
    assert results[0].status == doctor.WARN and "boom crashed" in results[0].what
    assert results[1].status == doctor.OK


def test_entry_point_runs_the_doctor_without_starting_nova(all_good, monkeypatch):
    monkeypatch.setitem(sys.modules, "nova.core.desktop", None)     # importing the desktop would fail the test
    assert entry.main(["--doctor", "--quiet"]) == 0


def test_constants_match_their_homes():
    from nova.audio import voice
    from nova.core.app import config
    assert doctor.DEFAULT_MODEL == config.DEFAULT_MODEL
    assert doctor.NEURAL_FILES == (voice.NEURAL_MODEL, voice.NEURAL_VOICES)
    assert doctor.NEURAL_DIR == voice.NEURAL_DIR
