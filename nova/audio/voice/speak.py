"""Nova speaking: the Windows voice and Kokoro (NeuralVoice), BrainBalance, and the Mouth that plays sentences."""
import collections
import queue
import re
import threading
import time
from pathlib import Path
from nova.avatar import avatar_client
from nova.audio.voice import wording


PREFERRED_VOICES = ("Heera", "Zira", "Aria", "Jenny")


ONECORE = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"


# ---------------------------------------------------------------- a natural voice (Kokoro, offline, on the processor)
# Model files live outside the project (it sits in OneDrive): ~/.cache/nova-voice/  - see README for the download.
NEURAL_DIR = Path.home() / ".cache" / "nova-voice"


NEURAL_MODEL, NEURAL_VOICES = "kokoro-v1.0.int8.onnx", "voices-v1.0.bin"


NEURAL_VOICE = "hf_alpha"      # Hindi female voice; she also speaks English, with an Indian accent. Others: hf_beta


NEURAL_SPEED = 1.05


NEURAL_THREADS = 4             # Kokoro's CPU threads (see NeuralVoice.load)


wording.HINGLISH_WORDS.update({
    "main": "मैं", "do": "दो", "me": "में", "band": "बंद", "na": "ना", "bhi": "भी", "hi": "ही", "pe": "पे", "yahan": "यहाँ",
    "wahan": "वहाँ", "jaldi": "जल्दी", "der": "देर", "pehle": "पहले", "baad": "बाद", "bol": "बोल", "sun": "सुन",
    "rakh": "रख", "rakho": "रखो", "ruk": "रुक", "ruko": "रुको", "chahta": "चाहता", "chahti": "चाहती", "chahte": "चाहते",
    "sakta": "सकता", "sakti": "सकती", "sakte": "सकते", "hota": "होता", "hoti": "होती", "wah": "वाह", "waah": "वाह",
    "shabash": "शाबाश", "zara": "ज़रा", "samajh": "समझ", "pata": "पता", "dekha": "देखा", "aaya": "आया", "aayi": "आई",
    "khush": "खुश", "maza": "मज़ा", "mazaa": "मज़ा", "kamaal": "कमाल", "dhyan": "ध्यान", "aaram": "आराम", "neend": "नींद",
    "thak": "थक", "padega": "पड़ेगा", "karega": "करेगा", "karungi": "करूँगी", "karunga": "करूँगा", "karenge": "करेंगे",
    "deti": "देती", "leti": "लेती", "baki": "बाकी", "baaki": "बाकी", "khatam": "ख़त्म", "shuru": "शुरू", "chalu": "चालू",
    "ghanta": "घंटा", "ghante": "घंटे", "samay": "समय", "waqt": "वक़्त", "sahab": "साहब", "bhaiya": "भैया", "ek": "एक",
    "teen": "तीन", "char": "चार", "paanch": "पाँच", "das": "दस", "sau": "सौ", "pura": "पूरा", "puri": "पूरी", "naya": "नया",
    "nayi": "नई", "purana": "पुराना", "bada": "बड़ा", "badi": "बड़ी", "chhota": "छोटा", "chota": "छोटा", "chhoti": "छोटी",
    "apna": "अपना", "apni": "अपनी", "apne": "अपने", "unka": "उनका", "iska": "इसका", "uska": "उसका", "kisi": "किसी",
    "koi": "कोई", "sabse": "सबसे", "jab": "जब", "tab": "तब", "agar": "अगर", "kyunki": "क्योंकि", "isliye": "इसलिए",
    "magar": "मगर", "rahungi": "रहूँगी", "chalti": "चलती", "chalta": "चलता",
    "khula": "खुला", "khuli": "खुली", "kholti": "खोलती", "kholun": "खोलूँ", "karun": "करूँ", "karu": "करूँ", "dikha": "दिखा",
    "dikhao": "दिखाओ", "lagta": "लगता", "lagti": "लगती", "milega": "मिलेगा", "aapke": "आपके", "tumhare": "तुम्हारे",
    "humara": "हमारा", "hamara": "हमारा", "ho gaya": "हो गया",
})


