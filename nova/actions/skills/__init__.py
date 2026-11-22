"""Skills: new abilities for Nova written as small text files instead of Python.

Each skill is a Markdown file in skills/ (NOVA_SKILLS points elsewhere; the tests use a temp folder).

Format 2 (the one to write; Nova's own saves use it):

    ---
    name: anime-time
    format: 2
    version: 3
    description: Sets up Crunchyroll for watching - dark mode, open the site, sign in if needed, fullscreen.
    examples: anime time, set up crunchyroll, crunchyroll fullscreen
    not_for: check the latest anime, which anime should i watch
    requires_any: anime time, crunchyroll
    risk: disruptive              (read_only | disruptive | consequential; never lower than the steps need)
    invoke: confirm_if_fuzzy      (auto | confirm_if_fuzzy | explicit_only)
    status: active                (draft | active | deprecated)
    ---
    s1. set_dark_mode enabled=true
    s2. open_website url_or_search=https://www.crunchyroll.com
        expect: window_open "Crunchyroll"
        on_fail: retry 1, then stop "Crunchyroll didn't open"
    s3. if page_has "Log In" goto s4
    s4. login_if_needed window=Crunchyroll
    s5. say Crunchyroll is ready, boss!

  Steps: a tool call (`tool key=value, key=value` or `tool {"json": "args"}`), `say <text>`, `wait <secs>`,
  `if <check> goto sN` (forward only), `confirm "<question>"` (pauses and asks Allow / No), `brain "<one
  instruction>" [tools: a, b] [slot: name]` (one small model call for that step only) and `report "<instruction>"`
  (last step: the brain words the answer from the results). Indented under a step: `expect: <check>` (checked by
  code, see checks.py), `on_fail: retry N | stop "msg" | ask "q" | brain "..." | skip | goto sN` (chain them with
  commas), `note: <text>`. `{input}` / `{input_url}` are replaced by what the user asked for.

Format 1 (older files, still run): the same without ids and checks, `when:` instead of `examples:`, and a skill
whose body is plain English is a "recipe" the brain follows. Recipes are the weak spot of a small model - it skips
steps and claims success - so new skills are routines, and code runs them.

Skills only combine Nova's existing tools, so every step still goes through the same approval rules as a direct
call (app.is_risky) - a skill can never get around the "ask first" step. Side files, written only by code, never
by the model: skills/.stats/<name>.json (runs, failures by step, ...), skills/.history/ (every old version, so a
change can be undone), skills/.trash/ (deleted skills).

This was one module; it is now a package, one module per job (scripts/split_module.py). `skills.<name>`
still works: it is read from the name's home module on every access. Patch or assign a name in its home module
(`home_of(name)`), never on the package - tests/test_packages.py checks that.
"""
# Import order: each module finds what it needs at import time already there.
from nova.actions.skills import format, matching, library, stats, drafts  # noqa: F401

_MODULES = {m.__name__.rsplit(".", 1)[1]: m for m in (format, matching, library, stats, drafts,)}
_HOME = {name: _MODULES[mod] for mod, names in {
    'format': ['ACTION_RE', 'ATTR_RE', 'CONSEQUENTIAL', 'ID_RE', 'INVOKES', 'KV_RE', 'LOCKED', 'MAX_ACTIVE', 'MAX_BODY', 'MAX_STEPS', 'NAME_RE', 'QUOTED', 'READ_ONLY', 'RISKS', 'STATUSES', 'STEP_RE', '_as_v2_text', '_explicit_risk', '_fmt_value', '_front', '_list', '_phrases', '_q', '_render', '_steps', '_tool_names', '_unq', '_value', 'compute_risk', 'parse', 'parse_body_v2', 'parse_check', 'parse_on_fail', 'parse_routine', 'parse_step', 'parse_step_v2', 'render_body', 'render_check', 'render_on_fail', 'render_step', 'render_v2', 'slug', 'tool_names_of'],
    'matching': ['FILLERS', 'FUZZY_FLOOR', 'FUZZY_FLOOR_WORDS', 'FUZZY_MARGIN', 'MORE_WORK', 'STOPWORDS', 'TRAILING', '_NAMES', '_content', '_has', '_norm', '_phrase_fires', '_requires_ok', '_strip_fillers', '_vetoed', 'direct_args', 'floor', 'guess_input', 'last_decision', 'match', 'pick', 'prompt_block', 'similar'],
    'library': ['_cache', '_history_add', '_ids_for', '_next_id', '_set_front', '_step_from_op', '_write', 'delete', 'edit', 'get', 'history', 'load', 'save', 'set_status', 'undo'],
    'stats': ['_side', 'add_not_for', 'least_used', 'record_run', 'stats_get', 'stats_save', 'stats_update'],
    'drafts': ['LOOKS', 'SKIP_IN_TRACE', 'describe', 'draft_from_steps', 'fill', 'steps_from_trace'],
}.items() for name in names}


def __getattr__(name):
    """skills.<name> -> the current value in its home module (so a patch there is seen by every caller)."""
    mod = _HOME.get(name)
    if mod is None:
        for m in _MODULES.values():          # a module one of them imports (avatar_client, ...)
            if name in vars(m):
                return vars(m)[name]
        raise AttributeError(f"module 'nova.actions.skills' has no attribute {name!r}")
    return getattr(mod, name)


def home_of(name):
    """The module that owns `name`, or None."""
    return _HOME.get(name)
