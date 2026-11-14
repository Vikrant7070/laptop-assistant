"""The tool registry, approval tiers and UI labels must agree with each other."""
import inspect

import app
import avatar
import tools
import shared


def test_every_tool_has_a_function_with_matching_params():
    for t in tools.TOOLS:
        fn = tools.FUNCS[t["name"]]
        params = inspect.signature(fn).parameters
        for p in t["input_schema"]["properties"]:
            assert p in params, f"{t['name']}: schema arg '{p}' is not a parameter"
        for r in t["input_schema"]["required"]:
            assert r in t["input_schema"]["properties"], f"{t['name']}: required '{r}' not in properties"


def test_tool_names_unique():
    names = [t["name"] for t in tools.TOOLS]
    assert len(names) == len(set(names))


def test_every_tool_has_a_friendly_label():
    missing = {t["name"] for t in tools.TOOLS} - set(app.FRIENDLY)
    assert not missing, f"no FRIENDLY label: {missing}"
    stale = set(app.FRIENDLY) - {t["name"] for t in tools.TOOLS}
    assert not stale, f"FRIENDLY labels for tools that don't exist: {stale}"


def test_every_risky_tool_has_an_ask_label():
    assert tools.RISKY <= set(app.ASK), f"no ASK label: {tools.RISKY - set(app.ASK)}"
    assert set(app.ASK) <= {t["name"] for t in tools.TOOLS}


def test_dangerous_is_subset_of_risky():
    assert tools.DANGEROUS <= tools.RISKY


def test_act_tools_exist():
    assert app.ACT_TOOLS <= {t["name"] for t in tools.TOOLS}


def test_reactions_defined_once():
    assert tools.REACTIONS is shared.REACTIONS
    assert avatar.REACTIONS is shared.REACTIONS


def test_run_tool_unknown_and_extra_args():
    assert "no such tool" in tools.run_tool("format_drive", {})
    assert tools.run_tool("get_datetime", {"bogus": 1})   # extras are dropped, not an error


def test_run_tool_reports_exceptions():
    assert tools.run_tool("set_brightness", {"percent": "abc"}).startswith("Error in set_brightness")


def test_tool_count_matches_readme_claim():
    assert len(tools.TOOLS) >= 50
