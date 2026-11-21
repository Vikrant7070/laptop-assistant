"""Text for her voice: what gets read out (_clean_for_speech), Hindi/Hinglish detection, the loudness envelope."""
import re
from nova.shared import stage_directions


def _clean_for_speech(text):
    text = stage_directions(text)[0]          # '*laughs*', '[react: "laugh"]': her body does it, she doesn't say it
    text = re.sub(r"[*_#`>|]", "", text)
    text = re.sub(r"\[spoken[^\]]*\]", "", text)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[\U0001F300-\U0001FAFF\U0001F1E6-\U0001F1FF☀-➿]", "", text)  # emoji (and flags: 🇬🇧)
    # ...and the invisible glue of combined emoji (🤷‍♂️ = shrug + joiner + ♂ + variation selector): left alone it
    # became a "sentence" Kokoro can't say, so the slow Windows voice said it (7 s, 2026-11-18).
    text = re.sub(r"[​-‏⁠︎️⃣]", "", text)
    return " ".join(text.split())


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
