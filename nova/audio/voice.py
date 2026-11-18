"""Nova's voice and ears - fully offline, using what's built into Windows.

Speaking:  Windows speech voices (prefers "Heera - English (India)"), with 3D Nova moving her mouth.
Listening: Windows' offline speech recognizer waits for "Hey Nova", then writes down what you say next.
           Runs in a small PowerShell helper (listen.ps1); the microphone is only used while it's switched on.
"""
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

from nova.avatar import avatar_client
from nova.shared import ROOT

PREFERRED_VOICES = ("Heera", "Zira", "Aria", "Jenny")
ONECORE = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"


def _clean_for_speech(text):
    text = re.sub(r"[*_#`>|]", "", text)
    text = re.sub(r"\[spoken[^\]]*\]", "", text)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", text)  # emoji
    return " ".join(text.split())


# ---------------------------------------------------------------- a natural voice (Kokoro, offline, on the processor)
# Model files live outside the project (it sits in OneDrive): ~/.cache/nova-voice/  - see README for the download.
NEURAL_DIR = Path.home() / ".cache" / "nova-voice"
NEURAL_MODEL, NEURAL_VOICES = "kokoro-v1.0.int8.onnx", "voices-v1.0.bin"
NEURAL_VOICE = "hf_alpha"      # Hindi female voice; she also speaks English, with an Indian accent. Others: hf_beta
NEURAL_SPEED = 1.05
NEURAL_THREADS = 4             # Kokoro's CPU threads (see NeuralVoice.load)
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
# Nova's small model sometimes writes Hindi in English letters. Spelled that way the voice would read it as English
# words, so the commonest Hinglish words are put back into Hindi script before speaking.
HINGLISH_WORDS = {
    "hai": "है", "hain": "हैं", "ho": "हो", "hoon": "हूँ", "hun": "हूँ", "hu": "हूँ", "tha": "था", "thi": "थी", "the": None,
    "gaya": "गया", "gayi": "गई", "gaye": "गए", "raha": "रहा", "rahi": "रही", "rahe": "रहे", "kar": "कर", "karo": "करो",
    "karna": "करना", "karti": "करती", "karta": "करता", "kiya": "किया", "diya": "दिया", "liya": "लिया", "do": None,
    "dun": "दूँ", "doon": "दूँ", "lo": "लो", "le": "ले", "de": "दे", "ja": "जा", "jao": "जाओ", "aa": "आ", "aao": "आओ",
    "kya": "क्या", "kyun": "क्यों", "kyon": "क्यों", "kaise": "कैसे", "kaisa": "कैसा", "kaisi": "कैसी", "kab": "कब",
    "kahan": "कहाँ", "kaun": "कौन", "kitna": "कितना", "kitni": "कितनी", "kitne": "कितने", "nahi": "नहीं", "nahin": "नहीं",
    "mat": "मत", "haan": "हाँ", "han": "हाँ", "ji": "जी", "yaar": "यार", "yar": "यार", "arey": "अरे", "arre": "अरे",
    "are": None, "bas": "बस", "abhi": "अभी", "ab": "अब", "phir": "फिर", "fir": "फिर", "toh": "तो", "to": None,
    "aur": "और", "ya": "या", "par": "पर", "lekin": "लेकिन", "ki": "की", "ka": "का", "ke": "के", "ko": "को", "se": "से",
    "mein": "में", "me": None, "main": None, "mai": "मैं", "mujhe": "मुझे", "mera": "मेरा", "meri": "मेरी", "mere": "मेरे",
    "tum": "तुम", "tumhara": "तुम्हारा", "tumhari": "तुम्हारी", "aap": "आप", "aapka": "आपका", "aapki": "आपकी", "hum": "हम",
    "yeh": "यह", "ye": "ये", "woh": "वह", "wo": "वो", "is": None, "us": None, "kuch": "कुछ", "sab": "सब", "bahut": "बहुत",
    "thoda": "थोड़ा", "thodi": "थोड़ी", "zyada": "ज़्यादा", "kam": "कम", "accha": "अच्छा", "acha": "अच्छा", "achha": "अच्छा",
    "theek": "ठीक", "thik": "ठीक", "sahi": "सही", "bilkul": "बिल्कुल", "ekdum": "एकदम", "chal": "चल", "chalo": "चलो",
    "chalega": "चलेगा", "khul": "खुल", "kholo": "खोलो", "band": None, "bachi": "बची", "baje": "बजे", "baj": "बज",
    "hua": "हुआ", "hui": "हुई", "hue": "हुए", "hoga": "होगा", "hogi": "होगी", "chahiye": "चाहिए", "chahe": "चाहे",
    "batao": "बताओ", "bata": "बता", "dekho": "देखो", "dekh": "देख", "suno": "सुनो", "sunao": "सुनाओ", "bolo": "बोलो",
    "lag": "लग", "laga": "लगा", "mil": "मिल", "mila": "मिला", "kaam": "काम", "baat": "बात", "din": "दिन", "raat": "रात",
    "aaj": "आज", "kal": "कल", "chai": "चाय", "dost": "दोस्त", "bhai": "भाई", "namaste": "नमस्ते", "shukriya": "शुक्रिया",
    "dhanyavaad": "धन्यवाद", "maaf": "माफ़", "zaroor": "ज़रूर", "tension": None, "mast": "मस्त", "badhiya": "बढ़िया",
    "khana": "खाना", "khaana": "खाना", "pani": "पानी", "ghar": "घर", "wala": "वाला", "wali": "वाली", "waise": "वैसे",
    "matlab": "मतलब", "shayad": "शायद", "sirf": "सिर्फ़", "tak": "तक", "liye": "लिए", "saath": "साथ", "bina": "बिना",
    "tayyar": "तैयार", "taiyar": "तैयार", "khada": "खड़ा", "khadi": "खड़ी", "gaana": "गाना", "gana": "गाना",
}
HINGLISH_WORDS.update({
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
HINGLISH_WORDS = {k: v for k, v in HINGLISH_WORDS.items() if v}     # (None = also a common English word; leave it)
# A sentence counts as Hinglish (worth converting) when it holds at least two of these giveaway words.
HINGLISH_SURE = {"hai", "hain", "hoon", "nahi", "nahin", "kya", "yaar", "arey", "arre", "gaya", "raha", "rahi", "aur",
                 "bahut", "abhi", "toh", "kuch", "mein", "ka", "ki", "ke", "ko", "karo", "ji", "bas", "hua", "accha", "theek"}


def _hindi_script(text):
    """'Internet chal raha hai, boss' -> 'Internet चल रहा है, boss' (only when the sentence is clearly Hinglish)."""
    words = re.findall(r"[A-Za-z']+", text)
    if sum(w.lower() in HINGLISH_SURE for w in words) < 2:
        return text
    return re.sub(r"[A-Za-z']+", lambda m: HINGLISH_WORDS.get(m.group(0).lower(), m.group(0)), text)


_brain_niced = {"at": 0.0, "pids": set()}


def voice_first():
    """Her voice goes before her brain: Ollama starts its model runner (llama-server, which does ~1/4 of the model
    on the processor here) at ABOVE-normal priority, so it got the cores first while she was speaking. Put it below
    normal - the user wants her to talk without pauses; slower thinking is fine. Measured 2026-11-18: a 3.6 s
    sentence took 1.3-2.3 s to make next to the brain, 1.2-1.5 s once the brain was below normal. Ollama starts a
    new runner when a model (re)loads, so this is checked again every 20 s, from the voice thread (cheap)."""
    now = time.time()
    if now - _brain_niced["at"] < 20:
        return
    _brain_niced["at"] = now

    def scan():          # (listing processes takes ~50-100 ms: not in front of her sentence)
        try:
            import psutil
            for p in psutil.process_iter(["name"]):
                if (p.info["name"] or "").lower() in ("llama-server.exe", "ollama_llama_server.exe") \
                        and p.pid not in _brain_niced["pids"]:
                    p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                    _brain_niced["pids"].add(p.pid)
        except Exception:
            pass
    threading.Thread(target=scan, daemon=True).start()


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
        voice_first()
        k = self.load()
        text = _hindi_script(text)
        # Clock times: "09:29" would be read "zero nine 29"; "9:00" as "nine zero zero".
        text = re.sub(r"\b0?(\d{1,2}):00\b", r"\1", text)
        text = re.sub(r"\b0?(\d{1,2}):(\d{2})\b", r"\1 \2", text)
        if DEVANAGARI.search(text):
            # Sound it out as Hindi; the English words inside are sounded as English automatically. The engine
            # marks those switches with "(en)" / "(hi)" - which must be removed or they are read out loud.
            ph = re.sub(r"\((?:en|hi)[a-z\-]*\)", " ", k.tokenizer.phonemize(text, "hi"))
        else:
            ph = k.tokenizer.phonemize(text, "en-us")
        return k.create(" ".join(ph.split()), voice=NEURAL_VOICE, speed=NEURAL_SPEED, is_phonemes=True)


SPEECH_FPS = 30     # loudness samples per second sent to 3D Nova with each sentence


def speech_envelope(samples, rate):
    """How loud a sentence is, SPEECH_FPS times a second, 0..1 (for 3D Nova's mouth and body while she speaks)."""
    import numpy as np
    step = max(1, int(rate / SPEECH_FPS))
    n = len(samples) // step
    if n == 0:
        return []
    rms = np.sqrt(np.mean(np.square(np.asarray(samples[:n * step], dtype=np.float32).reshape(n, step)), axis=1))
    top = float(np.percentile(rms, 95)) or 1.0
    return [round(float(v), 2) for v in np.clip(rms / top, 0, 1)]


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
        self.bubbles = True        # show each sentence in 3D Nova's speech bubble while it is spoken
        self.bubble_until = 0.0
        self.tells = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._player, daemon=True).start()
        threading.Thread(target=self._teller, daemon=True).start()

    def say(self, text):
        text = _clean_for_speech(text or "")
        if text:
            self.q.put((text, time.time()))

    def stop(self):
        self.stop_flag = True
        with self.lock:
            self.gen += 1
            self.pending = 0
        while not self.q.empty():
            self.q.get_nowait()
        while not self.audio.empty():
            self.audio.get_nowait()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
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
        sentences = [x for x in re.split(r"(?<=[.!?।])\s+", text) if x.strip()]  # sentence by sentence so "stop" is quick
        # A long opening sentence is cut in two, so she starts talking after preparing only a few words (the rest is
        # prepared while the first part plays): at its first comma, or else before a joining word ("... for 25
        # minutes | without touching ...").
        first = sentences[0] if sentences else ""
        if len(first) > 50:
            comma = re.search(r"[,;:—–]\s", first[:45])
            joiner = re.compile(r"\s(?=(?:and|but|so|because|without|while|which|then|or)\s)", re.I)
            at = comma.end() if comma and comma.end() > 8 else next(
                (m.end() for m in joiner.finditer(first) if 25 <= m.end() <= 80), None)
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
                pass
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
            text, said_at = self.q.get()
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
                    try:
                        samples, rate = self.neural.synth(sentence)
                    except Exception:
                        left = sentences[i:]          # the natural voice failed: the Windows voice says the rest
                        break
                    self.audio.put((gen, samples, rate, sentence, (said_at, t0, time.time())))
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
                        self._bubble(sentence, len(sentence) / 14 + 0.3, gen)   # (no length known in advance)
                        sp.Speak(DEVANAGARI.sub("", sentence), 0)   # (the Windows voice can't read Hindi script)
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
                gen, samples, rate, words, (said_at, synth_at, ready_at) = self.audio.get(timeout=0.25)
            except queue.Empty:
                if self.lips and not self.stop_flag:
                    self._maybe_idle()          # next sentence not ready yet: lips stop during the pause
                if self.pending == 0 and self.q.empty():
                    first_of_reply = True
                continue
            if gen != self.gen:
                continue
            try:
                secs = len(samples) / rate
                t_play = time.time()
                # One output stream stays open between sentences: opening the sound device took ~0.33 s every time,
                # which delayed each sentence and left a gap between them.
                if stream is None or stream.samplerate != rate or not stream.active:
                    if stream is not None:
                        stream.close()
                    stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", latency="low")
                    stream.start()
                data = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
                chunk = int(rate * 0.05)          # small pieces, so stop() cuts her off within ~0.1 s
                for i in range(0, len(data), chunk):
                    if gen != self.gen:
                        break
                    stream.write(data[i:i + chunk])
                    if i == 0:                    # the sound reaches the speaker one output latency from now
                        opened = time.time() - t_play
                        time.sleep(min(stream.latency, 0.1))
                        self._bubble(words, secs, gen)
                        self._lips(True, gen)
                        n_said = 0 if first_of_reply else n_said + 1
                        self._tell(gen, "speech", words=words, secs=round(secs, 2), n=n_said, fps=SPEECH_FPS,
                                   env=speech_envelope(data[:, 0], rate), start=time.time())
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
                    pass
                stream = None
            finally:
                self._done_one(gen)
            if self.audio.empty() and self.pending == 0 and self.q.empty():
                self._maybe_idle()

    def busy(self):
        return (self.speaking or self.pending > 0 or not self.q.empty() or not self.audio.empty()
                or time.time() - self.last_end < 0.8 or time.time() < self.hold_until)

    def talking(self):
        """Is she saying something right now (or about to)? Unlike busy(), no grace period after the last word."""
        return (self.speaking or self.pending > 0 or not self.q.empty() or not self.audio.empty()
                or time.time() < self.hold_until)

    def hold(self, secs):
        """3D Nova is making a sound (a reaction's giggle, claps...): don't listen over it."""
        self.hold_until = max(self.hold_until, time.time() + secs)


LISTEN_PS1 = r"""
param([string]$Mode = "dictation")
Add-Type -AssemblyName System.Speech
$culture = New-Object System.Globalization.CultureInfo("en-US")
$r = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
try { $r.SetInputToDefaultAudioDevice() } catch { [Console]::Out.WriteLine("ERROR:no microphone"); exit 1 }
$c = New-Object System.Speech.Recognition.Choices
$c.Add([string[]]@("hey nova", "hi nova", "hello nova", "okay nova", "ok nova", "nova", "hey noah", "hey nover",
  "nova wake up", "wake up nova", "hey nova wake up", "nova are you there",
  "nova go to sleep", "go to sleep nova", "nova sleep", "nova stop listening", "good night nova"))
$gb = New-Object System.Speech.Recognition.GrammarBuilder
$gb.Culture = $culture
$gb.Append($c)
$wake = New-Object System.Speech.Recognition.Grammar($gb)
$wake.Name = "wake"
$dict = New-Object System.Speech.Recognition.DictationGrammar
$dict.Name = "dict"
# Common requests as exact phrases: the recognizer is far more accurate on these than on free dictation.
$cc = New-Object System.Speech.Recognition.Choices
$cc.Add([string[]](Get-Content -Path "$PSScriptRoot\phrases.txt" -Encoding UTF8))
$cgb = New-Object System.Speech.Recognition.GrammarBuilder
$cgb.Culture = $culture
$cgb.Append($cc)
$cmd = New-Object System.Speech.Recognition.Grammar($cgb)
$cmd.Name = "cmd"
$cmd.Weight = 1.0
$dict.Weight = 0.5
# Modern Windows recognizer (much more accurate) for the request after the wake word. Needs
# Settings > Privacy & security > Speech > "Online speech recognition" switched on; otherwise we fall back.
$rt = $null
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $null = [Windows.Media.SpeechRecognition.SpeechRecognizer, Windows.Media.SpeechRecognition, ContentType = WindowsRuntime]
  $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
      $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  function Await($op, [Type]$t, $ms = 15000) {
    $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op))
    if (-not $task.Wait($ms)) { try { $op.Cancel() } catch { }; throw "timed out after $($ms / 1000) s" }
    $task.Result
  }
  $rt = New-Object Windows.Media.SpeechRecognition.SpeechRecognizer
  $rt.Timeouts.InitialSilenceTimeout = [TimeSpan]::FromSeconds(6)
  $rt.Timeouts.EndSilenceTimeout = [TimeSpan]::FromMilliseconds(1000)
  $null = Await ($rt.CompileConstraintsAsync()) ([Windows.Media.SpeechRecognition.SpeechRecognitionCompilationResult])
} catch { $rt = $null }
function OnlineOK { ((Get-ItemProperty "HKCU:\Software\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy" -ErrorAction SilentlyContinue).HasAccepted -eq 1) }
$r.InitialSilenceTimeout = [TimeSpan]::FromSeconds(6)
$r.EndSilenceTimeout = [TimeSpan]::FromMilliseconds(900)
# A wake-only grammar forces any sound onto the nearest phrase ("nova"), so a hum or a chair could pass the old 0.3
# limit; 0.5 asks for her name said clearly (anything between shows as "almost heard" in her status notes).
$r.LoadGrammar($wake)
[Console]::Out.WriteLine("READY"); [Console]::Out.Flush()
while ($true) {
  $res = $null
  try { $res = $r.Recognize([TimeSpan]::FromSeconds(20)) } catch { }
  if ($res -and $res.Grammar.Name -eq "wake" -and $res.Confidence -lt 0.5) {
    [Console]::Out.WriteLine("LOW:" + [math]::Round($res.Confidence, 2) + "|" + $res.Text); [Console]::Out.Flush()
  }
  if ($res -and $res.Grammar.Name -eq "wake" -and $res.Confidence -ge 0.5) {
    if ($res.Text -match "sleep|stop listening|good night") { [Console]::Out.WriteLine("SLEEP"); [Console]::Out.Flush(); continue }
    if ($res.Text -match "wake up|are you there") { [Console]::Out.WriteLine("WAKEUP"); [Console]::Out.Flush(); continue }
    [Console]::Out.WriteLine("WAKE"); [Console]::Out.Flush()
    # In the desktop app, Nova hands the request to Windows voice typing (accurate) - nothing more to do here.
    if ($Mode -eq "typing") { continue }
    if ($rt -and (OnlineOK)) {
      $done = $false
      try {
        $res = Await ($rt.RecognizeAsync()) ([Windows.Media.SpeechRecognition.SpeechRecognitionResult])
        if ($res.Status -eq "Success" -and $res.Text) {
          $conf = @{ "High" = 0.95; "Medium" = 0.8; "Low" = 0.5; "Rejected" = 0.1 }[[string]$res.Confidence]
          [Console]::Out.WriteLine("TEXT:rt|" + $conf + "|" + $res.Text)
        } else { [Console]::Out.WriteLine("INFO:accurate recognizer heard no words (status " + $res.Status + ")"); [Console]::Out.WriteLine("NOTHING") }
        $done = $true
      } catch {
        $m = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
        [Console]::Out.WriteLine("INFO:accurate recognizer failed (" + $m.Trim() + ") - using basic one")
      }
      [Console]::Out.Flush()
      if ($done) { continue }
    } else {
      [Console]::Out.WriteLine("INFO:online-off"); [Console]::Out.Flush()
    }
    $r.UnloadAllGrammars(); $r.LoadGrammar($cmd); $r.LoadGrammar($dict)
    $said = $null
    try { $said = $r.Recognize([TimeSpan]::FromSeconds(12)) } catch { }
    if ($said -and $said.Text) {
      $conf = [math]::Round($said.Confidence, 2)
      [Console]::Out.WriteLine("TEXT:" + $said.Grammar.Name + "|" + $conf + "|" + $said.Text)
    } else { [Console]::Out.WriteLine("NOTHING") }
    [Console]::Out.Flush()
    $r.UnloadAllGrammars(); $r.LoadGrammar($wake)
  }
}
"""


RT_PS1 = r"""
# Background transcriber: Windows' modern speech recognizer, no window. Reads "LISTEN" lines, writes one result each.
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechRecognition.SpeechRecognizer, Windows.Media.SpeechRecognition, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t, $ms) {
  $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op))
  if (-not $task.Wait($ms)) { try { $op.Cancel() } catch { }; throw "timed out" }
  $task.Result
}
try {
  $rt = New-Object Windows.Media.SpeechRecognition.SpeechRecognizer
  $rt.Timeouts.InitialSilenceTimeout = [TimeSpan]::FromSeconds(8)
  $rt.Timeouts.EndSilenceTimeout = [TimeSpan]::FromMilliseconds(1100)
  $c = Await ($rt.CompileConstraintsAsync()) ([Windows.Media.SpeechRecognition.SpeechRecognitionCompilationResult]) 20000
  [Console]::Out.WriteLine("READY:" + $c.Status); [Console]::Out.Flush()
} catch { [Console]::Out.WriteLine("ERROR:" + $_.Exception.Message); [Console]::Out.Flush(); exit 1 }
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($line -eq $null) { break }
  if ($line -ne "LISTEN") { continue }
  try {
    $res = Await ($rt.RecognizeAsync()) ([Windows.Media.SpeechRecognition.SpeechRecognitionResult]) 25000
    [Console]::Out.WriteLine("RESULT:" + $res.Status + "|" + $res.Confidence + "|" + $res.Text)
  } catch {
    $m = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
    [Console]::Out.WriteLine("ERROR:" + ($m -replace "\s+", " ").Trim())
  }
  [Console]::Out.Flush()
}
"""


class Transcriber:
    """Hidden listening: hands one utterance at a time to Windows' modern recognizer. No windows, no voice typing.
    Needs Settings > Privacy & security > Speech > Online speech recognition = On."""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.failures = 0      # in a row; after a few, the app falls back to the voice-typing bubble
        self.lines = queue.Queue()
        self.rejected = ""     # the last words thrown away as noise, and why (for the status notes)

    def start(self):
        if self.proc and self.proc.poll() is None:
            return True
        script = ROOT / "data" / "rt_listen.ps1"
        script.parent.mkdir(exist_ok=True)
        script.write_text(RT_PS1, encoding="utf-8")
        kill_strays("rt_listen.ps1")   # Windows' speech service only serves one recognizer well at a time
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self.proc, self.lines), daemon=True).start()
        try:
            first = self.lines.get(timeout=30)
        except queue.Empty:
            first = "ERROR:didn't start"
        if not first.startswith("READY"):
            self.stop()
            return False
        return True

    @staticmethod
    def _pump(proc, q):
        for line in proc.stdout:
            q.put(line.strip())

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None

    @staticmethod
    def doubt(text, conf):
        """Why a result from Windows' recognizer is probably noise, or "". conf: High, Medium, Low or Rejected.
        A short "Low" result is usually a hum or a door the recognizer forced into words; longer "Low" sentences
        are often real speech it heard imperfectly, so they stay (her brain is told it may have misheard)."""
        why = noise_words(text)
        if why:
            return why
        if conf == "Low" and len(_words(text)) <= 2 and not SHORT_REPLY.match(text):
            return "not sure enough"
        return ""

    def listen(self):
        """Wait for one utterance. Returns (kind, text): kind is 'text', 'silence', 'unclear' or 'error'."""
        self.rejected = ""
        with self.lock:
            if not self.start():
                self.failures += 1
                return "error", "the speech recognizer didn't start"
            try:
                self.proc.stdin.write("LISTEN\n")
                self.proc.stdin.flush()
                line = self.lines.get(timeout=35)
            except Exception as e:
                self.stop()
                self.failures += 1
                return "error", str(e)
        if line.startswith("RESULT:"):
            status, conf, text = (line[7:].split("|", 2) + ["", ""])[:3]
            if status == "Success" and text.strip() and conf != "Rejected":
                self.failures = 0
                why = self.doubt(text, conf)
                if why:
                    self.rejected = f"“{text.strip()[:40]}” ({why})"
                    return "unclear", status
                return "text", text.strip()
            if status in ("TimeoutExceeded", "Success", "UserCanceled", "PauseLimitExceeded"):
                self.failures = 0
                return "silence", status   # nobody spoke
            if status == "Unknown":        # it heard sound (noise, mumbling) but couldn't make out words
                self.failures = 0
                return "unclear", status
            self.failures += 1
            return "error", status
        self.failures += 1
        return "error", line[6:] if line.startswith("ERROR:") else line


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
        pass


