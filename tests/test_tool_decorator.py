"""One declaration per tool (nova/actions/registry.py): a tool declared with @tool is in every table that matters -
the schema list, run_tool, approval, the activity and Allow / No labels, and tool picking - without editing any."""
import copy

import pytest

from nova.actions import registry, tools
from nova.brain import retrieval, toolpick
from nova.core import app

TABLES = ((tools, ("TOOLS", "FUNCS", "RISKY", "DANGEROUS")), (app, ("FRIENDLY", "ASK")),
          (toolpick, ("CORE", "GROUPS", "HINTS")))


@pytest.fixture(autouse=True)
def fresh_tables(monkeypatch):
    """Copies of every table, so the tools these tests declare vanish afterwards."""
    monkeypatch.setattr(registry, "_specs", dict(registry._specs))
    for module, names in TABLES:
        for name in names:
            monkeypatch.setattr(module, name, copy.copy(getattr(module, name)))
    monkeypatch.setattr(retrieval, "ready", lambda: False)      # word overlap, as in the routing golden set


def test_canary_one_decorator_is_enough():
    @registry.tool("Count the rubber ducks on the desk.", friendly="Counted the ducks",
                   hints="ducks rubber duck how many ducks", group=("list_folder",))
    def count_ducks(colour: str, limit: int = 3):
        return f"{limit} {colour} ducks"

    schema = next(t for t in tools.TOOLS if t["name"] == "count_ducks")
    assert schema["input_schema"] == {"type": "object", "properties": {"colour": {"type": "string"},
                                                                         "limit": {"type": "number"}},
                                      "required": ["colour"], "additionalProperties": False}
    assert tools.run_tool("count_ducks", {"colour": "yellow"}) == "3 yellow ducks"
    assert app.FRIENDLY["count_ducks"] == "Counted the ducks"
    assert "count_ducks" not in tools.RISKY and "count_ducks" not in app.ASK
    assert "count_ducks" in toolpick.pick("how many rubber ducks are on my desk")
    assert {"count_ducks", "list_folder"} in toolpick.GROUPS
    assert "count_ducks" in {t["name"] for t in toolpick.schemas(toolpick.pick("how many rubber ducks"))}
    assert "count_ducks" in {t["name"] for t in app.active_tools("cloud:google:gemini-test")}   # a cloud brain gets all


def test_risky_and_dangerous_tools_are_guarded():
    @registry.tool("Shred a file so it can't be recovered.", friendly="Shredded a file",
                   risky="Shred a file for good", dangerous=True,
                   props={"path": {"type": "string", "description": "the file to shred"}})
    def shred_file(path):
        return f"shredded {path}"

    assert {"shred_file"} <= tools.RISKY and {"shred_file"} <= tools.DANGEROUS
    assert tools.is_dangerous("shred_file", {})
    assert app.ask_label("shred_file") == "Shred a file for good"
    assert app.is_risky({"name": "shred_file", "args": {"path": "x"}})
    schema = next(t for t in tools.TOOLS if t["name"] == "shred_file")["input_schema"]
    assert schema["properties"]["path"]["description"] == "the file to shred" and schema["required"] == ["path"]


def test_core_tools_are_always_offered():
    @registry.tool("Say which day of the week it is.", friendly="Checked the day", core=True)
    def weekday():
        return "Saturday"

    assert "weekday" in toolpick.CORE and "weekday" in toolpick.pick("hello there")


def test_mistakes_are_caught_when_declaring():
    def get_datetime():                                              # one of Nova's own tools has this name
        return ""
    with pytest.raises(ValueError, match="declared twice"):
        registry.tool("Another clock.", friendly="Checked")(get_datetime)

    with pytest.raises(ValueError, match="once"):
        registry.tool("Both.", friendly="Both", risky="Do it", ask="Do it")

    def twice():
        return ""
    registry.tool("Once.", friendly="Once")(twice)
    with pytest.raises(ValueError, match="declared twice"):
        registry.tool("Twice.", friendly="Twice")(twice)

    with pytest.raises(ValueError, match="dangerous tool needs"):
        registry.tool("Boom.", friendly="Boom", dangerous=True)

    def no_hint(path):
        return path
    with pytest.raises(TypeError, match="type hint"):
        registry.tool("No hint.", friendly="Hint")(no_hint)

    def wrong_props(path: str):
        return path
    with pytest.raises(TypeError, match="not parameters"):
        registry.tool("Wrong props.", friendly="Props", props={"file": {"type": "string"}})(wrong_props)


def test_a_table_registered_later_still_gets_every_tool():
    @registry.tool("A tool declared before the sink.", friendly="Early")
    def early_tool():
        return ""
    seen = []
    sink = registry.on_register(lambda spec: seen.append(spec.name))
    try:
        assert "early_tool" in seen
    finally:
        registry._sinks.remove(sink)
