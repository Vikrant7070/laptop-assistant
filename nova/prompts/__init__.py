"""What Nova tells her models: the long prompt texts, one .md file each (roadmap R15).

`load(name)` gives the text of `<name>.md`; `load(name, **values)` fills its `{placeholders}` with str.format,
the way the code always filled them (so a literal brace in a file is written `{{`). Personas stay in
`characters/<id>/persona.md`; tool descriptions stay with their `@tool`.

These texts are part of the prompt cache and of how well she does her job: tests/test_prompt_files.py pins each
one by hash, and changing one needs a task exam run (`python -m nova.exam`, the user starts it) before and after.

A file holds the text exactly, plus one final newline that `load` drops. Line endings are read as \n whatever
git checked out.
"""
from functools import cache
from pathlib import Path

HERE = Path(__file__).parent


@cache
def text(name):
    raw = (HERE / f"{name}.md").read_bytes().decode("utf-8").replace("\r\n", "\n")
    return raw[:-1] if raw.endswith("\n") else raw


def load(prompt, /, **values):
    """The prompt `prompt`, with `values` filled in when given (positional-only, so a `{name}` placeholder works)."""
    return text(prompt).format(**values) if values else text(prompt)


def names():
    return sorted(p.stem for p in HERE.glob("*.md"))
