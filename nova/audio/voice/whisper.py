"""Listening with Whisper on the GPU: noise filters, the voice profile, the wake regex, WhisperTranscriber."""
import re
import threading
import time
from pathlib import Path


# Best first: large-v3-turbo on the graphics card (~0.4 s per sentence, ~0.9 GB of its 6 GB - measured to fit beside
# Nova's Ollama model without slowing it). If the card can't take it, small.en on the processor (~1.6 s per sentence).
# large-v3-turbo on the processor took 13-16 s per sentence here, so it is never used that way.
WHISPER_CHOICES = [("large-v3-turbo", "cuda", "int8_float16"), ("small.en", "cpu", "int8")]


# English, Hindi and Hinglish. Each sentence's language is detected first. Clear English is written down as English.
# Anything else is treated as Hindi/Hinglish and written in English letters ("Chrome kholo", "abhi kitne baje hain") -
# the example sentences below are what make Whisper use English letters instead of Devanagari. That spelling is what
# Nova's brain, her command matching and her English (India) voice can all work with.
# The one switch for Nova's language. True = English only: every sentence is read as English (no language check,
# so listening is a little quicker and a short "Hi Nova" is never mistaken for Hindi), and she answers in English.
# False = English, Hindi and Hinglish, detected per sentence.
ENGLISH_ONLY = True


ENGLISH_SURE = 0.8


HINGLISH_HINT = ("Chrome kholo. Abhi kitne baje hain? Volume thoda kam kar do. YouTube par gaana chalao. "
                 "Meri battery kitni bachi hai? Screen par kya hai? Notepad band karo.")


def _add_nvidia_dlls():
    """The graphics-card maths libraries come from pip (nvidia-cublas-cu12, nvidia-cudnn-cu12); make them findable."""
    import os
    try:
        import nvidia
        for base in nvidia.__path__:
            for d in Path(base).glob("*/bin"):
                if str(d) not in os.environ["PATH"]:
                    os.environ["PATH"] = str(d) + os.pathsep + os.environ["PATH"]
    except Exception:
        pass  # intentional: an audio/OS call that has a fallback elsewhere


WHISPER_HINT = ("Open Chrome, open VS Code, open WhatsApp, YouTube, Spotify. "
                "What's on my screen? Check my battery. Volume up.")


# Gives Whisper her name to spell while it listens for "Hey Nova" (without it: "Noah", "Inua", "Noelle").
WAKE_HINT = "Hey Nova. Nova, what's the time? Hey Nova, open Chrome."


WAKE_NAME = "Nova"       # the name she answers to now (set_wake: the chosen character's)


WHISPER_JUNK = {"", "you", "thank you.", "thanks for watching!", "thank you for watching.", "bye.", ".", "so", "okay."}


# The same stock words, compared without punctuation or capitals ("Thank you!", "you.", "Bye bye."): what Whisper
# writes for a cough, a door or a few seconds of hum.
JUNK_WORDS = {"you", "thank you", "thanks for watching", "thank you for watching", "thank you so much for watching",
              "bye", "bye bye", "so", "okay", "the end", "you you", "i", "a", "the"}


# What Whisper "hears" in chimes, hums and her own reaction sounds (measured with Windows' notification sounds, a fan,
# typing and Nova's sfx, 2026-11-15): stock video endings, caption credits, and bare sounds that are not words.
WHISPER_GHOSTS = re.compile(r"(see you (next time|in the next)|thanks? (you )?for watching|subscribe|closed captions?|"
                            r"captions? by|subtitles? by|for more information,? visit|www\.|\.(com|org)\b)", re.I)


NOT_WORDS = {"huh", "hmm", "hm", "hmmm", "mm", "mmm", "mhm", "oh", "ohh", "ohhh", "ohhhh", "ah", "ahh", "uh", "um",
             "umm", "er", "sigh", "hey", "what", "wow", "whoa", "oops", "ha", "haha", "hahaha", "hehe", "heh", "ooh"}