WHISPER_HINT = ("Open Chrome, open VS Code, open WhatsApp, YouTube, Spotify. "
                "What's on my screen? Check my battery. Volume up.")
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
            pass

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
NOVA_NAMES = r"(?:nova|novah|noah|nover|nava|nowa|no va|नोवा|नौवा|नोआ)"
# "Hey Nova ..." anywhere, or a sentence that starts with "Nova ..."
# Nova's name as the first or second word ("Nova, ...", "Hey Nova ...", "Hei Nova", "Ok Nova", "Arey Nova", "हे नोवा"),
# or stuck to the greeting ("HeyNova"), or "hey/ok Nova" anywhere in the sentence. Whisper spells the greeting many
# ways, especially in Hindi mode, so the greeting itself is not checked.
WAKE_RE = re.compile(rf"^\W*(?:\S+\s+)?(?:he[yi]|hi|ok)?{NOVA_NAMES}(?!\w)"
                     rf"|\b(?:he[yi]|hi|hai|hello|okay|ok|arey)\W*{NOVA_NAMES}(?!\w)"
                     # A soft "Hey Nova" on its own can come out as "See you, Noah." - a short phrase that ends in
                     # her name is a call too.
                     rf"|^\W*(?:\S+\W+){{0,2}}{NOVA_NAMES}\W*$", re.I)
