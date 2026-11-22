"""Side files written only by code: run stats, not_for phrases, record_run, least_used."""
import json
import time
from nova import store


# ------------------------------------------------------------------ side files: stats and history

def _side(kind):
    return store.current().skills_dir / f".{kind}"


def stats_get(name):
    try:
        d = json.loads((_side("stats") / f"{format.slug(name)}.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def stats_save(name, d):
    try:
        _side("stats").mkdir(parents=True, exist_ok=True)
        (_side("stats") / f"{format.slug(name)}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def stats_update(name, fn):
    d = stats_get(name)
    fn(d)
    stats_save(name, d)
    return d


def add_not_for(name, text):
    """The user said no to a fuzzy match: remember that sentence so it never starts this skill again."""
    t = matching._norm(text)
    if not t:
        return

    def add(d):
        d["denied"] = d.get("denied", 0) + 1
        lst = [x for x in d.get("learned_not_for", []) if x != t] + [t]
        d["learned_not_for"] = lst[-50:]
    stats_update(name, add)


def record_run(name, ok, failed_step="", error="", stopped=False, author="code"):
    """Count one run of a skill. Returns a note for the user when this run promoted a draft or retired a failing
    skill (else "")."""
    def add(d):
        d["runs"] = d.get("runs", 0) + 1
        d["last_used"] = int(time.time())
        recent = (d.get("recent", []) + [1 if ok else 0])[-10:]
        d["recent"] = recent
        if ok:
            d["ok"] = d.get("ok", 0) + 1
            d["last_ok"] = d["last_used"]
        elif stopped:
            d["stopped_by_user"] = d.get("stopped_by_user", 0) + 1
        else:
            fb = d.setdefault("failed_by_step", {})
            fb[failed_step or "?"] = fb.get(failed_step or "?", 0) + 1
            d["last_error"] = str(error)[:200]
    d = stats_update(name, add)
    s = library.get(name)
    if not s or stopped:
        return ""
    recent = d.get("recent", [])
    if s["status"] == "draft" and ok and d.get("ok", 0) >= 2 and recent[-2:] == [1, 1]:
        library.set_status(name, "active", cause="2 verified runs", author=author)
        return f"My '{name}' skill worked twice in a row, so I'm keeping it - you can now say it any time."
    if s["status"] == "active" and d.get("runs", 0) >= 5 and len(recent) >= 5 and sum(recent[-5:]) / 5 < 0.4:
        step = max(d.get("failed_by_step", {"?": 1}).items(), key=lambda kv: kv[1])[0]
        library.set_status(name, "deprecated", cause="failed 3+ of the last 5 runs", author=author)
        return (f"My '{name}' skill keeps failing (mostly at step {step}), so I've stopped using it. "
                "Want to show me how it should work?")
    return ""


def least_used():
    """The active skill used least (for when the library is full)."""
    act = [s for s in library.load() if s["status"] == "active"]
    if not act:
        return None
    return min(act, key=lambda s: (stats_get(s["name"]).get("runs", 0), stats_get(s["name"]).get("last_used", 0)))["name"]


from nova.actions.skills import format, library, matching  # noqa: E402  (used when the code runs; no cycles)
