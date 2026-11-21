"""Test harness for Nova.

Everything that would touch the real laptop or the user's live Nova is stubbed:
  - data/ is redirected to a temp folder (NOVA_DATA), so settings, knowledge and keys are never touched
  - the voice (Mouth), Whisper and the 3D body (avatar_client) are replaced with silent fakes
  - the LLM and the tools are scripted per test (see FakeBrain)
Run:  python -m pytest -q
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_DATA = Path(tempfile.mkdtemp(prefix="nova_test_data_"))
os.environ["NOVA_DATA"] = str(_DATA)
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)

from nova.avatar import avatar_client  # noqa: E402

avatar_client.send = lambda *a, **k: None
avatar_client.running = lambda: False

from nova.audio import voice  # noqa: E402


class FakeMouth:
    voice_name = "fake"
    lips = False

    def __init__(self):
        self.said = []
        self.hidden = []       # said with show=False: out loud only, no bubble
        self.on_start = None

    def say(self, text, show=True):
        self.said.append(text)
        if not show:
            self.hidden.append(text)

    def stop(self, quick=False):
        pass

    def hold(self, secs):
        pass

    def talking(self):
        return False

    def busy(self):
        return False


voice.RealMouth = voice.Mouth     # tests of the real voice class use this
voice.Mouth = FakeMouth
voice.WhisperTranscriber.available = staticmethod(lambda: False)

from nova import store  # noqa: E402
from nova.actions import tools  # noqa: E402
from nova.brain import router  # noqa: E402
from nova.core import app  # noqa: E402
from nova.actions import skills  # noqa: E402
from nova.actions import checks  # noqa: E402
from nova.actions import recorder  # noqa: E402
from nova import log  # noqa: E402

recorder.ENABLED = False          # never install real mouse/keyboard hooks in tests (tests patch recorder's functions)

checks.real_evaluate = checks.evaluate     # the autouse fixture below replaces evaluate; tests of the checks use this


def _point_data_at(folder):
    """One Store for the test: every data file, the skills folder and the routing file live in `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    store.use(store.Store(folder, skills_dir=folder / "skills", routing=folder / "model_routing.json"))
    log.reset()
    router._cache.update(key=None, conf=None)
    skills._cache.update(key=None, skills=[])
    app.learn_state.update(last=0.0)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Fresh data folder and conversation for every test; never touch real windows."""
    _point_data_at(tmp_path / "data")
    app.state.clear()
    app.state.update({"messages": [], "pending": None})
    app.events.clear()
    monkeypatch.setattr(tools.keyboard, "send", lambda *a, **k: None)     # never press real keys
    monkeypatch.setattr(tools.keyboard, "write", lambda *a, **k: None)
    # typing goes in by pasting: a pretend clipboard, so tests never touch the real one
    monkeypatch.setattr(tools, "_clip_save", lambda: [])
    monkeypatch.setattr(tools, "_clip_set_text", lambda text: 1)
    monkeypatch.setattr(tools, "_clip_restore", lambda saved, seq: None)
    monkeypatch.setattr(tools, "_focused_text", lambda: None)
    monkeypatch.setattr(tools.webbrowser, "open", lambda *a, **k: True)  # never open real browser tabs
    monkeypatch.setattr(tools, "open_windows_summary", lambda limit=6: "")
    monkeypatch.setattr(tools, "_windows", lambda: [])      # an empty pretend desktop (a test adds its own windows)
    monkeypatch.setattr(tools, "_screen_allowed", lambda: False)
    monkeypatch.setattr(app, "react_later", lambda *a, **k: None)
    monkeypatch.setattr(app, "review_turn", lambda why: None)
    # skill step checks look at the real desktop: a test says what they find (or patches checks.evaluate itself)
    monkeypatch.setattr(checks, "evaluate", lambda *a, **k: (True, "ok (test)"))
    # the Judge's "which checks prove this?" brain call would eat a scripted FakeBrain answer: none fit by default
    monkeypatch.setattr(app, "ask_for_checks", lambda goal, windows, model: "NONE")
    # Phone Link's banner switch lives in the real registry: tests only record what would have been asked
    app.banner_calls = []
    monkeypatch.setattr(app.notifications, "phone_banners", lambda show, **k: (app.banner_calls.append(show), 1)[1])
    # background co-worker jobs, naps and offers start fresh, and never open real windows
    from nova.actions import coworker
    coworker.jobs.clear()

    def no_real_windows(*a, **k):
        raise AssertionError("a test tried to open a real window")
    monkeypatch.setattr(coworker, "open_hidden", no_real_windows)
    app.nap.update(asleep=False, reason=None, last_task=__import__("time").time())
    app.offer.update(job=None, until=0.0)
    app.offers_waiting.clear()
    app.convo.update(on=False, last_heard=0.0, thinking=False)
    app.listen_state.update(loop=False, single=False)
    app.mouth.said.clear()
    app.mouth.hidden.clear()
    app.llm.reset_cloud()
    yield tmp_path / "data"


class FakeBrain:
    """Scripted model: each chat() call pops the next (text, calls) pair."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def __call__(self, model, messages, tools_, think=False, on_text=None, fallback=None, on_think=None, **kw):
        self.seen.append(messages)
        text, calls = self.script.pop(0) if self.script else ("Done!", [])
        if isinstance(text, Exception):
            raise text
        calls = [{"id": f"c{i}", "name": n, "args": a} for i, (n, a) in enumerate(calls)]
        stored = {"role": "assistant", "content": text}
        if calls:
            stored["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["args"]}} for c in calls]
        if on_text and text:
            on_text(text)
        app.llm.LAST["fallback"] = None
        return stored, text, calls


@pytest.fixture
def brain(monkeypatch):
    """brain(script) installs a FakeBrain and a tool recorder; returns (fake, ran_tools)."""
    ran = []

    def install(script, tool_output="ok"):
        fake = FakeBrain(script)
        monkeypatch.setattr(app.llm, "chat", fake)
        monkeypatch.setattr(app.llm, "supports_tools", lambda m: True)
        monkeypatch.setattr(app.tools, "run_tool", lambda n, a: (ran.append((n, a)), tool_output)[1])
        monkeypatch.setattr(app, "settle", lambda *a, **k: "")
        return fake, ran
    return install


@pytest.fixture
def client():
    app.app.config["TESTING"] = True
    return app.app.test_client()
