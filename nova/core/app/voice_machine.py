"""What her voice side is doing, as one state machine (roadmap R10) - running in SHADOW MODE.

Today her voice is driven by flags spread over voice_io: convo["on"/"thinking"], nap["asleep"/"deep"],
listen_state["ears_off"/"single"], and whether her mouth is busy. Each is right on its own; together they are hard
to reason about ("is she napping with her ears off while still saying goodnight?"). This module says the same thing
as one machine:

  her MODE - which way she listens - changes only by the explicit TRANSITIONS table:
      EARS_OFF          the 👂 switch is off: no wake word (typing and the 🎤 button still work)
      WAITING_FOR_WAKE  awake, listening only for "Hey Nova"
      CONVERSATION      listening after every answer, until "go to sleep" or 5 quiet minutes
      NAPPING           lying in the corner; "Hey Nova" or a message wakes her
      DEEP_SLEEP        napping with the mic off; a click or a message wakes her
  on top of the mode, what she is doing right now (she can talk while napping - the goodnight line - or think with
  her ears off), each switched by its own on/off events:
      HEARING   the mic is taking in a request
      THINKING  the brain is working on an answer
      SPEAKING  her mouth is busy
  `state` is the most specific of them: SPEAKING > THINKING > HEARING > the mode.

Shadow mode: voice_io fires the events next to the flag changes it already makes; nothing reads the machine yet.
After every event `check(flags)` compares it with what the flags say, and a disagreement is logged as a
`voice_mismatch` event (db.find_events(kind="voice_mismatch")). After a week of live use with no mismatches the
readers switch to `state` and the flags go (roadmap R10 step 4).
"""
import threading
import time

from nova import log

EARS_OFF, WAITING, CONVERSATION, NAPPING, DEEP_SLEEP = "EARS_OFF", "WAITING_FOR_WAKE", "CONVERSATION", "NAPPING", "DEEP_SLEEP"
HEARING, THINKING, SPEAKING = "HEARING", "THINKING", "SPEAKING"
MODES = (EARS_OFF, WAITING, CONVERSATION, NAPPING, DEEP_SLEEP)
STATES = (*MODES, HEARING, THINKING, SPEAKING)

AWAKE = "AWAKE"        # a target in the table: WAITING_FOR_WAKE, or EARS_OFF if the 👂 switch is off

# Mode events. (mode, event) -> next mode. A pair that isn't here is illegal (raises in strict mode, logged in shadow).
TRANSITIONS = {
    (EARS_OFF, "ears_on"): WAITING,
    (EARS_OFF, "ears_off"): EARS_OFF,
    (EARS_OFF, "wake_heard"): CONVERSATION,      # the 🎤 button or a typed "wake up" still starts a conversation
    (EARS_OFF, "sleep_asked"): NAPPING,
    (EARS_OFF, "nap_due"): NAPPING,
    (EARS_OFF, "woken"): EARS_OFF,

    (WAITING, "wake_heard"): CONVERSATION,
    (WAITING, "ears_off"): EARS_OFF,
    (WAITING, "ears_on"): WAITING,
    (WAITING, "sleep_asked"): NAPPING,
    (WAITING, "nap_due"): NAPPING,
    (WAITING, "woken"): WAITING,
    (WAITING, "conversation_over"): WAITING,

    (CONVERSATION, "wake_heard"): CONVERSATION,
    (CONVERSATION, "ears_off"): EARS_OFF,         # ears off ends a conversation too
    (CONVERSATION, "ears_on"): CONVERSATION,
    (CONVERSATION, "sleep_asked"): NAPPING,
    (CONVERSATION, "nap_due"): NAPPING,            # five quiet minutes
    (CONVERSATION, "woken"): CONVERSATION,
    (CONVERSATION, "conversation_over"): AWAKE,    # a voice character change ends it without a nap

    (NAPPING, "wake_heard"): CONVERSATION,
    (NAPPING, "woken"): AWAKE,                     # a click, a typed message, a task, presence
    (NAPPING, "deep_due"): DEEP_SLEEP,
    (NAPPING, "ears_off"): NAPPING,                # (the switch is remembered: woken -> EARS_OFF)
    (NAPPING, "ears_on"): NAPPING,
    (NAPPING, "sleep_asked"): NAPPING,
    (NAPPING, "nap_due"): NAPPING,

    (DEEP_SLEEP, "woken"): AWAKE,
    (DEEP_SLEEP, "wake_heard"): CONVERSATION,      # (the mic is off: only the 🎤 button / typed wake-up get here)
    (DEEP_SLEEP, "ears_off"): DEEP_SLEEP,
    (DEEP_SLEEP, "ears_on"): DEEP_SLEEP,           # the ears come back when she's woken
    (DEEP_SLEEP, "sleep_asked"): DEEP_SLEEP,
    (DEEP_SLEEP, "nap_due"): DEEP_SLEEP,
    (DEEP_SLEEP, "deep_due"): DEEP_SLEEP,
}