EARS_SLEEP_RE = re.compile(r"go (?:back )?to sleep|back to sleep|stop listening|good ?night|so ?jao|so ?ja\b|sunna band|shubh ratri|सो जा", re.I)
EARS_WAKEUP_RE = re.compile(r"wake up|are you there|uth ?jao|\butho\b|jaag ?jao|\bjaago\b|उठ|जाग", re.I)


class WhisperTranscriber:
    """Offline listening with OpenAI's Whisper (faster-whisper, CPU). Records one utterance from the default
    microphone (starts when you speak, ends after a short pause) and writes it down. Same listen() results as
    Transcriber, so the app can use either. Nothing leaves the laptop."""

    RATE = 16000
    BLOCK = 0.03          # seconds per audio block
    START_WAIT = 8.0      # how long to wait for you to start talking
    END_PAUSE = 0.9       # this much quiet after speech = you're done
    # Loudness (RMS of a 30 ms block) that counts as speech. Measured 2026-11-14: a silent room reads ~0.00005 on this
    # laptop's mic array, and normal-volume speech often stays under the old fixed 0.0015 start / 0.001 keep-going
    # levels, so "Hey Nova" had to be said loudly and soft requests were cut off. The levels now follow the room's
    # own noise (x3.5 to start, x2.5 to keep going) with much lower fixed minimums; Silero VAD + Whisper still
    # reject clicks and bumps afterwards.
    START_MIN = 0.0003    # ~2x the room's loudest background blips (p90 0.00017), 6x its median
    KEEP_MIN = 0.00025
    MAX_LEN = 20.0

    def __init__(self):
        self.model = None
        self.lock = threading.Lock()
        self.waiting = 0       # someone wants an answer now (listen()); background wake-word listening steps aside
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
        self.skip = 0          # how many of WHISPER_CHOICES have been given up on
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
        with self.gpu:
            return self._decode_now(audio, hint, force)

    def _decode_now(self, audio, hint, force=None):
        model = self.load()
        language = "en"
        if model.model.is_multilingual and not force and not ENGLISH_ONLY:   # (small.en backup only knows English)
            _, _, probs = model.detect_language(audio)
            if dict(probs).get("en", 0.0) < ENGLISH_SURE:
                language, hint = "hi", HINGLISH_HINT
        self.language = language
        segs, _ = model.transcribe(audio, language=language, beam_size=3, vad_filter=True,
                                   initial_prompt=hint, condition_on_previous_text=False)
        # (the reading happens here) compression_ratio > 2.4 = the same words over and over ("open, open, open")
        return [s for s in segs if s.no_speech_prob < 0.6 and s.avg_logprob > -1.2 and s.compression_ratio < 2.4]

    def _record(self, start_wait, max_len, abort=None, peek=None, end_pause=None, hold=None):
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
                    floor = rms
                if not started:
                    chunks = (chunks + [block])[-12:]               # keep ~0.35 s from before speech starts
                    if rms > max(self.START_MIN, floor * 3.5):
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
                if rms < max(self.KEEP_MIN, floor * 2.5):
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
        speech = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=500))
        if sum(s["end"] - s["start"] for s in speech) < 0.25 * self.RATE:
            return ""
        try:
            segs = self._decode(audio, hint)
        except Exception:
            if self.skip + 1 >= len(WHISPER_CHOICES):
                raise
            # The graphics card gave up mid-sentence (usually: out of memory). Drop to the next model and redo it.
            self.model, self.skip = None, self.skip + 1
            segs = self._decode(audio, hint)
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

    def quick_english(self, audio):
        """A fast, rough reading of a short piece of audio as English (about 0.2 s on the graphics card). Used to
        spot "Hey Nova" while the sentence is still being spoken."""
        import numpy as np
        if not self.gpu.acquire(blocking=False):      # the full reading is running: skip this early look
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

    def hear(self, start_wait, max_len, hint=None, abort=None, peek=None, end_pause=None, hold=None, strict=False):
        """One utterance. Returns (kind, text): 'text', 'silence' (nobody spoke), 'unclear' (sound, no words), 'error'.
        strict: it will be taken as a request without "Hey Nova", so noise must not get through as words."""
        with self.lock:
            try:
                self.load()
                self.rejected = ""
                audio = self._record(start_wait, max_len, abort, peek, end_pause, hold)
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
        self.waiting += 1
        try:
            kind, text = self.hear(self.START_WAIT, self.MAX_LEN, WHISPER_HINT, strict=True)
        finally:
            self.waiting -= 1
        self.failures = self.failures + 1 if kind == "error" else 0
        return kind, text


