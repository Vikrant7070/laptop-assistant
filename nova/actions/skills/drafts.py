"""Making skills from what Nova did: steps_from_trace, draft_from_steps; describe and fill."""
import json
import re
from nova import store


def describe():
    skills = library.load()
    if not skills:
        return (f"I have no skills yet. Teach me one - e.g. 'when I say movie time, close everything, open Netflix "
                f"and turn the volume up' - and I'll save it in {store.current().skills_dir}.")
    lines = []
    for s in skills:
        tag = "BROKEN - " + s["error"] if s["kind"] == "broken" else s["kind"]
        st = stats.stats_get(s["name"])
        extra = ""
        if s["status"] != "active":
            extra += f" [{s['status']}]"
        if st.get("runs"):
            extra += f" [{st.get('ok', 0)}/{st['runs']} ok]"
        lines.append(f"- {s['name']} ({tag}): {s['description'] or s['body'][:80]}" + extra
                     + (f" [say: {', '.join(s['examples'][:3])}]" if s["examples"] else ""))
    return f"My skills (files in {store.current().skills_dir}):\n" + "\n".join(lines)


def fill(args, text):
    """Replace {input} (as said) and {input_url} (made safe for a web address) in a step's args."""
    from urllib.parse import quote_plus

    def sub(v):
        return v.replace("{input_url}", quote_plus(text)).replace("{input}", text) if isinstance(v, str) else v
    return {k: sub(v) for k, v in args.items()}


# ------------------------------------------------------------------ skills from what Nova really did

SKIP_IN_TRACE = {"more_tools", "escalate", "list_skills", "react", "new_move", "go_to_sleep", "remember_fact",
                 "update_profile", "learn_lesson", "recall_knowledge", "forget_fact", "forget_move", "change_look",
                 "get_datetime", "background_jobs", "stop_background_job"}


LOOKS = {"look_at_screen", "look_through_camera", "list_clickable", "read_page", "take_screenshot", "list_windows",
         "read_selection", "get_clipboard", "get_system_status", "list_notes", "read_notifications"}


def steps_from_trace(trace, goal=""):
    """The tool calls of a finished task -> clean format-2 steps, or (None, why not). Only steps that worked; looks
    that only helped her find the way are dropped (a final look becomes a report); a click at screen coordinates
    can't be replayed, so it becomes one small brain step; the same step twice in a row is kept once."""
    from nova.actions.knowledge import SECRET
    good = [t for t in trace if t.get("ok") and t["tool"] not in SKIP_IN_TRACE and t["tool"] not in format.LOCKED]
    while good and good[0]["tool"] in LOOKS and len(good) > 1:
        good = good[1:]
    last_look = bool(good) and good[-1]["tool"] in LOOKS
    steps, prev = [], None
    for i, t in enumerate(good):
        if t["tool"] in LOOKS and not (last_look and i == len(good) - 1):
            continue
        if t["tool"] == "click_at":
            st = {"brain": f"Click the on-screen item this task needs (task: {goal[:100]})", "tools": ["list_clickable", "click_element"], "slot": ""}
        elif t["tool"] == "_brain":          # from a recording: something to do that can't be written down exactly
            st = {"brain": str(t["args"].get("instr", ""))[:200], "tools": list(t["args"].get("tools") or []), "slot": ""}
        else:
            st = {"tool": t["tool"], "args": dict(t["args"])}
        key = json.dumps(st, sort_keys=True, default=str)
        if key == prev:
            continue
        prev = key
        steps.append(st)
    if not steps:
        return None, "There was nothing in that task worth saving as steps."
    if last_look:
        steps.append({"report": "Tell the user briefly what you found."})
    steps = steps[:format.MAX_STEPS - 1]
    if SECRET.search(json.dumps(steps, ensure_ascii=False)):
        return None, "That task had a password, code, key or personal number in it, so I won't save it as a skill."
    for n, st in enumerate(steps, 1):
        st.update(id=f"s{n}", expect=None, on_fail=None, note="")
    return steps, ""


def draft_from_steps(name, description, examples, steps, hints=None, goal=""):
    """Save steps Nova really did as a DRAFT skill (only runs when asked for by name; it becomes a real skill after
    it has worked twice - see record_run). `hints` (from the cloud brain, all optional and checked here):
    {"slots": [{"step","arg","find"}], "expect": {id: check text}, "not_for": [text]}. Returns a message."""
    from nova.actions.knowledge import SECRET
    name = format.slug(name)
    if not name:
        return "Give the skill a short name, like 'crunchyroll-new'."
    if (store.current().skills_dir / f"{name}.md").exists():
        return f"I already have a skill called '{name}' - pick another name, or change it with edit_skill."
    ex = [e for e in (format._list(examples) if not isinstance(examples, list) else examples) if str(e).strip()] or [name.replace("-", " ")]
    description = re.sub(r"\s+", " ", str(description or "")).strip()[:300] or f"{name.replace('-', ' ')} (saved from a task: {goal[:80]})"
    if SECRET.search(description + " ".join(ex)):
        return "I won't save passwords, codes, keys, email addresses or phone numbers in a skill."
    if len([s for s in library.load() if s["status"] == "active"]) >= format.MAX_ACTIVE:
        return (f"I already have {format.MAX_ACTIVE} skills. Delete or retire one first - '{stats.least_used()}' is the one I use least.")
    sim = matching.similar(description, ex)
    if sim:
        return f"That looks like my '{sim[0]}' skill already - shall I change that one with edit_skill instead?"
    by_id = {s["id"]: s for s in steps}
    hints = hints if isinstance(hints, dict) else {}
    for sl in hints.get("slots", []) if isinstance(hints.get("slots"), list) else []:
        if not isinstance(sl, dict):
            continue
        st = by_id.get(str(sl.get("step", "")).lower())
        arg, find = sl.get("arg"), str(sl.get("find", ""))
        if st and "tool" in st and find and isinstance(st["args"].get(arg), str) and find in st["args"][arg]:
            st["args"][arg] = st["args"][arg].replace(find, "{input}")
    exp = hints.get("expect") if isinstance(hints.get("expect"), dict) else {}
    for sid, text in exp.items():
        chk = format.parse_check(text)
        if chk and str(sid).lower() in by_id and "tool" in by_id[str(sid).lower()]:
            by_id[str(sid).lower()]["expect"] = chk
    nf = [str(x) for x in hints.get("not_for", []) if isinstance(x, str)][:8] if isinstance(hints.get("not_for"), list) else []
    meta = {"name": name, "description": description, "examples": ex, "not_for": nf, "requires_any": [],
            "invoke": "explicit_only", "status": "draft"}
    text = format.render_v2(meta, steps)
    chk = format.parse(text, name)
    if not chk or chk["kind"] == "broken":
        return f"I couldn't turn that task into a skill ({chk['error'] if chk else 'empty'})."
    library._write(name, text, author="recorder", cause=goal[:200], op="create from a finished task")
    return (f"Saved a draft skill '{name}' with {len(steps)} steps: " + "; ".join(format.render_step(s)[:50] for s in steps[:5])
            + f". Say '{ex[0]}' to try it - after it works twice I'll keep it for good.")


from nova.actions.skills import format, library, matching, stats  # noqa: E402  (used when the code runs; no cycles)
