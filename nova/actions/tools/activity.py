"""📊 The activity log by voice: pause it, carry on, switch it off, say what it is doing, say the routines found.

Switching it ON is only in ⚙ Settings → 🔒 Privacy (the user ticks it themselves). Nothing recorded (titles, sites)
goes to the brain from here: only the state, a count, and the routines found by plain code.
"""
from nova.actions.registry import tool


@tool("The user's activity log (which apps / sites they use and when, kept on this laptop; it is OFF unless they "
      "switched it on in Settings). action: 'pause' or 'resume' recording, 'off' to stop it, 'status' to say what "
      "it is doing, 'routines' to say the daily routines it noticed. It can't be switched on from here.",
      friendly="Activity log", hints="pause the activity log, stop recording my apps, resume logging, what are my "
                                     "routines, do you log what I do",
      props={"action": {"type": "string", "enum": ["pause", "resume", "off", "status", "routines"]}},
      required=["action"])
def activity_log(action="status"):
    from nova.actions import activity
    s = activity.prefs()
    if action in ("pause", "resume", "off"):
        if not s["on"]:
            return "The activity log is off - nothing is being recorded. (Only the user can switch it on, in ⚙ Settings.)"
        if action == "off":
            activity.save_prefs(on=False, paused=False)
            return "Switched the activity log off. What was recorded stays until the user deletes it (⚙ Settings → Privacy)."
        activity.save_prefs(paused=action == "pause")
        return "Paused the activity log - nothing is recorded until the user says resume." if action == "pause" \
            else "The activity log is recording again."
    if action == "routines":
        found = activity.routines()
        if not found:
            return "I haven't noticed any routines yet (it takes the same steps on at least 3 days)."
        return "Routines I noticed:\n" + "\n".join(f"- {r['text']} (seen on {r['days']} days)" for r in found[:8])
    st = activity.status()
    if not st["on"]:
        return "The activity log is off - nothing is being recorded."
    return (f"The activity log is {'paused' if st['paused'] else 'on'}; {st['spans']} entries kept on this laptop "
            f"(each deleted after {st['keep_days']} days).")
