# 0004 Every data path comes from `store`

Status: accepted (Part B, 2026-11)

## Context
Modules built their own `data/...` path constants at import time, so redirecting Nova's data (for tests, or a second
profile) meant patching a dozen module globals, and a missed one wrote into the user's real data.

## Decision
`nova/store.py` holds one `Store` with every file and folder under the data folder (`data/` or `NOVA_DATA`).
Code asks for paths when it needs them: `store.current().settings`, `store.current().file(name)`. Tests install
their own with `store.use(store.Store(tmp))` (`conftest.py` does it for the whole suite). The skills folder and
`config/model_routing.json` are on the Store too, but live with the project because the user edits them.

## Consequences
- No module keeps a data path in a global.
- The suite is hermetic: it never touches the user's `data/`.
