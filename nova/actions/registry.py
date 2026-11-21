"""One declaration per tool.

Adding a tool used to mean editing up to six tables in three files: the schema in tools.TOOLS, the function in
tools.FUNCS, the approval tier in tools.RISKY / DANGEROUS, the activity label in app.FRIENDLY, the Allow / No label
in app.ASK, and toolpick's CORE / GROUPS / HINTS. Forget one and the tool is invisible, unlabelled or unguarded.

A tool declared with @tool is put into every one of those tables:

    @tool("Read the battery level.", friendly="Checked the battery", hints="battery charge power left")
    def battery_level():
        ...

    @tool("Delete a file for good.", friendly="Deleted a file", risky="Delete a file for good", dangerous=True,
          props={"path": {"type": "string", "description": "the file"}})
    def delete_for_good(path):
        ...

The schema's properties come from `props` when given (needed for enums and argument descriptions), otherwise from
the function's type hints (str, int, float, bool). Required = the parameters without a default.

The tables are filled by "sinks": each module that owns a table registers one with on_register(), and it receives
every tool declared before and after. That way this module imports nothing from Nova and the tables stay where
their readers expect them. The hand-written tables are still there; tools move here over time (Part B, Step D).
"""
import inspect
from dataclasses import dataclass

_TYPES = {str: "string", int: "number", float: "number", bool: "boolean"}


@dataclass(frozen=True)
class Spec:
    name: str
    description: str
    fn: object
    props: dict
    required: tuple
    friendly: str
    ask: str = ""              # the Allow / No question; set for risky tools
    risky: bool = False        # needs an OK under "careful" autonomy
    dangerous: bool = False    # needs an OK under "trusted" too
    core: bool = False         # always offered to the local brain
    group: tuple = ()          # tools it is usually used with (toolpick brings them along)
    hints: str = ""            # words people say when they want it

    def schema(self):
        """The same shape as tools._tool()."""
        return {"name": self.name, "description": self.description,
                "input_schema": {"type": "object", "properties": dict(self.props), "required": list(self.required),
                                 "additionalProperties": False}}


_specs = {}        # name -> Spec, in declaration order
_sinks = []


def _infer(fn):
    props, required = {}, []
    for p in inspect.signature(fn).parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        kind = _TYPES.get(p.annotation)
        if kind is None:
            raise TypeError(f"{fn.__name__}: give '{p.name}' a type hint (str, int, float, bool) or pass props=")
        props[p.name] = {"type": kind}
        if p.default is p.empty:
            required.append(p.name)
    return props, required


def tool(description, *, friendly, name=None, props=None, required=None, risky="", dangerous=False, core=False,
         group=(), hints=""):
    """Declare a tool. `risky` is the Allow / No question ("Delete a file for good"); a risky tool must have one, and
    a dangerous tool is always risky too."""
    if dangerous and not risky:
        raise ValueError("a dangerous tool needs risky='<the Allow / No question>'")

    def deco(fn):
        tool_name = name or fn.__name__
        if tool_name in _specs:
            raise ValueError(f"tool {tool_name!r} is declared twice")
        if props is None:
            p, req = _infer(fn)
        else:
            params = inspect.signature(fn).parameters
            unknown = set(props) - set(params)
            if unknown:
                raise TypeError(f"{tool_name}: props {sorted(unknown)} are not parameters")
            p = props
            req = [k for k in props if params[k].default is inspect.Parameter.empty] if required is None else required
        spec = Spec(tool_name, description, fn, p, tuple(req), friendly, ask=risky or "", risky=bool(risky),
                    dangerous=dangerous, core=core, group=tuple(group), hints=hints)
        _specs[tool_name] = spec
        for sink in list(_sinks):
            sink(spec)
        return fn
    return deco


def on_register(sink):
    """Call sink(spec) for every tool declared so far and every one declared later."""
    _sinks.append(sink)
    for spec in list(_specs.values()):
        sink(spec)
    return sink


def specs():
    """Every declared tool, in declaration order."""
    return list(_specs.values())