SHORT_REPLY = re.compile(r"^\W*(yes|yeah|yep|no|nope|okay|ok|sure|stop|cancel|do it|go ahead|haan|nahi)\b", re.I)


# How sure Whisper must be (mean log-probability per token) before words become a request. Clear requests score
# -0.1 to -0.5; noise it turned into words scored -0.77 to -1.6. Bare yes/no replies score lower ("No." -1.1), so
# they keep the old limit. Words that are one of WHISPER_HINT's own sentences need more: Whisper repeats its hint
# when it hears a chime ("What's on my screen?" at -0.79 from her spin whoosh).
SURE = -0.8


SURE_SHORT = -1.2


SURE_HINT = -0.6


# The looser limit for a bare yes/no is only for when she has just asked something (an Allow/No question, "want to
# see it?", or an answer that ended in "?"). Otherwise a stray "No." from a cough gets answered ("Arey, what
# happened, boss?", 2026-11-15). "Stop" and "cancel" keep the looser limit always: they can cut her off any time.
ANYTIME_SHORT = re.compile(r"^\W*(stop|cancel)\b", re.I)


# "Hey Nova" on its own wakes her into a conversation for 5 minutes, so it must be clearly said, not a hum Whisper
# wrote down as "Noah." (her name in a sure reading scores -0.1 to -0.6).
SURE_WAKE = -1.0


def _words(text):
    return re.findall(r"[a-z']+", (text or "").lower())


def noise_words(text):
    """Why these words are what speech recognizers make up from noise, or "" if they could be real. Needs no
    confidence score, so it works for Windows' recognizers too."""
    words = _words(text)
    if not words or all(w in NOT_WORDS for w in words):
        return "a sound, not words"
    if " ".join(words) in JUNK_WORDS:
        return "a stock phrase noise turns into"
    if WHISPER_GHOSTS.search(text):
        return "a phrase Whisper makes up from noise"
    return ""