class _EarsBase:
    """What both listeners share: the status line and the last few things heard (for the chat's status screen)."""
    status = "off"
    on_wake = None             # set by the app: listen for the request (conversation=True for "wake up")
    on_sleep = None            # set by the app: end conversation mode

    def _note(self, what):
        self.heard = (self.heard + [f"{time.strftime('%H:%M:%S')} {what}"])[-10:]

    def _woke_for_conversation(self):
        self._note("heard 'Nova, wake up' - conversation mode")
        if getattr(self, "on_name", None):
            self.on_name()
        avatar_client.send("wave", quiet=True, timeout=2)
        if self.on_wake:
            threading.Thread(target=self.on_wake, kwargs={"conversation": True}, daemon=True).start()

    def _asked_to_sleep(self):
        self._note("heard 'Nova, go to sleep'")
        if self.on_sleep:
            threading.Thread(target=self.on_sleep, daemon=True).start()


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
        self.quiet_until = 0.0
        self.online_off = False
        self.skip_next = False
        self.running = False
        self.thread = None
        self.early = 0.0           # when her name was last spotted mid-sentence (the bubble is already showing)
        self.early_bare = False    # ...and nothing but her name had been said at that point
        self.peeking = False

    def _named(self):
        if self.on_name:
            try:
                self.on_name()
            except Exception:
                pass

    def _peek(self, audio):
        """Runs while the user is still speaking: if the sentence starts with her name, react right away."""
        if self.peeking or self.early > time.time() - 6 or self._step_aside_quiet():
            return
        self.peeking = True
        try:
            text = self.w.quick_english(audio)
            m = WAKE_RE.search(text)
            if m and not EARS_SLEEP_RE.search(text) and not EARS_WAKEUP_RE.search(text):
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
        if len(rest.split()) >= 2 and not EARS_SLEEP_RE.search(rest) and not EARS_WAKEUP_RE.search(rest):
            return WhisperTranscriber.doubt(rest, sure, None, expecting=False)
        return "not sure enough" if sure < SURE_WAKE else ""

    def _step_aside_quiet(self):
        return not self.running or self.mouth.busy() or bool(self.paused and self.paused())

    def set_mode(self, mode):
        pass   # one way of listening only

    def start(self):
        if self.thread and self.thread.is_alive() and self.running:
            return
        kill_strays("listen.ps1")   # the old Windows listener must not answer "Hey Nova" as well
        self.running = True
        self.status = "starting"
        self.thread = threading.Thread(target=self._loop, daemon=True)
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
            kind, text = self.w.hear(30.0, 12.0, None, abort=self._step_aside, peek=self._peek, end_pause=0.7,
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
                if not WAKE_RE.search(text) and (early or self.w.language != "en"):
                    english = "Nova" if early else self.w.again_in_english()
                    if WAKE_RE.search(english):
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
        m = WAKE_RE.search(text)
        if not m:
            # Not for Nova. Only the start of the latest one is kept (in memory, replaced each time), so that
            # "she doesn't react" can be worked out from the status screen.
            self.heard = [h for h in self.heard if "didn't start with" not in h]
            self._note(f"heard speech that didn't start with 'Hey Nova': “{text[:50]}” (mic level {self.w.level:.2f})")
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
        if not EARS_SLEEP_RE.search(low):
            self._named()
        if EARS_SLEEP_RE.search(low):
            self._asked_to_sleep()
        elif EARS_WAKEUP_RE.search(low):
            self._woke_for_conversation()
        elif len(rest.split()) >= 2:                       # "Hey Nova, what's the time?" in one breath
            self._note(f"heard 'Hey Nova' + request (mic level {self.w.level:.2f}): {rest}"[:160])
            if not early:
                avatar_client.send("wave", quiet=True, timeout=2)
            avatar_client.send("say", text=f"“{rest}”", secs=4, timeout=2)
            threading.Thread(target=self.on_text, args=(rest,), daemon=True).start()
        else:
            self._note(f"heard 'Hey Nova' (mic level {self.w.level:.2f})")
            if not early:     # (if early, the same bubble is up already and the app's listener keeps it up)
                avatar_client.send("wave", quiet=True, timeout=2)
                avatar_client.send("listen", on=True, secs=6, timeout=2)
            if self.on_wake:
                threading.Thread(target=self.on_wake, daemon=True).start()


def kill_strays(script_name):
    """Stop leftover copies of a helper script (e.g. from a crashed or test run of Nova)."""
    import psutil
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() != "powershell.exe":
                continue
            args = p.info["cmdline"] or []
            if any(a.replace("/", "\\").lower().endswith("\\" + script_name.lower()) for a in args):
                p.terminate()
        except Exception:
            pass


def online_speech_on():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy") as k:
            return winreg.QueryValueEx(k, "HasAccepted")[0] == 1
    except OSError:
        return False


def command_phrases():
    """Everyday things people say to Nova. Matched exactly, these are recognized much more reliably."""
    base = [
        "what time is it", "what's the time", "what's the date today", "what day is it",
        "how is my laptop doing", "how's my laptop", "check my battery", "how much battery do I have",
        "am I connected to the internet", "check my wifi", "why is my laptop slow", "what's using my memory",
        "volume up", "volume down", "turn the volume up", "turn the volume down", "mute", "unmute",
        "play music", "pause music", "play", "pause", "next song", "previous song",
        "brightness up", "brightness down", "lock my laptop", "lock the screen", "take a screenshot",
        "show the desktop", "what windows are open", "turn on dark mode", "turn off dark mode",
        "clean temp files", "how much space do I have", "find big files", "organize my downloads",
        "read my notes", "list my notes", "how do I look", "what am I holding", "look at me",
        "what's on my screen", "look at my screen", "read this error", "what am I looking at", "what's open",
        "tell me a joke", "how are you", "thank you", "good morning", "good night", "stop", "never mind",
        "cancel the shutdown", "put the laptop to sleep",
    ]
    apps = ["notepad", "calculator", "chrome", "edge", "file explorer", "settings", "task manager",
            "vs code", "spotify", "youtube", "whatsapp", "word", "excel", "powerpoint", "camera", "paint"]
    return base + [f"open {a}" for a in apps] + [f"close {a}" for a in apps]


class Ears(_EarsBase):
    """'Hey Nova' wake word + dictation. on_text(text) is called with what the user said."""

    def __init__(self, mouth, on_text):
        self.mouth, self.on_text = mouth, on_text
        self.proc = None
        self.status = "off"
        self.heard = []  # last few things the ears noticed, for the status screen and testing
        self.skip_next = False
        self.online_off = False
        self.on_wake = None        # set by the app: show the listening bubble and start Windows voice typing
        self.on_sleep = None       # set by the app: end conversation mode
        self.quiet_until = 0.0     # ignore the wake word while the user is busy talking to voice typing

    def set_mode(self, mode):
        """'typing' = after the wake word the app does the listening (desktop app: silent recognizer or bubble).
        'dictation' = the listener transcribes the request itself with the old built-in recognizer (fallback).
        Takes effect the next time the listener starts."""
        self.mode = mode

    mode = "dictation"

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        script = ROOT / "data" / "listen.ps1"
        script.write_text(LISTEN_PS1, encoding="utf-8")
        (ROOT / "data" / "phrases.txt").write_text("\n".join(command_phrases()), encoding="utf-8")
        kill_strays("listen.ps1")   # only one wake-word listener may run, or two of them answer at once
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Mode", self.mode],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.status = "starting"
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None
        self.status = "off"

    def _read(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if line == "READY":
                self.status = "listening"
            elif line.startswith("ERROR:"):
                self.status = line[6:]
            elif line.startswith("INFO:"):
                info = line[5:]
                if info == "online-off":
                    self.online_off = True
                    self._note("using the basic recognizer - turn on Online speech recognition for accuracy")
                else:
                    self._note(info)
            elif line.startswith("LOW:"):
                conf, text = (line[4:].split("|", 1) + [""])[:2]
                self._note(f"almost heard '{text}' ({float(conf or 0):.0%} sure) - try a bit louder/closer")
            elif line == "SLEEP":
                self._asked_to_sleep()
            elif line == "WAKEUP":
                if self.mouth.busy():
                    self._note("'wake up' ignored (I was talking)")
                    continue
                self._woke_for_conversation()
            elif line == "WAKE":
                if self.mouth.busy():   # that was probably her own voice
                    self._note("wake word ignored (I was talking)")
                    self.skip_next = True   # and don't act on whatever gets transcribed after it
                    continue
                if time.time() < self.quiet_until:
                    continue   # the user is mid-sentence in voice typing; "nova" in their words isn't a new wake
                self.skip_next = False
                self._note("heard 'Hey Nova'")
                avatar_client.send("wave", quiet=True, timeout=2)
                if self.mode == "typing" and self.on_wake:
                    self.quiet_until = time.time() + 20
                    avatar_client.send("listen", on=True, secs=6, timeout=2)
                    threading.Thread(target=self.on_wake, daemon=True).start()
                else:
                    avatar_client.send("listen", on=True, secs=6, timeout=2)
            elif self.skip_next and (line.startswith("TEXT:") or line == "NOTHING"):
                self.skip_next = False
                continue
            elif line.startswith("TEXT:"):
                kind, conf, text = (line[5:].split("|", 2) + ["", ""])[:3]
                text, conf = text.strip(), float(conf or 0)
                label = {"cmd": "command", "rt": "accurate", "dict": "basic"}.get(kind, kind)
                self._note(f"heard ({label}, {conf:.0%} sure): {text}")
                if noise_words(text):     # "Thank you.", "Hmm." from a cough: nothing to answer or ask about
                    continue
                if (kind == "dict" and conf < 0.45) or (kind == "rt" and conf < 0.2):  # not sure: ask again
                    avatar_client.send("say", text=f"Did you say “{text}”? Say “Hey Nova” and try again 🙏", secs=5, timeout=2)
                    self.mouth.say("Sorry, I didn't catch that clearly. Could you say it again?")
                    continue
                if text and not self.mouth.busy():
                    avatar_client.send("say", text=f"“{text}”", secs=4, timeout=2)
                    threading.Thread(target=self.on_text, args=(text,), daemon=True).start()
            elif line == "NOTHING":
                self._note("woke up but heard nothing after")
                avatar_client.send("say", text="Hmm, I didn't catch that.", secs=3, timeout=2)
        if self.proc is proc:
            self.status = "stopped"