VOICE_CUSHION = 3.0     # seconds of speech ready ahead of the speaker: below this, the voice gets the processor first


class BrainBalance:
    """Shares the processor between her voice (Kokoro) and her brain (Ollama's llama-server, which does ~1/4 of the
    model on the processor here, and which Ollama starts at ABOVE-normal priority - so it used to win every core
    while she spoke).
    - The voice is about to run dry (less than VOICE_CUSHION s of speech ready) and is making the next sentence:
      the brain drops to below normal for that moment, so she never pauses mid-reply.
    - Otherwise (she has plenty ready, or isn't speaking) both run at normal priority, so the brain keeps writing
      at full speed - the voice can't get far ahead anyway, it only says what the brain has written.
    Measured 2026-11-18, 3.6 s sentence made next to a busy brain: 1.3-2.3 s at the brain's own priority, 1.2-1.5 s
    with the brain below normal. Ollama starts a new runner when a model (re)loads, so the runner list is refreshed
    in the background every 20 s."""

    def __init__(self):
        self.procs = []
        self.low = None           # True: the brain is below normal right now
        self.found_at = 0.0
        self.lock = threading.Lock()

    def _refresh(self):
        try:
            import psutil
            procs = [p for p in psutil.process_iter(["name"])
                     if (p.info["name"] or "").lower() in ("llama-server.exe", "ollama_llama_server.exe")]
        except Exception:
            return
        with self.lock:
            new = {p.pid for p in procs} != {p.pid for p in self.procs}
            self.procs = procs
            if new:
                self.low = None   # a new runner starts above normal: set it on the next call

    def voice_needs_cpu(self, urgent):
        if time.time() - self.found_at > 20:      # (listing processes takes ~50-100 ms: never in front of a sentence)
            self.found_at = time.time()
            threading.Thread(target=self._refresh, daemon=True).start()
        with self.lock:
            if self.low == urgent or not self.procs:
                return
            self.low = urgent
            procs = list(self.procs)
        try:
            import psutil
            level = psutil.BELOW_NORMAL_PRIORITY_CLASS if urgent else psutil.NORMAL_PRIORITY_CLASS
            for p in procs:
                p.nice(level)
        except Exception:
            pass  # intentional: an audio/OS call that has a fallback elsewhere


brain_balance = BrainBalance()


class NeuralVoice:
    """Kokoro text-to-speech: sounds far more human than the Windows voices, runs offline on the processor
    (about 2.7x faster than real time here). Reads Hindi script as Hindi and English letters as English, mixed
    freely in one sentence."""

    def __init__(self):
        self.k = None
        self.broken = False

    @staticmethod
    def available():
        try:
            import kokoro_onnx, sounddevice  # noqa: F401
            return (NEURAL_DIR / NEURAL_MODEL).exists() and (NEURAL_DIR / NEURAL_VOICES).exists()
        except Exception:
            return False

    def load(self):
        if self.k is None:
            from kokoro_onnx import Kokoro
            try:
                # 4 threads, not one per core: with the default, Kokoro and the half-on-CPU brain (Ollama 24/76 CPU/GPU)
                # fought over every core - measured 2026-11-18 while both ran: brain 16.7 -> 30 tok/s with 4, and the
                # voice no slower (1.4-2.2 s for a 4 s sentence).
                import onnxruntime as ort
                so = ort.SessionOptions()
                so.intra_op_num_threads = NEURAL_THREADS
                sess = ort.InferenceSession(str(NEURAL_DIR / NEURAL_MODEL), so, providers=["CPUExecutionProvider"])
                self.k = Kokoro.from_session(sess, str(NEURAL_DIR / NEURAL_VOICES))
            except Exception:
                self.k = Kokoro(str(NEURAL_DIR / NEURAL_MODEL), str(NEURAL_DIR / NEURAL_VOICES))
        return self.k

    def synth(self, text):
        """(samples, sample_rate) for one sentence."""
        k = self.load()
        text = wording._hindi_script(text)
        # Clock times: "09:29" would be read "zero nine 29"; "9:00" as "nine zero zero".
        text = re.sub(r"\b0?(\d{1,2}):00\b", r"\1", text)
        text = re.sub(r"\b0?(\d{1,2}):(\d{2})\b", r"\1 \2", text)
        if wording.DEVANAGARI.search(text):
            # Sound it out as Hindi; the English words inside are sounded as English automatically. The engine
            # marks those switches with "(en)" / "(hi)" - which must be removed or they are read out loud.
            ph = re.sub(r"\((?:en|hi)[a-z\-]*\)", " ", k.tokenizer.phonemize(text, "hi"))
        else:
            # British voices (bf_/bm_) need British sounds, or they speak with an odd half-American accent.
            ph = k.tokenizer.phonemize(text, "en-gb" if NEURAL_VOICE[:1] == "b" else "en-us")
        return k.create(" ".join(ph.split()), voice=NEURAL_VOICE, speed=NEURAL_SPEED, is_phonemes=True)