class VoiceProfile:
    """How loud the user's own voice reaches the microphone, learned from each "Hey Nova" (only the user calls her
    by name, so those recordings are theirs). Requests in a conversation (no "Hey Nova" needed) that are far quieter
    than that - a TV, people in the next room, someone on a call - are not taken as requests.
    This is loudness, not who is speaking: telling one person's voice from another's needs a speaker-recognition
    model, and none is installed (no torch, speechbrain or resemblyzer; Smart App Control blocks adding them)."""

    KEEP = 30            # the last 30 "Hey Nova"s
    MIN_SAMPLES = 5      # nothing is filtered until her name has been heard this many times
    QUIET_SHARE = 0.2    # under a fifth of your usual loudness (-14 dB, ~5x as far away) = not you
    SNR_MIN = 3.0        # speech must be at least 3x the room's own noise, or Whisper is guessing words into a hum

    def __init__(self, path=None):
        from nova.shared import DATA
        self.path = Path(path) if path else DATA / "voice_profile.json"
        self.levels = []
        try:
            import json
            self.levels = [float(x) for x in json.loads(self.path.read_text(encoding="utf-8")).get("levels", [])]
        except Exception:
            self.levels = []

    def learn(self, level):
        if not level or level <= 0:
            return
        self.levels = (self.levels + [round(float(level), 6)])[-self.KEEP:]
        try:
            import json
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"levels": self.levels}), encoding="utf-8")
        except Exception:
            pass  # intentional: an audio/OS call that has a fallback elsewhere

    def usual(self):
        if len(self.levels) < self.MIN_SAMPLES:
            return None
        s = sorted(self.levels)
        return s[len(s) // 2]

    def doubt(self, level, noise=0.0):
        """Why a recording this loud (mean loudness of its voiced parts) is probably not the user, or ""."""
        if not level:
            return ""
        if noise and level < noise * self.SNR_MIN:
            return f"barely louder than the room ({level / noise:.1f}x its noise)"
        usual = self.usual()
        if usual and level < usual * self.QUIET_SHARE:
            return f"much quieter than your voice ({level / usual:.0%} of usual) - someone or something farther away"
        return ""


NOVA_NAMES = r"(?:nova|novah|noah|nover|nava|nowa|inua|innova|enova|nuva|noelle|no va|नोवा|नौवा|नोआ)"


# "Hey Nova ..." anywhere, or a sentence that starts with "Nova ..."
# Nova's name as the first or second word ("Nova, ...", "Hey Nova ...", "Hei Nova", "Ok Nova", "Arey Nova", "हे नोवा"),
# or stuck to the greeting ("HeyNova"), or "hey/ok Nova" anywhere in the sentence. Whisper spells the greeting many
# ways, especially in Hindi mode, so the greeting itself is not checked.
def _wake_re(names):
    return re.compile(rf"^\W*(?:\S+\s+)?(?:he[yi]|hi|ok)?{names}(?!\w)"
                      rf"|\b(?:he[yi]|hi|hai|hello|okay|ok|arey)\W*{names}(?!\w)"
                      # A soft "Hey Nova" on its own can come out as "See you, Noah." - a short phrase that ends in
                      # her name is a call too.
                      rf"|^\W*(?:\S+\W+){{0,2}}{names}\W*$", re.I)


WAKE_RE = _wake_re(NOVA_NAMES)


EARS_SLEEP_RE = re.compile(r"go (?:back )?to sleep|back to sleep|stop listening|good ?night|so ?jao|so ?ja\b|sunna band|shubh ratri|सो जा", re.I)


EARS_WAKEUP_RE = re.compile(r"wake up|are you there|uth ?jao|\butho\b|jaag ?jao|\bjaago\b|उठ|जाग", re.I)


class WhisperTranscriber:
    """Offline listening with OpenAI's Whisper (faster-whisper, CPU). Records one utterance from the default
    microphone (starts when you speak, ends after a short pause) and writes it down. Same listen() results as
    Transcriber, so the app can use either. Nothing leaves the laptop."""

    RATE = 16000
    BLOCK = 0.03          # seconds per audio block
    START_WAIT = 8.0      # how long to wait for you to start talking
    END_PAUSE = 0.7       # this much quiet after speech = you're done (⚙ Senses: "Stop listening after…"; the app sets it)
    # Quiet also means "much softer than your own voice": a TV or fan in the room stays above the room-noise level
    # measured before you spoke, so on that alone a recording ran to MAX_LEN (task log 2026-11-18: most requests
    # ended with "end pause 0.00s" - cut off by the time limit, never by a pause). Below 20% of your voice's
    # loudness (about -14 dB) counts as you having stopped, once you've said ~0.3 s.
    VOICE_DROP = 0.2
    # Loudness (RMS of a 30 ms block) that counts as speech. Measured 2026-11-14: a silent room reads ~0.00005 on this
    # laptop's mic array, and normal-volume speech often stays under the old fixed 0.0015 start / 0.001 keep-going
    # levels, so "Hey Nova" had to be said loudly and soft requests were cut off. The levels now follow the room's
    # own noise (x3.5 to start, x2.5 to keep going) with much lower fixed minimums; Silero VAD + Whisper still
    # reject clicks and bumps afterwards.
    START_MIN = 0.0003    # ~2x the room's loudest background blips (p90 0.00017), 6x its median
    KEEP_MIN = 0.00025
    MAX_LEN = 10.0        # longest one request may be (⚙ Senses: "Listen at most…")
    # ⚙ Senses "How loud I must be" (mic_level 1-10, 5 = the levels above). The app sets this; see thresholds().
    scale = 1.0

    @staticmethod
    def level_scale(mic_level):
        """mic_level 1..10 -> how much the "someone is speaking" levels are multiplied by (1 -> x0.25, 5 -> x1,
        10 -> x5.7, about 15 dB more sound needed)."""
        return 2 ** ((float(mic_level) - 5) / 2)

    def thresholds(self):
        """(start_min, start_factor, keep_min, keep_factor) at the current mic_level. The keep-going factor moves
        more gently (square root) so a loud setting doesn't cut off a sentence's softer ending, and the start
        factor never drops below 2x the room's noise (steady noise alone must not start a recording)."""
        s = self.scale
        return self.START_MIN * s, max(2.0, 3.5 * s), self.KEEP_MIN * s, max(1.5, 2.5 * s ** 0.5)

    def __init__(self):
        self.model = None
        self.lock = threading.Lock()
        self.waiting = 0       # someone wants an answer now (listen()); background wake-word listening steps aside
        self.muted = False     # 👂 off in the chat: no listening at all - a recording in progress is dropped too
        # One Whisper job on the graphics card at a time. Two at once (the quick wake-word look + the full
        # reading) doubled its working memory: 0.9 GB grew to 1.2 GB, the 6 GB card overflowed, and Nova's brain
        # slowed to a crawl.
        self.gpu = threading.Lock()
        self.failures = 0
        self.error = None
        self.level = 0.0       # loudness of the last thing heard, for the status notes
        self.hearing_at = 0.0  # when the microphone last caught a voice (3D Nova's ear waves grow while you speak)
        self.using = None      # which model ended up loaded, e.g. "large-v3-turbo on cuda"
        self.language = "en"   # what the last sentence was taken as: "en", or "hi" for Hindi/Hinglish
        self.last_audio, self.seconds = None, 0.0
        self.on_partial = None  # set by the app: shows the words so far (text) while the user is still speaking
        self.live, self.live_busy = False, False
        self.timing = {}       # seconds spent on each part of the last recording (pause, vad, gpu_wait, decode)
        self.skip = 0         # how many of WHISPER_CHOICES have been given up on
        # Set by the app to mouth.busy: True while Nova is talking (or just stopped, or a reaction sound plays).
        # Whatever the microphone takes in then is her own voice, never a request - it is thrown away.
        self.self_voice = None
        self.rejected = ""     # the last words thrown away as noise, and why (for the status notes)
        self.sure = 0.0        # how sure Whisper was of the last words (mean log-probability per token)
        self.voice_level = 0.0  # raw loudness of the last recording's voiced parts (before it is turned up)
        self.noise = 0.0       # the room's background loudness just before that recording
        self.profile = VoiceProfile()
        # Set by the app: True while she is waiting for a yes/no (bare "No." from noise is ignored otherwise).
        self.expect_reply = None
        self.next_floor = None  # set by the app after a barge-in: the room's noise for the next recording
        self.meter = (0.0, 0.0, 0.0)  # (mic loudness now, level that starts a recording, when) for the live meter

    @staticmethod
    def available():
        try:
            import faster_whisper, sounddevice  # noqa: F401
            return True
        except Exception:
            return False

    def load(self):
        """Load the best model that works (a few seconds). Call early in a background thread so the first
        request is quick."""
        if self.model is None:
            import numpy as np
            from faster_whisper import WhisperModel
            _add_nvidia_dlls()
            last = None
            for name, device, compute in WHISPER_CHOICES[self.skip:]:
                try:
                    model = WhisperModel(name, device=device, compute_type=compute)
                    list(model.transcribe(np.zeros(self.RATE, np.float32), language="en")[0])   # prove it really runs
                    self.model, self.using = model, f"{name} on {device}"
                    break
                except Exception as e:     # no graphics-card libraries, card full, model not downloaded...
                    last = e
                    self.skip += 1
            if self.model is None:
                raise last or RuntimeError("no Whisper model could be loaded")
        return self.model

    def _decode(self, audio, hint, force=None):
        t0 = time.time()
        with self.gpu:
            self.timing["gpu_wait"] = time.time() - t0     # (waiting for the graphics card: the brain or a peek had it)
            return self._decode_now(audio, hint, force)

    def timing_note(self):
        """The last recording's delays in one line, for the task log."""
        t = self.timing
        return (f"end pause {t.get('pause', 0.0):.2f}s, vad {t.get('vad', 0.0):.2f}s, "
                f"gpu wait {t.get('gpu_wait', 0.0):.2f}s, decode {t.get('decode', 0.0):.2f}s, {self.using}")

    def _decode_now(self, audio, hint, force=None):
        model = self.load()
        language = "en"
        if model.model.is_multilingual and not force and not ENGLISH_ONLY:   # (small.en backup only knows English)
            _, _, probs = model.detect_language(audio)
            if dict(probs).get("en", 0.0) < ENGLISH_SURE:
                language, hint = "hi", HINGLISH_HINT
        self.language = language
        # Keep these careful settings. On 2026-11-18 beam 1 + no second VAD pass made her mishear her own name
        # ("Hey, Noah", sure -1.05 < SURE_WAKE) so she stopped reacting; decoding is only ~0.5 s anyway.
        segs, _ = model.transcribe(audio, language=language, beam_size=3, vad_filter=True,
                                   initial_prompt=hint, condition_on_previous_text=False)
        # (the reading happens here) compression_ratio > 2.4 = the same words over and over ("open, open, open")
        return [s for s in segs if s.no_speech_prob < 0.6 and s.avg_logprob > -1.2 and s.compression_ratio < 2.4]

    def _record(self, start_wait, max_len, abort=None, peek=None, end_pause=None, hold=None, peek_every=0.0):
        """Returns float32 mono audio of one utterance, or None if nobody spoke (or abort() said stop).
        This laptop's microphone is very quiet (a silent room reads ~0.0002), so the "someone is speaking" level
        is set relative to the room's own background noise rather than to a fixed loudness."""
        import numpy as np
        import sounddevice as sd
        n = int(self.RATE * self.BLOCK)
        chunks, started, quiet, floor, loud = [], False, 0.0, None, 0
        waited, power = 0.0, 0.0      # power: sum of squared loudness over the voiced blocks
        self.voice_level, self.noise = 0.0, 0.0
        end_pause = end_pause or self.END_PAUSE
        start_min, start_x, keep_min, keep_x = self.thresholds()
        # Seconds into the sentence at which peek() gets what was said so far. A bare "Hey Nova" lasts only ~0.6 s,
        # so the first look has to come early.
        peeks, spoken = [0.5, 0.9, 1.5], 0.0
        last_sound, held_at, voiced = 0.0, None, 0.0
        give_up = time.time() + start_wait + 90         # (in case she never stops talking)
        with sd.InputStream(samplerate=self.RATE, channels=1, dtype="float32", blocksize=n) as st:
            while True:
                block, _ = st.read(n)
                block = block[:, 0].copy()
                if self.self_voice and self.self_voice():
                    # She is speaking (a notification, her answer, a reaction sound): forget what was recorded so
                    # far and wait for her to finish. Her quiet time doesn't count towards start_wait either.
                    chunks, started, loud, quiet, spoken, voiced, power = [], False, 0, 0.0, 0.0, 0.0, 0.0
                    peeks, last_sound, held_at = [0.5, 0.9, 1.5], 0.0, None
                    if time.time() > give_up or (abort and abort()):
                        return None
                    continue
                rms = float(np.sqrt(np.mean(block ** 2)))
                if floor is None:
                    # (after a barge-in the user is already talking: the room's noise was measured while she spoke)
                    floor, self.next_floor = (self.next_floor or rms), None
                # For the ⚙ Senses live meter: how loud the mic is now, and the level that starts a recording.
                self.meter = (rms, max(start_min, floor * start_x), time.time())
                if not started:
                    chunks = (chunks + [block])[-12:]               # keep ~0.35 s from before speech starts
                    if rms > max(start_min, floor * start_x):
                        loud += 1
                        started = loud >= 3                         # ~0.1 s of sound, not a single click
                    else:
                        loud = 0
                        floor = 0.95 * floor + 0.05 * rms           # track background noise
                    waited += self.BLOCK
                    if not started and (waited > start_wait or (abort and abort())):
                        return None
                    continue
                chunks.append(block)
                spoken += self.BLOCK
                if peek and peeks and spoken >= peeks[0]:
                    peeks.pop(0)
                    threading.Thread(target=peek, args=(np.concatenate(chunks),), daemon=True).start()
                    if peek_every and not peeks:         # keep looking every so often while the sentence goes on
                        peeks.append(spoken + peek_every)
                n_voice = round(voiced / self.BLOCK)
                mine = (power / n_voice) ** 0.5 * self.VOICE_DROP if voiced >= 0.3 and n_voice else 0.0
                if rms < max(keep_min, floor * keep_x, mine):
                    quiet += self.BLOCK
                else:
                    quiet, last_sound, voiced = 0.0, spoken, voiced + self.BLOCK
                    power += rms * rms
                    self.hearing_at = time.time()
                # Only "Hey Nova" so far and she has shown "I'm listening": keep this same recording open for the
                # request (up to 3.5 s), so its first word isn't lost while the microphone is reopened. Once the
                # request has been spoken (sound well after the wake word), the normal short pause ends it.
                # (More than ~1.1 s of voice means a request came with the wake word, so there is nothing to wait for.)
                holding = bool(hold and hold())
                if holding and held_at is None:
                    held_at, voiced_then = spoken, voiced
                patience = 3.5 if (holding and voiced_then <= 1.1 and last_sound <= held_at + 0.5) else end_pause
                if quiet >= patience or len(chunks) * self.BLOCK >= max_len:
                    break
                if abort and abort():
                    return None
        self.timing = {"pause": quiet}      # (how much quiet was waited out at the end: the recording's own delay)
        n_voiced = round(voiced / self.BLOCK)
        self.voice_level = float(np.sqrt(power / n_voiced)) if n_voiced else 0.0
        self.noise = float(floor or 0.0)
        return np.concatenate(chunks)

    def _transcribe(self, audio, hint, strict=False):
        """Text of a recording, or "" if it holds no real speech."""
        import numpy as np
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        peak = float(np.abs(audio).max())
        self.level = peak
        if peak < 1e-4:
            return ""
        audio = (audio * min(0.9 / peak, 60.0)).astype(np.float32)   # bring a quiet microphone up to a normal level
        self.last_audio, self.seconds = audio, len(audio) / self.RATE  # (in memory only, replaced by the next one)
        # A second opinion on "is this a voice?" (keyboard clicks and bumps are not) before the slow part.
        t0 = time.time()
        speech = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=500))
        self.timing["vad"] = time.time() - t0
        if sum(s["end"] - s["start"] for s in speech) < 0.25 * self.RATE:
            return ""
        t0 = time.time()
        try:
            segs = self._decode(audio, hint)
        except Exception:
            if self.skip + 1 >= len(WHISPER_CHOICES):
                raise
            # The graphics card gave up mid-sentence (usually: out of memory). Drop to the next model and redo it.
            self.model, self.skip = None, self.skip + 1
            segs = self._decode(audio, hint)
        self.timing["decode"] = time.time() - t0 - self.timing.get("gpu_wait", 0.0)
        text = " ".join(s.text.strip() for s in segs).strip()
        if text.lower() in WHISPER_JUNK or " ".join(_words(text)) in JUNK_WORDS:
            return ""
        sure = sum(s.avg_logprob * len(s.text) for s in segs) / max(1, sum(len(s.text) for s in segs))
        self.sure = sure
        if strict:
            expecting = True if self.expect_reply is None else bool(self.expect_reply())
            why = self.doubt(text, sure, hint, expecting)
            if why:
                self.rejected = f"“{text[:40]}” ({why}, sure {sure:.2f})"
                return ""
        return text

    @staticmethod
    def doubt(text, sure, hint=None, expecting=True):
        """Why these words are probably not a request (noise Whisper turned into words), or "" if they look real.
        expecting: she has just asked a yes/no question, so a bare "No." may be a little unclear and still count."""
        words = _words(text)
        why = noise_words(text)
        if why:
            return why
        said = " ".join(words)
        hinted = hint and any(said == " ".join(_words(h)) for h in re.split(r"(?<=[.?!])\s+", hint) if h)
        if hinted and sure < SURE_HINT:
            return "Whisper repeating its hint"
        if len(words) <= 3 and (ANYTIME_SHORT.match(text) or (expecting and SHORT_REPLY.match(text))):
            return "" if sure >= SURE_SHORT else "not sure enough"
        return "" if sure >= SURE else "not sure enough"

    def loudness_doubt(self):
        """Why the last recording is probably not the user talking to her (see VoiceProfile), or ""."""
        return self.profile.doubt(self.voice_level, self.noise) if self.profile else ""

    def again_in_english(self):
        """The last recording written down as plain English - a short "Hi Nova" is sometimes taken for Hindi."""
        with self.lock:
            try:
                segs = self._decode(self.last_audio, None, force="en")
                return " ".join(s.text.strip() for s in segs).strip()
            except Exception:
                return ""

    def quick_english(self, audio, wait=0.0):
        """A fast, rough reading of a short piece of audio as English (about 0.2 s on the graphics card). Used to
        spot "Hey Nova" while the sentence is still being spoken. wait: seconds to wait for the graphics card."""
        import numpy as np
        if not (self.gpu.acquire(timeout=wait) if wait else self.gpu.acquire(blocking=False)):   # card busy: skip
            return ""
        try:
            peak = float(np.abs(audio).max())
            if peak < 1e-4:
                return ""
            audio = (audio * min(0.9 / peak, 60.0)).astype(np.float32)
            segs, _ = self.load().transcribe(audio, language="en", beam_size=1, vad_filter=False,
                                             condition_on_previous_text=False, without_timestamps=True)
            return " ".join(s.text.strip() for s in segs if s.no_speech_prob < 0.6).strip()
        except Exception:
            return ""
        finally:
            self.gpu.release()

    def _live_peek(self, audio):
        """While the user is still speaking: a quick rough reading of what was said so far goes to on_partial (the
        bubble), so the words appear in step with the voice. One look at a time; skipped when the card is busy."""
        if self.live_busy or not self.live:
            return
        self.live_busy = True
        try:
            text = self.quick_english(audio)
            if text and self.live and not (self.self_voice and self.self_voice()):
                self.on_partial(text)
        except Exception:
            pass  # intentional: an audio/OS call that has a fallback elsewhere
        finally:
            self.live_busy = False

    def hear(self, start_wait, max_len, hint=None, abort=None, peek=None, end_pause=None, hold=None, strict=False,
             peek_every=0.0):
        """One utterance. Returns (kind, text): 'text', 'silence' (nobody spoke), 'unclear' (sound, no words), 'error'.
        strict: it will be taken as a request without "Hey Nova", so noise must not get through as words."""
        with self.lock:
            try:
                self.load()
                self.rejected = ""
                audio = self._record(start_wait, max_len, abort, peek, end_pause, hold, peek_every)
                if audio is None:
                    return "silence", "no speech"
                quiet = self.loudness_doubt() if strict else ""
                if quiet:                  # (checked before Whisper runs: no graphics-card time spent on a TV)
                    self.level = float(abs(audio).max())
                    self.rejected = f"a sound {quiet}"
                    return "unclear", ""
                text = self._transcribe(audio, hint, strict)
            except Exception as e:
                self.error = str(e)
                return "error", str(e)
        return ("text", text) if text else ("unclear", "")

    def listen(self):
        """Wait for one request. Returns (kind, text): kind is 'text', 'silence', 'unclear' or 'error'."""
        if self.muted:
            return "silence", "ears off"
        self.waiting += 1
        try:
            self.live = True
            kind, text = self.hear(self.START_WAIT, self.MAX_LEN, WHISPER_HINT, strict=True, abort=lambda: self.muted,
                                   peek=self._live_peek if self.on_partial else None, peek_every=0.9)
        finally:
            self.live = False
            self.waiting -= 1
        self.failures = self.failures + 1 if kind == "error" else 0
        return kind, text
