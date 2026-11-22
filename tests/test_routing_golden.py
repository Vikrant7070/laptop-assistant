"""Golden routing checks: what a request turns into, before any model runs.

`tests/routing.jsonl` holds cases pulled from the real request log (data/task_log.txt) and from the
tool descriptions: a request, the tool(s) it must reach, the skill it must start, and the route
(code / deep / question) it must take, and what each user-intent regex (nova/intents.py) must and must not
hear. They are the contract the Phase 1-3 refactors must not break.

The embedding model is optional (downloaded on first use), so the tool and skill matchers fall back
to shared words when it is not there. These tests pin that fallback: they are deterministic on any
machine, online or offline. A second test below checks the same tool cases with embeddings when
they are available, and skips otherwise.
"""
import json
from pathlib import Path

import pytest

from nova import intents, store
from nova.actions import skills, tools
from nova.brain import retrieval, router, toolpick

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).with_name("routing.jsonl")
CASES = [c for c in (json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip())]


def _ids(case):
    kind = case["kind"]
    if kind == "tool":
        return f"tool: {case['query']}"
    if kind == "skill":
        return f"skill: {case['query']}"
    if kind == "intent":
        return f"intent {case['intent']}: {case['query']}"
    return f"route: {case['query']}"


@pytest.fixture(autouse=True)
def lexical(monkeypatch):
    """Pin the shared-word fallback so CI does not depend on the downloaded embedding model."""
    monkeypatch.setattr(retrieval, "ready", lambda: False)


@pytest.fixture
def repo_skills():
    """conftest redirects store.current().skills_dir to a temp folder; these cases describe the skills shipped in the repo."""
    store.current().skills_dir = ROOT / "skills"
    skills.library._cache.update(key=None, skills=[])
    yield
    skills.library._cache.update(key=None, skills=[])


def _tools_cases():
    return [pytest.param(c, id=_ids(c)) for c in CASES if c["kind"] == "tool"]


def _skill_cases():
    return [pytest.param(c, id=_ids(c)) for c in CASES if c["kind"] == "skill"]


def _route_cases():
    return [pytest.param(c, id=_ids(c)) for c in CASES if c["kind"] == "route"]


def _intent_cases():
    return [pytest.param(c, id=_ids(c)) for c in CASES if c["kind"] == "intent"]


def test_golden_file_is_well_formed():
    assert len(CASES) >= 40
    for case in CASES:
        assert case["kind"] in ("tool", "skill", "route", "intent"), case
        assert case["query"].strip(), case
        if case["kind"] == "tool":
            assert case["must"] and all(isinstance(n, str) for n in case["must"]), case
        elif case["kind"] == "skill":
            assert "skill" in case and "how" in case, case
        elif case["kind"] == "intent":
            assert case["intent"] in intents.KINDS or case["intent"] in intents.TABLES, case
            assert "match" in case, case
        else:
            assert isinstance(case["code"], bool) and isinstance(case["deep"], bool) and isinstance(case["question"], bool), case


@pytest.mark.parametrize("case", _tools_cases())
def test_request_reaches_its_tool(case):
    picked = toolpick.pick(case["query"])
    missing = [name for name in case["must"] if name not in picked]
    assert not missing, f"{case['query']!r} did not pick {missing}; got {picked}"


@pytest.mark.parametrize("case", _skill_cases())
def test_request_reaches_its_skill(case, repo_skills):
    skill, how = skills.pick(case["query"])
    assert (skill or {}).get("name") == case["skill"], f"{case['query']!r}: {skills.last_decision}"
    if case["skill"] is not None:
        assert how == case["how"], f"{case['query']!r}: {skills.last_decision}"


@pytest.mark.parametrize("case", _route_cases())
def test_request_takes_its_route(case):
    got = (router.code_wanted(case["query"]), router.deep_wanted(case["query"]), router.code_question(case["query"]))
    assert got == (case["code"], case["deep"], case["question"]), case["note"]


@pytest.mark.parametrize("case", _intent_cases())
def test_user_words_mean_their_intent(case):
    assert intents.match(case["intent"], case["query"]) == case["match"], case["note"]


def test_every_intent_has_golden_cases():
    """At least five cases per intent, and at least one near-miss, so a loosened pattern shows up here."""
    for kind in [*intents.KINDS, *intents.TABLES]:
        cases = [c for c in CASES if c["kind"] == "intent" and c["intent"] == kind]
        assert len(cases) >= 5, kind
        assert any(c["match"] in (False, None) for c in cases), kind
        assert any(c["match"] not in (False, None) for c in cases), kind


def test_every_pick_keeps_the_core_tools():
    for case in CASES:
        if case["kind"] != "tool":
            continue
        picked = set(toolpick.pick(case["query"]))
        assert set(toolpick.CORE) <= picked, f"{case['query']!r}: {set(toolpick.CORE) - picked}"


def test_pick_is_deterministic_and_in_schema_order():
    """Same request twice -> identical prompt (Ollama's cache is per prefix), and an order that never drifts."""
    order = [t["name"] for t in tools.TOOLS]
    for case in CASES:
        if case["kind"] != "tool":
            continue
        first, second = toolpick.pick(case["query"]), toolpick.pick(case["query"])
        assert first == second
        assert first == sorted(first, key=order.index), case["query"]


def test_every_picked_tool_has_a_schema():
    for case in CASES:
        if case["kind"] != "tool":
            continue
        for name in toolpick.pick(case["query"]):
            assert toolpick.schemas([name]), f"{case['query']!r} picked {name} with no schema"


def test_tool_set_stays_small():
    """The prompt budget is the reason toolpick exists: a long list makes every reply slower on the 6 GB GPU."""
    for case in CASES:
        if case["kind"] != "tool":
            continue
        picked = toolpick.pick(case["query"])
        assert len(picked) <= 45, f"{case['query']!r} picked {len(picked)} tools: {picked}"


@pytest.mark.skipif(not retrieval.ready(), reason="embedding model not downloaded")
def test_same_tool_cases_with_embeddings():
    """When the real model is available, the same requests must still reach their tools."""
    for case in CASES:
        if case["kind"] != "tool":
            continue
        picked = toolpick.pick(case["query"])
        missing = [name for name in case["must"] if name not in picked]
        assert not missing, f"{case['query']!r} did not pick {missing} with embeddings; got {picked}"
