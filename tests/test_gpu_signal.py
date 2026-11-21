"""Nova goes first on the graphics card: while her local brain works, the signal file asks training to wait."""
import json
import threading
import time

from nova.brain import gpu_signal, llm


def until():
    return json.loads(gpu_signal.path().read_text(encoding="utf-8"))["until"]


def test_tests_never_touch_the_real_signal(tmp_path):
    assert str(gpu_signal.path()).startswith(str(tmp_path))     # conftest moves it: a test never pauses real training


def test_busy_marks_the_card_and_lingers_after(monkeypatch):
    assert not gpu_signal.wanted()
    with gpu_signal.busy():
        assert gpu_signal.wanted() and until() <= time.time() + gpu_signal.IN_FLIGHT + 1
    left = until() - time.time()
    assert gpu_signal.LINGER - 2 < left <= gpu_signal.LINGER         # kept for the next step of the same task
    assert gpu_signal.wanted(now=time.time() + gpu_signal.LINGER + 1) is False   # ...then the card is free again


def test_overlapping_requests_linger_only_after_the_last(monkeypatch):
    monkeypatch.setattr(gpu_signal, "LINGER", 1000)
    monkeypatch.setattr(gpu_signal, "IN_FLIGHT", 5)
    first_done, second_go = threading.Event(), threading.Event()

    def other():
        with gpu_signal.busy():
            second_go.set()
            first_done.wait(5)
    t = threading.Thread(target=other)
    t.start()
    second_go.wait(5)
    with gpu_signal.busy():
        pass                                             # this one ends while the other is still running
    assert until() < time.time() + 100                   # not the linger yet: a request is still in flight
    first_done.set()
    t.join()
    assert until() > time.time() + 900                   # the last one ended: now it lingers


def test_a_broken_or_missing_file_means_free(tmp_path):
    gpu_signal.path().parent.mkdir(parents=True, exist_ok=True)
    gpu_signal.path().write_text("{half", encoding="utf-8")
    assert not gpu_signal.wanted()


def test_local_brain_calls_raise_it_and_cloud_calls_do_not(monkeypatch):
    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"message": {"content": "hi"}}).encode()
    seen = []
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout=0: (seen.append(gpu_signal.wanted()), Reply())[1])
    llm._post("/api/chat", {"model": "m"})
    assert seen == [True] and gpu_signal.wanted()        # while it ran, and lingering after
    gpu_signal.path().unlink()
    monkeypatch.setattr(llm.cloud, "chat", lambda *a, **k: ({"role": "assistant", "content": "hi"}, "hi", []))
    llm.chat("cloud:gemini:gemini-2.5-flash", [{"role": "user", "content": "hi"}], [])
    assert not gpu_signal.wanted()                       # a cloud brain doesn't use the card
