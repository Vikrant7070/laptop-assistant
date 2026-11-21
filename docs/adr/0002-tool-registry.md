# 0002 Tools are declared once, with `@tool`

Status: accepted (Part B, PR #16, 2026-11-21)

## Context
A tool used to be spread over hand-written tables (schemas, friendly labels, risky set, core list, hints, groups),
which drifted apart.

## Decision
Every tool is one function with `@tool(description, friendly=..., risky=..., ask=..., dangerous=..., core=...,
group=..., hints=..., props=...)` (`nova/actions/registry.py`). The decorator registers it, and `tools`, `toolpick`
and `app` build their tables from the registrations. There are no hand-written tables.

## Consequences
- Adding a tool means one function in its area module under `nova/actions/tools/`, plus a case in
  `tests/routing.jsonl`.
- Changing a tool's approval tier or picking is a one-line change in its decorator.