# Activity events: legal in every mode, they only switch HEARING / THINKING / SPEAKING on or off.
ACTIVITY = {
    "hearing_started": (HEARING, True), "hearing_ended": (HEARING, False),
    "request_heard": (THINKING, True), "answered": (THINKING, False),
    "reply_started": (SPEAKING, True), "reply_done": (SPEAKING, False), "barge_in": (SPEAKING, False),
}
EVENTS = sorted({e for _, e in TRANSITIONS} | set(ACTIVITY))


class IllegalTransition(Exception):
    pass


class VoiceState:
    def __init__(self, mode=WAITING, ears=True, strict=False, publish=None):
        self.mode, self.ears, self.strict = mode, ears, strict
        self.busy = {HEARING: False, THINKING: False, SPEAKING: False}
        self.publish = publish          # publish(kind, data): the event stream (eventbus.publish)
        self.history = []               # the last 50 (time, event, from, to)
        self.lock = threading.Lock()

    @property
    def state(self):
        for activity in (SPEAKING, THINKING, HEARING):
            if self.busy[activity]:
                return activity
        return self.mode

    def fire(self, event):
        """Apply one event. -> the new state. Unknown or illegal: raises in strict mode, else logged and ignored."""
        with self.lock:
            before = self.state
            if event in ACTIVITY:
                activity, on = ACTIVITY[event]
                self.busy[activity] = on
            elif (self.mode, event) in TRANSITIONS:
                nxt = TRANSITIONS[(self.mode, event)]
                if event in ("ears_on", "ears_off"):
                    self.ears = event == "ears_on"
                self.mode = (WAITING if self.ears else EARS_OFF) if nxt == AWAKE else nxt
            else:
                if self.strict:
                    raise IllegalTransition(f"{event} in {self.mode}")
                log.event("voice_illegal", f"{event} in {self.mode}", event=event, mode=self.mode)
                return before
            after = self.state
            self.history = (self.history + [(time.time(), event, before, after)])[-50:]
        if after != before:
            log.event("voice", f"{before} -> {after} ({event})", event=event, before=before, after=after)
            if self.publish:
                self.publish("voice_state", {"state": after, "mode": self.mode, "event": event})
        return after

    def check(self, flags):
        """Shadow mode: does the machine agree with the old flags? flags = {"ears_off", "convo_on", "asleep", "deep",
        "thinking"}. A disagreement is logged (voice_mismatch) and returned as text; agreement returns ""."""
        want = from_flags(flags)
        got = (self.mode, self.busy[THINKING])
        if (want["mode"], want["thinking"]) == got:
            return ""
        last = self.history[-1][1] if self.history else ""
        note = f"machine {self.mode}/thinking={got[1]}, flags {want['mode']}/thinking={want['thinking']} after {last}"
        log.event("voice_mismatch", note, machine=self.mode, flags=dict(flags), last_event=last)
        return note


def from_flags(flags):
    """What today's flags say her mode is (and whether she's thinking)."""
    if flags.get("deep"):
        mode = DEEP_SLEEP
    elif flags.get("asleep"):
        mode = NAPPING
    elif flags.get("convo_on"):
        mode = CONVERSATION
    elif flags.get("ears_off"):
        mode = EARS_OFF
    else:
        mode = WAITING
    return {"mode": mode, "thinking": bool(flags.get("thinking"))}
