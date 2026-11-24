"""Talking over her: BargeIn hears the user while she speaks and stops her."""
import collections
import difflib
import re
import time
from nova import runtime
from nova.audio.voice import whisper


# Words that mean "stop talking, I'm saying something" when the user says them over her. Her name counts too.
# (Whisper writes "ruko" as Rucco / Ruco / Roko / Ruconova, so those count too.)
BARGE_STOP_RE = re.compile(r"\b(?:stop|ru+c*k*h?o(?:nova)?|roko|ruk ?jao|bas|chup|enough|shut up|quiet)\b", re.I)


def _barge_re(names):
    return re.compile(rf"\b(?:{names}|wait|hold on|one sec(?:ond)?|pause|excuse me)\b|" + BARGE_STOP_RE.pattern, re.I)


BARGE_RE = _barge_re(whisper.NOVA_NAMES)

# Symbols her voice reads out as words ("C:\Users" is said "C backslash Users"), so her echo of them matches.
_SPOKEN_SYMBOLS = {"\\": " backslash ", "/": " slash "}


def echo_words(text):
    """Words for comparing what Whisper heard with what she said: lower case, no apostrophes ("Nova's" = "novas"),
    spoken symbols as words, and spelled-out letters joined ("R.E.M." and "REM" are both "rem")."""
    text = (text or "").replace("’", "'")
    for sym, said in _SPOKEN_SYMBOLS.items():
        text = text.replace(sym, said)
    out, letters = [], ""
    for w in whisper._words(text.replace("'", "")):
        if len(w) == 1 and w not in ("a", "i"):
            letters += w
            continue
        if letters:
            out.append(letters)
            letters = ""
        out.append(w)
    if letters:
        out.append(letters)
    return out


def _same_word(heard, said):
    """Is this heard word one of hers, allowing Whisper's near misses ("Novus" for "Nova's", "Zikra" for "vikra")?
    Barge words (stop, wait, her name...) must match exactly, so "stop" is never taken for her "step"."""
    if heard == said:
        return True
    if max(len(heard), len(said)) < 3 or BARGE_RE.fullmatch(heard):
        return False
    return difflib.SequenceMatcher(None, heard, said).ratio() >= 0.75


def echo_score(text, said):
    """How much of what Whisper heard is her own words: the share (0..1) of heard words that are (nearly) in said.
    Words of one or two letters ("in", "it", "a") don't count: Whisper fills gaps with them. 0.0 when nothing was
    heard."""
    heard = [w for w in echo_words(text) if len(w) > 2]
    mine = set(echo_words(said))
    if not heard:
        return 0.0
    return sum(any(_same_word(w, m) for m in mine) for w in heard) / len(heard)


def other_apps_playing(level=0.02):
    """Is another app (music, a video, a call) making sound right now? Reads the volume meters, records nothing."""
    import os
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        comtypes.CoInitialize()
        for s in AudioUtilities.GetAllSessions():
            if s.ProcessId in (0, os.getpid()):
                continue
            try:
                if s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue() > level:
                    return True
            except Exception:
                pass  # intentional: an audio/OS call that has a fallback elsewhere
    except Exception:
        pass  # intentional: an audio/OS call that has a fallback elsewhere
    return False


