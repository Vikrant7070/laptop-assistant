"""The wake-word ears built on Whisper (WhisperEars) and set_wake, which renames the wake word."""
import threading
import time
from nova import runtime
from nova.avatar import avatar_client
from nova.log import log_exception

on_change = None   # set by the app: on_change(status, heard) when the ears' status line or what they heard changes


class _EarsBase:
    """What both listeners share: the status line and the last few things heard (for the chat's status screen)."""
    status = "off"
    on_wake = None             # set by the app: listen for the request (conversation=True for "wake up")
    on_sleep = None            # set by the app: end conversation mode

    def __setattr__(self, name, value):
        changed = name in ("status", "heard") and getattr(self, name, None) != value
        super().__setattr__(name, value)
        if changed and on_change and hasattr(self, "heard"):
            try:
                on_change(self.status, list(self.heard))
            except Exception:
                log_exception("ears on_change")

    def _note(self, what):
        self.heard = (self.heard + [f"{time.strftime('%H:%M:%S')} {what}"])[-10:]

    def _woke_for_conversation(self):
        self._note(f"heard '{whisper.WAKE_NAME}, wake up' - conversation mode")
        if getattr(self, "on_name", None):
            self.on_name()
        avatar_client.send("wave", quiet=True, timeout=2)
        if self.on_wake:
            runtime.spawn("wake.on_wake", self.on_wake, kwargs={"conversation": True})

    def _asked_to_sleep(self):
        self._note(f"heard '{whisper.WAKE_NAME}, go to sleep'")
        if self.on_sleep:
            runtime.spawn("wake.on_sleep", self.on_sleep)