DUCK_LEVEL = 0.3    # Mouth.duck(): her loudness while she checks whether the user is talking over her (-10 dB)


class Mouth:
    """Nova's voice, in two stages so her lips and speech bubble match the sound:
    - the worker prepares audio for each sentence as soon as the text arrives (several sentences ahead), and
    - the player plays it, and at the moment the sound starts tells 3D Nova to move her lips and to show that
      sentence in her bubble for exactly as long as it lasts; the lips stop as soon as it ends - including short
      pauses while the next sentence is still being prepared.
    Messages to 3D Nova go through their own thread (_tell), so a slow reply from her never delays the sound."""

    def __init__(self):
        self.q = queue.Queue()            # (text, time it was handed over) to say
        self.audio = queue.Queue()        # (generation, samples, rate, words, timing) ready to play
        self.speaking = False
        self.last_end = 0.0
        self.hold_until = 0.0             # hold(): a reaction sound is playing - count as busy so the mic ignores it
        self.stop_flag = False
        self.gen = 0                      # bumped by stop(): anything prepared before that is thrown away
        self.pending = 0                  # sentences taken from the text queue but not yet played
        self.lock = threading.Lock()
        self.lips = False                 # is 3D Nova currently showing the talking animation?
        self.voices = []
        self.voice_name = None
        self.neural = NeuralVoice() if NeuralVoice.available() else None
        self.on_start = None       # on_start(words, timing): she starts saying a reply (the app logs it)
        self.on_gap = None         # on_gap(secs, "brain"|"voice", words): a pause mid-reply, and whose fault (logged)
        self.ready_secs = 0.0      # speech made but not started yet
        self.play_until = 0.0      # when the sentence playing now ends
        self.bubbles = True        # show each sentence in 3D Nova's speech bubble while it is spoken
        self.bubble_until = 0.0
        self.tells = queue.Queue()
        # What came out of the speaker and when (for BargeIn, which must tell her own echo from the user's voice):
        # (time, loudness) every 10 ms, and (start, end, words) per sentence. Only the last ~30 s are kept.
        self.played = collections.deque(maxlen=3000)
        self.said_lines = collections.deque(maxlen=12)
        self._out_end = 0.0               # when the last sample written to the speaker will have been played
        self.grace = 0.8                  # busy() stays True this long after her last word (the room's echo)
        self.duck_until, self._gain = 0.0, 1.0   # duck(): softer for a moment
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._player, daemon=True).start()
        threading.Thread(target=self._teller, daemon=True).start()

    def say(self, text, show=True):
        """Queue text to be said. show=False: out loud only, with no bubble (iPhone notifications)."""
        text = wording._clean_for_speech(text or "")
        if text:
            self.grace = 0.8
            self.q.put((text, time.time(), show))

    def stop(self, quick=False):
        """quick: the user is talking over her (BargeIn). Her microphone grace period (busy() stays True 0.8 s
        after she stops, for the room's echo) shrinks to 0.2 s, so the listening that follows catches their words."""
        self.stop_flag = True
        with self.lock:
            self.gen += 1
            self.pending = 0
            self.ready_secs = 0.0
            self.play_until = 0.0
        if quick:
            self.speaking = False
            self.last_end = time.time()
            self.grace = 0.2
        while not self.q.empty():
            self.q.get_nowait()
        while not self.audio.empty():
            self.audio.get_nowait()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass  # intentional: an audio/OS call that has a fallback elsewhere
        self._lips(False)
        if self.bubble_until > time.time():      # her words are still up: take them down with the sound
            self.bubble_until = 0.0
            self._tell(None, "say", text="", secs=0)

    # ---- messages to 3D Nova, in order, without ever making the sound wait
    def _tell(self, gen, do, **args):
        """gen = the reply this belongs to (dropped if she was stopped since), or None = always deliver."""
        self.tells.put((gen, do, args))

    def _teller(self):
        while True:
            gen, do, args = self.tells.get()
            if gen is not None and gen != self.gen:
                continue
            avatar_client.send(do, timeout=2, **args)

    def _lips(self, on, gen=None):
        if on != self.lips:
            self.lips = on
            self._tell(gen, "anim", name="talk" if on else "idle")

    def _bubble(self, words, secs, gen):
        """Show the sentence being spoken, for as long as it is spoken (+ a moment to finish reading)."""
        if self.bubbles and words:
            self.bubble_until = time.time() + secs + 0.6
            self._tell(gen, "say", text=words, secs=round(secs + 0.6, 2))

    @staticmethod
    def _sentences(text):
        sentences = [x for x in re.split(r"(?<=[.!?।])\s+", text)        # sentence by sentence so "stop" is quick
                     if re.search(r"[^\W_]", x)]                            # (nothing to say in "?!" or "…")
        # A long opening sentence is cut in two, so she starts talking after preparing only a few words (the rest is
        # prepared while the first part plays): at its first comma, or else before a joining word ("... for 25
        # minutes | without touching ...").
        first = sentences[0] if sentences else ""
        if len(first) > 50:
            comma = re.search(r"[,;:—–]\s", first[:45])
            joiner = re.compile(r"\s(?=(?:and|but|so|because|without|while|which|then|or)\s)", re.I)
            at = comma.end() if comma and comma.end() > 8 else next(
                (m.end() for m in joiner.finditer(first) if 25 <= m.end() <= 80), None)
            if not at and len(first) > 70:
                # Still long ("Tea was introduced to India by the British East India Company in the 18th century,
                # starting..." took 3.6 s to make): a later comma, else before a preposition around 30-65 chars in.
                comma = re.search(r"[,;:—–]\s", first[20:65])
                prep = re.compile(r"\s(?=(?:by|in|with|for|from|to|on|at|of|into|during|after|before|through)\s)", re.I)
                at = (comma.end() + 20) if comma else next((m.end() for m in prep.finditer(first) if 30 <= m.end() <= 65), None)
            if at:
                sentences = [first[:at].strip(), first[at:].strip()] + sentences[1:]
        return sentences

    def _worker(self):
        import comtypes
        import comtypes.client as cc
        comtypes.CoInitialize()
        sp = cc.CreateObject("SAPI.SpVoice")
        tokens = []
        for cat_id in (ONECORE, None):  # OneCore voices (Heera, Ravi...) + classic ones
            try:
                if cat_id:
                    cat = cc.CreateObject("SAPI.SpObjectTokenCategory")
                    cat.SetId(cat_id, False)
                    toks = cat.EnumerateTokens()
                else:
                    toks = sp.GetVoices()
                tokens += [toks.Item(i) for i in range(toks.Count)]
            except Exception:
                pass  # intentional: an audio/OS call that has a fallback elsewhere
        self.voices = [t.GetDescription() for t in tokens]
        for pref in PREFERRED_VOICES:
            match = next((t for t in tokens if pref in t.GetDescription()), None)
            if match:
                sp.Voice = match
                break
        self.voice_name = sp.Voice.GetDescription()
        sp.Rate = 1
        if self.neural:
            try:
                self.neural.load()
                self.neural.synth("Hello.")          # warm up, so her first sentence isn't slow
                self.voice_name = f"Kokoro {NEURAL_VOICE} (natural voice)"
            except Exception:
                self.neural = None
        while True:
            text, said_at, show = self.q.get()
            self.stop_flag = False
            sentences = self._sentences(text)
            with self.lock:
                gen = self.gen
                self.pending += len(sentences)
            self.speaking = True
            left = sentences
            if self.neural:
                for i, sentence in enumerate(sentences):
                    if gen != self.gen:
                        left = []
                        break
                    t0 = time.time()
                    brain_balance.voice_needs_cpu(self.ahead() < VOICE_CUSHION)
                    try:
                        samples, rate = self.neural.synth(sentence)
                    except Exception:
                        left = sentences[i:]          # the natural voice failed: the Windows voice says the rest
                        break
                    finally:
                        brain_balance.voice_needs_cpu(False)    # sentence made: the brain gets its full share again
                    with self.lock:
                        self.ready_secs += len(samples) / rate
                    self.audio.put((gen, samples, rate, sentence, (said_at, t0, time.time()), show))
                else:
                    left = []
            if left:
                # Windows voice: wait for anything already prepared to finish, then speak with the lips moving.
                while not self.audio.empty() or (self.lips and gen == self.gen):
                    time.sleep(0.05)
                if gen == self.gen:
                    self._lips(True, gen)
                    if self.on_start:
                        self.on_start(left[0], f"Windows voice, {time.time() - said_at:.2f}s after the text")
                for sentence in left:
                    if self.stop_flag or gen != self.gen:
                        break
                    try:
                        if show:
                            self._bubble(sentence, len(sentence) / 14 + 0.3, gen)   # (no length known in advance)
                        sp.Speak(wording.DEVANAGARI.sub("", sentence), 0)   # (the Windows voice can't read Hindi script)
                    except Exception:
                        break
                    finally:
                        self._done_one(gen)
                with self.lock:
                    if gen == self.gen:
                        self.pending = max(0, self.pending - len(left))
                self._maybe_idle()

    def _done_one(self, gen):
        with self.lock:
            if gen == self.gen:
                self.pending = max(0, self.pending - 1)

    def _maybe_idle(self):
        """Nothing being said right now: close her mouth. (Called after a short wait for the next sentence.)"""
        if self.audio.empty() and self.pending == 0 and self.q.empty():
            self.speaking = False
            self.last_end = time.time()
        self._lips(False)

    def _player(self):
        import numpy as np
        import sounddevice as sd
        first_of_reply = True
        n_said = 0
        stream = None
        try:   # open the speaker now, so even her very first sentence doesn't wait for it (kept open from then on)
            stream = sd.OutputStream(samplerate=24000, channels=1, dtype="float32", latency="low")
            stream.start()
        except Exception:
            stream = None
        while True:
            try:
                gen, samples, rate, words, (said_at, synth_at, ready_at), show = self.audio.get(timeout=0.25)
            except queue.Empty:
                if self.lips and not self.stop_flag:
                    self._maybe_idle()          # next sentence not ready yet: lips stop during the pause
                if self.pending == 0 and self.q.empty():
                    first_of_reply = True
                continue
            secs = len(samples) / rate
            with self.lock:
                self.ready_secs = max(0.0, self.ready_secs - secs)
            if gen != self.gen:
                continue
            try:
                t_play = time.time()
                if not first_of_reply and t_play - self.play_until > 0.4 and self.on_gap:
                    # A pause in the middle of a reply: was the text late (brain) or the sound (voice)?
                    self.on_gap(t_play - self.play_until, "brain" if synth_at > self.play_until else "voice", words)
                self.play_until = t_play + secs
                # One output stream stays open between sentences: opening the sound device took ~0.33 s every time,
                # which delayed each sentence and left a gap between them.
                if stream is None or stream.samplerate != rate or not stream.active:
                    if stream is not None:
                        stream.close()
                    stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", latency="low")
                    stream.start()
                data = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
                chunk = int(rate * 0.05)          # small pieces, so stop() cuts her off within ~0.1 s
                line_start = None
                for i in range(0, len(data), chunk):
                    if gen != self.gen:
                        break
                    piece = self._ducked(data[i:i + chunk])
                    at = self._note_played(piece[:, 0], rate, stream.latency)
                    if line_start is None:
                        line_start = at
                        self.said_lines.append((at, at + secs, words))
                    stream.write(piece)
                    if i == 0:                    # the sound reaches the speaker one output latency from now
                        opened = time.time() - t_play
                        time.sleep(min(stream.latency, 0.1))
                        if show:
                            self._bubble(words, secs, gen)
                        self._lips(True, gen)
                        n_said = 0 if first_of_reply else n_said + 1
                        self._tell(gen, "speech", words=words, secs=round(secs, 2), n=n_said, fps=wording.SPEECH_FPS,
                                   env=wording.speech_envelope(data[:, 0], rate), start=time.time())
                        if first_of_reply and self.on_start:
                            self.on_start(words, f"text->synth {synth_at - said_at:.2f}s, synth {ready_at - synth_at:.2f}s, "
                                                 f"ready->play {t_play - ready_at:.2f}s, sound device {opened:.2f}s")
                        first_of_reply = False
                end = time.time() + stream.latency    # the last piece is still in the speaker's buffer
                while time.time() < end and gen == self.gen:
                    time.sleep(0.01)
            except Exception:
                try:                              # device unplugged / changed: reopen it for the next sentence
                    stream.close()
                except Exception:
                    pass  # intentional: an audio/OS call that has a fallback elsewhere
                stream = None
            finally:
                self._done_one(gen)
            if self.audio.empty() and self.pending == 0 and self.q.empty():
                self._maybe_idle()

    def duck(self, secs):
        """Talk much softer for a moment (BargeIn heard something: is the user saying something?)."""
        self.duck_until = max(self.duck_until, time.time() + secs)

    def _ducked(self, piece):
        """The piece at her current loudness. Going softer or back up fades over one piece (no click)."""
        import numpy as np
        target = DUCK_LEVEL if time.time() < self.duck_until else 1.0
        if target == 1.0 and self._gain == 1.0:
            return piece
        ramp = np.linspace(self._gain, target, len(piece), dtype=np.float32).reshape(-1, 1)
        self._gain = target
        return piece * ramp

    def _note_played(self, piece, rate, latency):
        """Remember when this piece of sound comes out of the speaker, and how loud it is (10 ms steps).
        The output stream stays open, so pieces written back to back play back to back: only after a pause
        (the stream ran dry) is the time worked out again from the clock. Returns when the piece starts."""
        import numpy as np
        now = time.time()
        start = self._out_end if self._out_end > now else now + latency
        step = max(1, int(rate * 0.01))
        for j in range(0, len(piece), step):
            self.played.append((start + j / rate, float(np.sqrt(np.mean(piece[j:j + step] ** 2)))))
        self._out_end = start + len(piece) / rate
        return start

    def echo_at(self, t0, t1):
        """The loudest she played between t0 and t1 (0.0 if she was silent)."""
        loud = 0.0
        for t, rms in reversed(list(self.played)):     # (a copy: the player adds to it from its own thread)
            if t < t0:
                break
            if t <= t1 and rms > loud:
                loud = rms
        return loud

    def words_between(self, t0, t1):
        """The words of every sentence she was saying at some point between t0 and t1."""
        return " ".join(w for s, e, w in list(self.said_lines) if e >= t0 and s <= t1)

    def ahead(self):
        """Seconds of speech ready to come out: the rest of this sentence + the ones already made."""
        return max(0.0, self.play_until - time.time()) + self.ready_secs

    def busy(self):
        return (self.speaking or self.pending > 0 or not self.q.empty() or not self.audio.empty()
                or time.time() - self.last_end < self.grace or time.time() < self.hold_until)

    def talking(self):
        """Is she saying something right now (or about to)? Unlike busy(), no grace period after the last word."""
        return (self.speaking or self.pending > 0 or not self.q.empty() or not self.audio.empty()
                or time.time() < self.hold_until)

    def hold(self, secs):
        """3D Nova is making a sound (a reaction's giggle, claps...): don't listen over it."""
        self.hold_until = max(self.hold_until, time.time() + secs)