class BargeIn:
    """Talk over her and she stops ("Nova, stop", "wait", "ruko"), the way people interrupt each other.

    While she speaks, a second recording runs. The hard part: the microphone hears her own voice from the speakers
    about as loud as the user's (measured 2026-11-19 on this laptop: her echo 0.006 RMS, the user's voice 0.006),
    Windows' echo cancelling did nothing here, and removing her voice by its known sound (adaptive filter,
    spectral masking) took off only 3-10 dB. So, like a person who hears someone start to speak:
      1. Sound her own voice can't explain. Mouth.played says how loud she was at every moment; her echo is that
         times a learned gain. NEED blocks within a second more than EXCESS times louder (mostly: the user
         speaking in her pauses between words) -> step 2. Strict, so it rarely fires on her own voice.
      2. She falters: DUCK_SECS at a third of her loudness (Mouth.duck), so the user's voice now stands out.
      3. A quick Whisper reading of that moment holds a barge word (her name, stop, wait, ruko...) that is NOT
         in what she was saying -> she stops (on_barge). Otherwise she carries on at full voice.
    Her own words come out of Whisper as her own words, or nearly ("Novus" for "Nova's"): when most of what Whisper
    heard is what she said in the last few seconds (judge's echo score), it is her echo and can't stop her. While another app plays sound
    (song lyrics say "stop" and "wait"), only her name counts. Needs the natural voice (Kokoro): with the Windows
    voice her sound isn't known, so this stays off."""

    LAG = -0.045          # her echo reaches the mic this long after Mouth's estimate (measured: -39..-46 ms)
    BEFORE, AFTER = 0.15, 0.04    # echo and room reverb: what she said up to 0.15 s ago can still be heard
    EXCESS = 4.5          # her own voice alone stayed under 3.1x its predicted echo in 99% of blocks (max 7.2)
    GAIN0 = 0.02          # mic loudness / played loudness on this laptop (0.017-0.024 live; relearned as she talks)
    NEED, WINDOW = 3, 1.0
    LOOK = 2.0            # seconds of microphone Whisper reads at least (ending with the dip)
    DUCK_SECS = 1.2
    DUCK_MAX = 2.5        # a dip lasts longer while the user keeps talking, up to this
    CHECK_EVERY = 1.0     # after a check, wait this long before the next dip (Whisper shares the graphics card)
    BLOCK = 0.03
    RATE = 16000

    def __init__(self, mouth, whisper, on_barge, enabled=lambda: True, others_playing=other_apps_playing):
        self.mouth, self.w, self.on_barge = mouth, whisper, on_barge
        self.enabled, self.others_playing = enabled, others_playing
        self.gain = self.GAIN0
        self.ratios = collections.deque(maxlen=300)
        self.floor = 0.0001
        self.hits = collections.deque()
        self.checking = False
        self.last_check = 0.0
        self.fired_gen = -1       # one barge per reply
        self.log = None           # set by the app: writes a line to the task log
        self.note = ""            # the last thing checked, for the status screen
        self.thread = None

    def start(self):
        if not (self.thread and self.thread.is_alive()):
            self.thread = runtime.spawn("barge_in.loop", self._loop, start=False)
            self.thread.start()

    def _active(self):
        m = self.mouth
        return bool(m.neural) and m.talking() and m.gen != self.fired_gen and self.enabled()

    def _loop(self):
        while True:
            try:
                if self._active():
                    self._listen_while_she_talks()
            except Exception as e:      # no microphone, device changed...
                self.note = f"barge-in mic problem: {e}"[:120]
                time.sleep(2)
            time.sleep(0.1)

    def unexplained(self, t, rms, played):
        """One microphone block. True when there have been NEED blocks of sound her voice can't explain within
        WINDOW seconds (time to check the words). Also keeps learning her echo's gain and the room's noise."""
        start_min = whisper.WhisperTranscriber.START_MIN * getattr(self.w, "scale", 1.0)    # (⚙ Senses mic level)
        odd = rms > self.EXCESS * self.gain * played + 4 * self.floor and rms > start_min
        if played > 0.02 and not odd:
            self.ratios.append(rms / played)
            if len(self.ratios) >= 50:
                s = sorted(self.ratios)
                self.gain = min(max(s[len(s) // 2], self.GAIN0 / 4), self.GAIN0 * 4)
        elif played < 0.005 and not odd and rms < self.floor * 3:
            self.floor = 0.9 * self.floor + 0.1 * rms
        if odd:
            self.hits.append(t)
        while self.hits and self.hits[0] < t - self.WINDOW:
            self.hits.popleft()
        return len(self.hits) >= self.NEED

    def _listen_while_she_talks(self):
        import numpy as np
        import sounddevice as sd
        n = int(self.RATE * self.BLOCK)
        blocks = collections.deque(maxlen=int((self.DUCK_MAX + 1.0) / self.BLOCK))
        self.hits.clear()
        quiet_since, check_at, dip_from = None, None, 0.0
        with sd.InputStream(samplerate=self.RATE, channels=1, dtype="float32", blocksize=n) as st:
            opened = time.time()
            while True:
                block, _ = st.read(n)
                t = time.time() - st.latency          # (about when this block's sound reached the mic)
                block = block[:, 0].copy()
                blocks.append(block)
                if not self._active():
                    quiet_since = quiet_since or time.time()
                    if time.time() - quiet_since > 0.4 or not self.enabled() or self.mouth.gen == self.fired_gen:
                        return
                    continue
                quiet_since = None
                if time.time() - opened < 0.25:          # (the microphone's own click as it opens)
                    continue
                rms = float(np.sqrt(np.mean(block ** 2)))
                played = self.mouth.echo_at(t - self.LAG - self.BLOCK - self.BEFORE, t - self.LAG + self.AFTER)
                odd = self.unexplained(t, rms, played)
                now = time.time()
                if odd and not self.checking and check_at is None and now - self.last_check >= self.CHECK_EVERY:
                    dip_from = now
                    self.mouth.duck(self.DUCK_SECS + 0.15)      # (+ the speaker's buffer, so the whole dip is heard)
                    check_at = now + 0.15 + self.DUCK_SECS
                elif odd and check_at is not None and check_at - now < 0.5 and now - dip_from < self.DUCK_MAX:
                    # Still sound that isn't her (softer now, so it stands out): they're still talking. Stay soft
                    # a little longer, so Whisper gets the end of what they say.
                    check_at = now + 0.5
                    self.mouth.duck(0.65)
                if check_at is not None and now >= check_at and not self.checking:
                    check_at, self.checking = None, True
                    secs = max(self.LOOK, now - dip_from + 0.6)     # the whole dip, and a moment before it
                    audio = np.concatenate(list(blocks)[-int(secs / self.BLOCK):])
                    runtime.spawn("barge_in.check", self._check, args=(audio, t, self.mouth.gen, secs))

    def _check(self, audio, t, gen, secs=None):
        try:
            text = self.w.quick_english(audio, wait=1.0)
            look = secs or self.LOOK
            own = self.mouth.words_between(t - look - 0.5, t + 0.2)
            recent = self.mouth.words_between(t - look - self.ECHO_BACK, t + 0.5)
            kind, score = self.judge(text, own, music=bool(self.others_playing and self.others_playing()),
                                     recent=recent)
            verdict = kind or ("her own echo" if score >= self.ECHO_MIN else "not for me")
            self.note = (f"{time.strftime('%H:%M:%S')} over her voice: “{text[:60]}” -> {verdict}"
                         f" (echo {score:.2f})")
            fire = bool(kind) and gen == self.mouth.gen and self.mouth.talking()
            if self.log and echo_words(text):
                said = (recent or own)[-60:]
                if fire:
                    self.log(f"BARGE-IN ({kind}, echo {score:.2f}): heard {text[:60]!r} while she said {said!r}")
                else:
                    self.log(f"barge-in ignored ({verdict}, echo {score:.2f}): heard {text[:60]!r}"
                             f" while she said {said!r}")
            if fire:
                self.fired_gen = gen
                self.on_barge(kind, text)
        except Exception as e:
            self.note = f"barge-in check failed: {e}"[:120]
        finally:
            self.last_check = time.time()
            self.checking = False

    TALK_WORDS = 4        # this many new words (none of them hers) in the dip = the user is saying something
    # Echo: this share of the heard words (or more) is her own -> Whisper heard her, not a sentence of the user's.
    # Live echoes: 2026-11-21 21:33 "Not too bad, boss. R.E.M. is a big" (all hers, but missed by the old exact
    # check) and 2026-11-24 14:02 "with PRs that are in there. It looks like Novus got some fun" (0.67 hers).
    # Stop words and her name still stop her at any score. Tune from the "barge-in ... echo" lines in the task log.
    ECHO_MIN = 0.65
    ECHO_BACK = 6.0       # her echo can be of anything she said this many seconds before the dip

    @classmethod
    def barge_kind(cls, text, own, music=False, recent=None):
        return cls.judge(text, own, music, recent)[0]

    @classmethod
    def judge(cls, text, own, music=False, recent=None):
        """(kind, echo score). kind: 'stop' (stop words), 'name' (her name / wait / hold on), 'talk' (a sentence of
        the user's own, like "open Chrome instead"), or ''. Stop words and her name count only when she wasn't saying
        them herself (own: what she said during the dip); 'talk' needs TALK_WORDS new words and an echo score under
        ECHO_MIN against recent (what she said over the last few seconds; own when not given).
        music: another app is playing, so only her name counts."""
        mine = set(whisper._words(own))
        new_words = [w for w in whisper._words(text) if w not in mine]
        new = " ".join(new_words)
        score = echo_score(text, f"{own} {recent or ''}")
        if not new:
            return "", score
        named = re.search(rf"\b{whisper.NOVA_NAMES}\b", new, re.I)
        if music and not named:
            return "", score
        if BARGE_STOP_RE.search(new):
            return "stop", score
        if named or BARGE_RE.search(new):
            return "name", score
        if len(new_words) >= cls.TALK_WORDS and score < cls.ECHO_MIN and not whisper.noise_words(new):
            return "talk", score
        return "", score
