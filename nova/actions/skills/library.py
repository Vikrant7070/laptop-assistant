"""The skills folder: load/get (cached), save, delete, small edits, history and undo, status."""
import json
import re
import shutil
import time
from nova import store


# ------------------------------------------------------------------ the folder

_cache = {"key": None, "skills": []}


def load():
    """All skills, re-read only when a file in the folder changed."""
    try:
        files = sorted(store.current().skills_dir.glob("*.md"))
        # mtime alone isn't enough: Windows file times are coarse, so a save right after another kept the same key
        # and the old skill was read back (seen as a flaky test). The size usually changes; _write also clears it.
        key = (str(store.current().skills_dir),) + tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    except OSError:
        return []
    if key != _cache["key"]:
        out = []
        for f in files:
            try:
                s = format.parse(f.read_text(encoding="utf-8"), f.stem)
            except (OSError, UnicodeDecodeError):
                continue
            if s:
                s["file"] = str(f)
                out.append(s)
        _cache.update(key=key, skills=out)
    return _cache["skills"]


def get(name):
    n = format.slug(name)
    return next((s for s in load() if s["name"] == n), None)


def _history_add(name, entry):
    try:
        stats._side("history").mkdir(parents=True, exist_ok=True)
        with (stats._side("history") / f"{name}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def history(name):
    try:
        return [json.loads(l) for l in (stats._side("history") / f"{format.slug(name)}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def _set_front(text, key, value):
    m = re.match(r"^(﻿?---\s*\n)(.*?)(\n---\s*\n?)(.*)$", text, re.S)
    if not m:
        return text
    lines, done = m.group(2).splitlines(), False
    for i, l in enumerate(lines):
        if l.split(":", 1)[0].strip().lower() == key:
            lines[i], done = f"{key}: {value}", True
    if not done:
        lines.append(f"{key}: {value}")
    return m.group(1) + "\n".join(lines) + m.group(3) + m.group(4)


def _write(name, text, author="local", cause="", op=""):
    """Write a skill file. The file it replaces is kept in .history (so the change can be undone) and the change is
    logged. Returns the new version number."""
    store.current().skills_dir.mkdir(parents=True, exist_ok=True)
    f = store.current().skills_dir / f"{name}.md"
    old_ver, backup = 0, ""
    if f.exists():
        old = f.read_text(encoding="utf-8")
        try:
            old_ver = int(format._front(old)[0].get("version", 1))
        except ValueError:
            old_ver = 1
        stats._side("history").mkdir(parents=True, exist_ok=True)
        backup = f"{name}.v{old_ver}.md"
        if (stats._side("history") / backup).exists():
            backup = f"{name}.v{old_ver}-{int(time.time())}.md"
        (stats._side("history") / backup).write_text(old, encoding="utf-8")
    ver = old_ver + 1
    f.write_text(_set_front(text, "version", ver), encoding="utf-8")
    _cache["key"] = None                       # read it back fresh, whatever the file time says
    _history_add(name, {"ts": int(time.time()), "version": ver, "parent": old_ver or None, "author": author,
                        "cause": str(cause)[:200], "op": op, "backup": backup})
    return ver


def set_status(name, status, cause="", author="code"):
    f = store.current().skills_dir / f"{format.slug(name)}.md"
    if status not in format.STATUSES or not f.exists():
        return False
    text = _set_front(f.read_text(encoding="utf-8"), "status", status)
    _write(format.slug(name), text, author=author, cause=cause, op=f"set_status {status}")
    return True


def undo(name):
    """Put back the version a change replaced (undoing it again redoes it)."""
    n = format.slug(name)
    last = next((h for h in reversed(history(n)) if h.get("backup")), None)
    f = store.current().skills_dir / f"{n}.md"
    if not last or not (stats._side("history") / last["backup"]).exists() or not f.exists():
        return f"There is no earlier version of '{n}' to go back to."
    old = (stats._side("history") / last["backup"]).read_text(encoding="utf-8")
    _write(n, old, author="user", cause="undo", op="undo")
    return f"Put back the earlier version of '{n}'."


def save(name, description, steps, when="", kind="", replace=False, author="local", cause="", not_for="",
         requires_any="", status="active"):
    """Write a new skill. Returns a message for the brain. A skill that already exists is not overwritten unless
    replace=True - change one step with edit_skill instead of retyping the whole thing."""
    from nova.actions.knowledge import SECRET
    name = format.slug(name)
    if not name:
        return "Give the skill a short name, like 'movie-time'."
    description = re.sub(r"\s+", " ", str(description or "")).strip()[:300]
    body = str(steps or "").strip()
    if isinstance(steps, list):
        body = "\n".join(f"- {s}" for s in steps)
    if not body:
        return "The skill needs its steps."
    if len(body) > format.MAX_BODY:
        return f"That's too long for a skill (max {format.MAX_BODY} characters) - keep only the steps."
    if SECRET.search(body + " " + description):
        return "I won't save passwords, codes, keys, email addresses or phone numbers in a skill."
    when_ = format._list(when) if not isinstance(when, list) else [str(w).strip() for w in when if str(w).strip()]
    nf = format._list(not_for) if not isinstance(not_for, list) else [str(w).strip() for w in not_for if str(w).strip()]
    ra = format._list(requires_any) if not isinstance(requires_any, list) else [str(w).strip() for w in requires_any if str(w).strip()]
    kind = str(kind or "").lower()
    if kind not in ("routine", "recipe", ""):
        kind = ""
    existed = (store.current().skills_dir / f"{name}.md").exists()
    if existed and not replace:
        return (f"I already have a skill called '{name}'. To change it use edit_skill (one step at a time), or save "
                "it again with replace=true to rewrite the whole thing.")
    v2_lines = any(format.ID_RE.match(l.strip()) or format.ATTR_RE.match(l.strip()) for l in body.splitlines())
    if not format._steps(body) and len(body.splitlines()) > 1 and not v2_lines:
        body = "\n".join(f"- {l.strip()}" for l in body.splitlines() if l.strip())
    if not existed and not replace:
        sim = matching.similar(description, when_, skip=name)
        if sim:
            return (f"That looks like my '{sim[0]}' skill already - shall I change that one with edit_skill instead? "
                    "(To keep both, save it again with replace=true.)")
        if len([s for s in load() if s["status"] == "active"]) >= format.MAX_ACTIVE:
            return f"I already have {format.MAX_ACTIVE} skills - delete one first ('{stats.least_used()}' is the one I use least)."
    if kind in ("routine", ""):
        parsed, bad = format.parse_body_v2(body)
        if parsed is None and kind == "routine":
            return (f"That line isn't a step I can run: '{bad}'. Steps look like: open_app name=Spotify / "
                    "set_volume action=up, steps=5 / say Enjoy! / wait 2 / if page_has \"Log In\" goto s4. Or save it "
                    "as a recipe (plain steps).")
        if parsed is not None:
            for st in parsed:
                if "tool" in st and st["tool"] in format.LOCKED:
                    return f"A skill can't use {st['tool']}."
            text = format.render_v2({"name": name, "description": description, "examples": when_, "not_for": nf,
                              "requires_any": ra, "invoke": "explicit_only" if status == "draft" else "confirm_if_fuzzy",
                              "status": status}, parsed)
            ver = _write(name, text, author=author, cause=cause, op="replace" if existed else "create")
            s = get(name)
            how = f"say '{when_[0]}'" if when_ else f"ask for '{name.replace('-', ' ')}'"
            return (("Updated" if existed else "Saved") + f" the routine skill '{name}'"
                    + (f" with {len(s['steps'])} steps" if s else "") + f" (version {ver}). Next time just {how}.")
        kind = "recipe"
    text = format._render(name, description, when_, "recipe", body)
    ver = _write(name, text, author=author, cause=cause, op="replace" if existed else "create")
    how = f"say '{when_[0]}'" if when_ else f"ask for '{name.replace('-', ' ')}'"
    return ("Updated" if existed else "Saved") + f" the recipe skill '{name}' (version {ver}). Next time just {how}."


def delete(name):
    """Move a skill to skills/.trash (not gone for good)."""
    s = get(name)
    if not s:
        return f"I have no skill called '{name}'."
    trash = store.current().skills_dir / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    shutil.move(s["file"], trash / f"{s['name']}-{int(time.time())}.md")
    _cache["key"] = None
    return f"Deleted the skill '{s['name']}' (a copy is in {trash})."


# ---- small edits

def _next_id(steps):
    nums = [int(s["id"][1:]) for s in steps if re.fullmatch(r"s\d+", s["id"])]
    return f"s{max(nums, default=0) + 1}"


def _ids_for(steps):
    """A format-1 routine's steps get ids s1..sN when it is first edited."""
    out = []
    for i, st in enumerate(steps, 1):
        out.append({**st, "id": st.get("id") or f"s{i}", "expect": st.get("expect"), "on_fail": st.get("on_fail"),
                    "note": st.get("note", "")})
    return out


def _step_from_op(op, known):
    text = str(op.get("step", "")).strip()
    text = format.ID_RE.sub(lambda m: m.group(2), text) if format.ID_RE.match(text) else text
    st = format.parse_step_v2(text, known)
    if st is None:
        return None, f"'{text}' isn't a step I can run"
    if "tool" in st and st["tool"] in format.LOCKED:
        return None, f"a skill can't use {st['tool']}"
    for t in st.get("tools", []):
        if t not in known or t in format.LOCKED:
            return None, f"{t} is not a tool I can use here"
    st.setdefault("expect", None)
    st.setdefault("on_fail", None)
    st.setdefault("note", "")
    if "expect" in op:
        st["expect"] = format.parse_check(op["expect"]) if op["expect"] else None
        if op["expect"] and not st["expect"]:
            return None, f"'{op['expect']}' isn't a check I know"
    if "on_fail" in op:
        st["on_fail"] = format.parse_on_fail(op["on_fail"]) if op["on_fail"] else None
        if op["on_fail"] and not st["on_fail"]:
            return None, f"'{op['on_fail']}' isn't a valid on_fail"
    return st, ""


def edit(name, ops, author="local", cause=""):
    """Change a skill with a few small, checked operations instead of rewriting it. Returns a message for the brain."""
    from nova.actions.knowledge import SECRET
    s = get(name)
    if not s:
        return f"I have no skill called '{name}'."
    if s["kind"] == "recipe":
        return (f"'{s['name']}' is a plain-English recipe, so there are no steps to change one by one. Save it again "
                "as a routine (replace=true) with steps I can run.")
    if s["kind"] == "broken":
        return f"'{s['name']}' is broken ({s['error']}). Fix its file or save it again with replace=true."
    if isinstance(ops, str):
        try:
            ops = json.loads(ops)
        except ValueError:
            return "ops must be a JSON list like [{\"op\": \"insert_step\", \"after\": \"s2\", \"step\": \"wait 2\"}]."
    if isinstance(ops, dict):
        ops = [ops]
    if not isinstance(ops, list) or not ops:
        return "Give me at least one change in ops."
    if len(ops) > 5:
        return "That's too many changes at once (max 5) - do them in a couple of goes."
    if SECRET.search(json.dumps(ops, ensure_ascii=False)):
        return "I won't save passwords, codes, keys, email addresses or phone numbers in a skill."
    known = format._tool_names()
    steps = _ids_for([dict(st) for st in s["steps"]])
    meta = {k: list(s[k]) if isinstance(s[k], list) else s[k] for k in ("name", "description", "examples", "not_for",
                                                                       "requires_any", "invoke", "status")}
    done = []
    for op in ops:
        if not isinstance(op, dict):
            return "Each change must be an object with an \"op\"."
        kind = str(op.get("op", ""))
        ids = [x["id"] for x in steps]
        sid = str(op.get("id", "")).lower()
        if kind in ("replace_step", "delete_step", "set_expect", "set_on_fail") and sid not in ids:
            return f"There is no step {sid or '(missing id)'} in '{s['name']}' (steps: {', '.join(ids)}). Nothing was changed."
        if kind == "replace_step":
            st, err = _step_from_op(op, known)
            if not st:
                return f"{err}. Nothing was changed."
            old = steps[ids.index(sid)]
            st["id"] = sid
            st["expect"] = st["expect"] if "expect" in op else old.get("expect")
            st["on_fail"] = st["on_fail"] if "on_fail" in op else old.get("on_fail")
            steps[ids.index(sid)] = st
            done.append(f"changed {sid}")
        elif kind == "insert_step":
            after = str(op.get("after", "")).lower()
            if after not in ids + ["start", ""]:
                return f"There is no step {after} to insert after. Nothing was changed."
            st, err = _step_from_op(op, known)
            if not st:
                return f"{err}. Nothing was changed."
            st["id"] = _next_id(steps)
            steps.insert(0 if after in ("start", "") else ids.index(after) + 1, st)
            done.append(f"added {st['id']}")
        elif kind == "delete_step":
            if any(sid == x.get("goto") or sid in [a.get("id") for a in (x.get("on_fail") or [])] for x in steps):
                return f"Another step jumps to {sid}, so I can't delete it. Nothing was changed."
            steps = [x for x in steps if x["id"] != sid]
            done.append(f"removed {sid}")
        elif kind == "set_expect":
            chk = format.parse_check(op.get("expect", "")) if op.get("expect") else None
            if op.get("expect") and not chk:
                return f"'{op['expect']}' isn't a check I know. Nothing was changed."
            steps[ids.index(sid)]["expect"] = chk
            done.append(f"{sid} now checks {format.render_check(chk) if chk else 'nothing'}")
        elif kind == "set_on_fail":
            acts = format.parse_on_fail(op.get("on_fail", "")) if op.get("on_fail") else None
            if op.get("on_fail") and not acts:
                return f"'{op['on_fail']}' isn't a valid on_fail. Nothing was changed."
            steps[ids.index(sid)]["on_fail"] = acts
            done.append(f"{sid} on_fail updated")
        elif kind in ("add_example", "add_not_for"):
            text = re.sub(r"\s+", " ", str(op.get("text", "")).replace(",", " ")).strip()
            key = "examples" if kind == "add_example" else "not_for"
            if not text:
                return "That change needs some text. Nothing was changed."
            if text.lower() not in [x.lower() for x in meta[key]]:
                meta[key].append(text)
            done.append(f"{kind[4:]} '{text}'")
        elif kind == "set_description":
            meta["description"] = re.sub(r"\s+", " ", str(op.get("text", ""))).strip()[:300]
            done.append("new description")
        else:
            return (f"I don't know the change '{kind}'. Use replace_step, insert_step, delete_step, set_expect, "
                    "set_on_fail, add_example, add_not_for or set_description. Nothing was changed.")
    ids = [x["id"] for x in steps]
    for i, st in enumerate(steps):                       # the result must still be a skill that runs
        for t in ([st["goto"]] if "goto" in st else []) + [a["id"] for a in (st.get("on_fail") or []) if a["do"] == "goto"]:
            if t not in ids or ids.index(t) <= i:
                return f"That would leave step {st['id']} jumping to {t}, which isn't ahead of it. Nothing was changed."
    text = format.render_v2({**meta, "risk_explicit": format._explicit_risk(s["name"])}, steps)
    check = format.parse(text, s["name"])
    if not check or check["kind"] == "broken":
        return f"That would break the skill ({check['error'] if check else 'empty'}). Nothing was changed."
    ver = _write(s["name"], text, author=author, cause=cause, op="; ".join(done))
    return (f"Changed '{s['name']}' (now version {ver}): {', '.join(done)}. Say 'undo that skill change' to go back.")


from nova.actions.skills import format, matching, stats  # noqa: E402  (used when the code runs; no cycles)