class WhisperEars(_EarsBase):
    """'Hey Nova' with Whisper - no Windows speech recognizer involved. While switched on, it listens to each thing
    said near the laptop, writes it down on the laptop itself, and throws it away unless it starts with 'Hey Nova'.
    'Hey Nova, what's the time?' in one breath works; so does 'Hey Nova' ... pause ... the request."""

    def __init__(self, mouth, on_text, whisper):
        self.mouth, self.on_text, self.w = mouth, on_text, whisper
        self.status = "off"
        self.heard = []
        self.on_wake = None        # set by the app: listen for the request (conversation=True for "wake up")
        self.on_sleep = None
        self.paused = None         # set by the app: True while it is already listening for a request itself
        self.on_name = None        # set by the app: her name was just heard - get up now if she's napping
        self.log = None            # set by the app: writes a line to the task log
        self.quiet_until = 0.0
        self.online_off = False
        self.skip_next = False
        self.running = False
        self.thread = None
        self.early = 0.0          # when her name was last spotted mid-sentence (the bubble is already showing)
        self.early_bare = False    # ...and nothing but her name had been said at that point
        self.peeking = False
        self.is_stop = None        # set by the app: is this "Nova, stop" (stop everything, no conversation)?
        self.on_stop = None

    def _named(self):
        if self.on_name:
            try:
                self.on_name()
            except Exception:
                pass  # intentional: an audio/OS call that has a fallback elsewhere

    def _peek(self, audio):
        """Runs while the user is still speaking: if the sentence starts with her name, react right away."""
        if self.peeking or self.early > time.time() - 6 or self._step_aside_quiet():
            return
        self.peeking = True
        try:
            text = self.w.quick_english(audio)
            m = whisper.WAKE_RE.search(text)
            if m and not whisper.EARS_SLEEP_RE.search(text) and not whisper.EARS_WAKEUP_RE.search(text):
                self.early_bare = not text[m.end():].strip(" ,.!?-…")
                self.early = time.time()
                self._named()
                avatar_client.send("listen", on=True, secs=8, timeout=2)
                avatar_client.send("wave", quiet=True, timeout=2)
        finally:
            self.peeking = False

    @staticmethod
    def wake_doubt(rest, sure):
        """Why something with her name in it is probably noise, or "". rest = the words after her name.
        "Hey Nova, <request>" in one breath goes straight to her brain, so the request gets the same check as one
        made in a conversation; her name alone (or "wake up") opens a 5-minute conversation, so it must be sure."""
        if len(rest.split()) >= 2 and not whisper.EARS_SLEEP_RE.search(rest) and not whisper.EARS_WAKEUP_RE.search(rest):
            return whisper.WhisperTranscriber.doubt(rest, sure, None, expecting=False)
        return "not sure enough" if sure < whisper.SURE_WAKE else ""

    def _step_aside_quiet(self):
        return not self.running or self.mouth.busy() or bool(self.paused and self.paused())

    def set_mode(self, mode):
        pass   # one way of listening only

    def start(self):
        if self.thread and self.thread.is_alive() and self.running:
            return
        windows_ears.kill_strays("listen.ps1")   # the old Windows listener must not answer "Hey Nova" as well
        self.running = True
        self.status = "starting"
        self.thread = runtime.spawn("wake.loop", self._loop, start=False)
        self.thread.start()

    def stop(self):
        self.running = False
        self.status = "off"

    def _step_aside(self):
        return (not self.running or self.w.waiting > 0 or self.mouth.busy() or bool(self.paused and self.paused()))

    def _loop(self):
        try:
            self.w.load()
        except Exception as e:
            self.status = f"Whisper didn't load: {e}"
            return
        self.status = "listening"
        self._note(f"listening with Whisper {self.w.using}")
        me = threading.current_thread()
        while self.running and self.thread is me:
            if self._step_aside():
                time.sleep(0.15)
                continue
            kind, text = self.w.hear(30.0, self.w.MAX_LEN + 2, whisper.WAKE_HINT, abort=self._step_aside, peek=self._peek, end_pause=None,
                                     hold=lambda: self.peeking or (self.early_bare and time.time() - self.early < 15))
            early = time.time() - self.early < 15       # her name was already spotted in this sentence
            if not self.running:
                break
            if kind == "error":
                self.status = f"microphone problem: {text}"[:120]
                time.sleep(3)
                continue
            self.status = "listening"
            if kind == "text" and not self.mouth.busy():   # (while she talks, it would be her own voice)
                if not whisper.WAKE_RE.search(text) and (early or self.w.language != "en"):
                    english = "Nova" if early else self.w.again_in_english()
                    if whisper.WAKE_RE.search(english):
                        # Short = just the wake word (use the English reading). Long = a Hindi request to Nova:
                        # keep the Hindi reading of it.
                        text = english if self.w.seconds < 3.0 else "Nova, " + text
                self._handle(text, early)
            elif early and kind != "error":             # she showed "I'm listening" but then caught no words
                self.early = 0.0
                self._handle("Nova", True)
        if self.thread is me:
            self.status = "off"

    def _handle(self, text, early=False):
        """early = her name was spotted mid-sentence, so she has already waved and shown "I'm listening"."""
        self.early, self.early_bare = 0.0, False
        m = whisper.WAKE_RE.search(text)
        if not m:
            # Not for Nova. Only the start of the latest one is kept (in memory, replaced each time), so that
            # "she doesn't react" can be worked out from the status screen.
            self.heard = [h for h in self.heard if "didn't start with" not in h]
            self._note(f"heard speech that didn't start with 'Hey {whisper.WAKE_NAME}': “{text[:50]}” (mic level {self.w.level:.2f})")
            return
        low = text.lower()
        rest = text[m.end():].strip(" ,.!?-…")
        # (early: the quick look already read her name mid-sentence - two readings agree, so it was really said)
        why = "" if early else self.wake_doubt(rest, getattr(self.w, "sure", 0.0))
        if why:
            self.heard = [h for h in self.heard if "ignored noise" not in h]
            self._note(f"ignored noise: “{text[:50]}” ({why}, mic level {self.w.level:.2f})")
            return
        if getattr(self.w, "profile", None):
            self.w.profile.learn(getattr(self.w, "voice_level", 0.0))   # this is the user: remember how loud they are
        if not whisper.EARS_SLEEP_RE.search(low):
            self._named()
        if whisper.EARS_SLEEP_RE.search(low):
            self._asked_to_sleep()
        elif getattr(self, "is_stop", None) and self.on_stop and self.is_stop(text):   # "Hey Nova, stop"
            self._note(f"heard 'Hey {whisper.WAKE_NAME}, stop' (mic level {self.w.level:.2f})")
            runtime.spawn("wake.on_stop", self.on_stop, args=(text,))
        elif whisper.EARS_WAKEUP_RE.search(low):
            self._woke_for_conversation()
        elif len(rest.split()) >= 2:                       # "Hey Nova, what's the time?" in one breath
            self._note(f"heard 'Hey {whisper.WAKE_NAME}' + request (mic level {self.w.level:.2f}): {rest}"[:160])
            if not early:
                avatar_client.send("wave", quiet=True, timeout=2)
            avatar_client.send("say", text=f"“{rest}”", secs=30, timeout=2)   # (her first spoken sentence replaces it)
            if self.log:
                self.log(f"HEARD {rest[:60]!r} ({self.w.timing_note()})")
            runtime.spawn("wake.on_text", self.on_text, args=(rest,))
        else:
            self._note(f"heard 'Hey {whisper.WAKE_NAME}' (mic level {self.w.level:.2f})")
            if not early:     # (if early, the same bubble is up already and the app's listener keeps it up)
                avatar_client.send("wave", quiet=True, timeout=2)
                avatar_client.send("listen", on=True, secs=6, timeout=2)
            if self.on_wake:
                runtime.spawn("wake.on_wake", self.on_wake)


def set_wake(names, hint, name="Nova"):
    """Answer to another name: the chosen character's ("Hey Lucy"). names = a regex group of its spellings
    (characters.wake_names), hint = a sentence that teaches Whisper to spell it. Only this name wakes her now."""
    whisper.NOVA_NAMES, whisper.WAKE_HINT, whisper.WAKE_NAME = names, hint, name
    whisper.WAKE_RE, barge_in.BARGE_RE = whisper._wake_re(names), barge_in._barge_re(names)


from nova.audio.voice import barge_in, whisper, windows_ears  # noqa: E402  (used when the code runs; no cycles)
