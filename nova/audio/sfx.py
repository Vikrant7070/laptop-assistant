"""Sound effects for 3D Nova's reactions: a real giggle when she laughs, "Hey!" when she waves, a sigh when she's sad...

Voice reactions use short CC0 voice-actress clips in sounds/<reaction>.wav (see sounds/CREDITS.txt). Reactions
with no clip there (dance tune, claps, spin whoosh...) are made here with numpy. Either way the result is written
once to data/sfx/*.wav and played with winsound (asynchronously, so her animation never waits for it). avatar.py calls play(name) when a
reaction starts. The quiet idle fidgets (look around, yawn...) stay silent so she doesn't beep every 30 seconds.
"""
import json
import math
import wave

import numpy as np


from nova import store
from nova.shared import ROOT

RATE = 22050
VERSION = 3                       # bump to rebuild the .wav files after changing a sound
CLIPS = ROOT / "sounds"     # real voice clips, one per reaction (optional)
VOLUME = 0.45                     # peak level of every effect (0-1): clearly there, never louder than her voice


# ------------------------------------------------------------------ building blocks
def _t(secs):
    return np.arange(int(RATE * secs)) / RATE


def _env(n, attack=0.01, release=0.08):
    """Soft attack/release so nothing clicks."""
    e = np.ones(n)
    a = min(n // 3, max(1, int(RATE * attack)))
    r = min(n // 2, max(1, int(RATE * release)))   # short sounds: shorter fades
    if not n or not a or not r:
        return e
    e[:a] = np.linspace(0, 1, a)
    e[-r:] *= np.linspace(1, 0, r)
    return e


def _glide(f0, f1, secs, harmonics=(1, .35, .15), curve=1.0):
    """A tone whose pitch slides from f0 to f1 (curve > 1 = slow start, < 1 = fast start)."""
    t = _t(secs)
    f = f0 + (f1 - f0) * (t / secs) ** curve
    phase = 2 * np.pi * np.cumsum(f) / RATE
    return sum(a * np.sin(k * phase) for k, a in enumerate(harmonics, 1))


def _bell(freq, secs=.6, decay=6.0):
    """A soft chime: sine + a slightly inharmonic overtone, fading out."""
    t = _t(secs)
    return (np.sin(2 * np.pi * freq * t) + .3 * np.sin(2 * np.pi * freq * 2.76 * t) * np.exp(-t * 8)) * np.exp(-t * decay)


def _pluck(freq, secs=.22):
    t = _t(secs)
    return (np.sin(2 * np.pi * freq * t) + .4 * np.sin(4 * np.pi * freq * t)) * np.exp(-t * 14)


def _voice(f0, f1, secs, vowel="a", breath=0.0):
    """A cartoon vowel ('ha', 'mm', 'ah') - harmonics shaped by two formants, pitch gliding f0 -> f1."""
    formants = {"a": (800, 1200), "e": (500, 1900), "m": (300, 1000), "u": (350, 800), "o": (500, 900)}[vowel]
    t = _t(secs)
    f = f0 + (f1 - f0) * t / secs
    f = f * (1 + .012 * np.sin(2 * np.pi * 5.5 * t))          # a little vibrato: sounds alive, not like a beep
    phase = 2 * np.pi * np.cumsum(f) / RATE
    out = np.zeros_like(t)
    for k in range(1, 18):
        hz = k * float(np.mean(f))
        if hz > RATE / 2.2:
            break
        gain = sum(math.exp(-((hz - fm) / 220) ** 2) for fm in formants) + .05
        out += gain / k ** .4 * np.sin(k * phase)
    if breath:
        out += breath * np.random.default_rng(len(t)).standard_normal(len(t)) * out.std()
    return out


def _noise(secs, color=0.0, seed=1):
    """Noise burst; color 0..1 smooths it (darker)."""
    n = np.random.default_rng(seed).standard_normal(int(RATE * secs))
    if color:
        k = max(1, int(1 + color * 30))
        n = np.convolve(n, np.ones(k) / k, mode="same")
    return n


def _norm(x):
    x = np.asarray(x, dtype=float)
    peak = float(np.max(np.abs(x))) or 1.0
    return x / peak


def _cat(*parts, gap=0.0):
    out = []
    for p in parts:
        out += [_norm(p) * _env(len(p)), np.zeros(int(RATE * gap))]
    return np.concatenate(out)


def _mix(length_secs, *placed):
    """placed = (start_secs, sound, gain)..."""
    out = np.zeros(int(RATE * length_secs))
    for start, snd, gain in placed:
        s = int(RATE * start)
        snd = _norm(snd) * _env(len(snd)) * gain
        end = min(len(out), s + len(snd))
        out[s:end] += snd[:end - s]
    return out


# ------------------------------------------------------------------ one sound per reaction
def _laugh():          # "ha-ha-ha-ha" twice (she laughs twice over), each "ha" a little lower
    burst = [_voice(430 - i * 25, 380 - i * 25, .11, "a", breath=.25) for i in range(5)]
    return _cat(*burst, np.zeros(int(RATE * .12)), *burst[1:], gap=.06)


def _dance():          # a bouncy little tune (she dances three times over)
    notes = [523, 659, 784, 659, 587, 698, 880, 698, 523, 659, 784, 659, 587, 698, 880, 1047] * 2
    beat = .17
    bass = [(i * beat * 4, _pluck(f / 2, .3), .8) for i, f in enumerate(notes[::4])]
    return _mix(len(notes) * beat + .4, *[(i * beat, _pluck(f), 1.0 if i % 4 == 0 else .65) for i, f in enumerate(notes)], *bass)


def _spin():           # whoosh + rising slide
    whoosh = _noise(1.1, .6) * np.sin(np.linspace(0, np.pi, int(RATE * 1.1)))
    return _mix(1.2, (0, whoosh, .6), (0, _glide(300, 1100, 1.0, curve=1.6), .5), (.95, _bell(1319, .25, 12), .4))


def _clap():           # two pairs of claps
    clap = lambda seed: _noise(.07, .1, seed) * np.exp(-_t(.07) * 45)   # noqa: E731
    return _mix(2.1, *[(s, clap(i), 1.0) for i, s in enumerate((0, .33, .66, 1.0, 1.33, 1.66))])


def _surprised():      # "whoa!" - fast rise
    return _cat(_voice(260, 620, .32, "o"), _glide(900, 1400, .12, curve=.5))


def _sad():            # "wah wah wahhh"
    return _cat(_voice(330, 315, .3, "u"), _voice(294, 280, .3, "u"), _voice(262, 200, .7, "u"), gap=.05)


def _confused():       # "hmm?" - low hum, then a question lift
    return _cat(_voice(220, 210, .35, "m"), _voice(230, 420, .3, "m"), gap=.02)


def _nod():            # "mm-hm!"
    return _cat(_voice(240, 230, .14, "m"), _voice(330, 350, .18, "m"), gap=.05)


def _shake():          # "uh-uh"
    return _cat(_voice(330, 320, .14, "u"), _voice(250, 230, .2, "u"), gap=.07)


def _facepalm():       # soft slap + a sigh
    slap = _noise(.08, .2, 7) * np.exp(-_t(.08) * 40) + _glide(140, 90, .08, (1,))
    return _mix(1.0, (0, slap, 1.0), (.15, _voice(300, 180, .7, "a", breath=.8), .6))


def _bow():            # two gentle chimes, falling
    return _mix(1.2, (0, _bell(1047), .9), (.22, _bell(784, .9), .9))


def _love():           # sparkly rising arpeggio
    notes = [784, 988, 1175, 1568]
    return _mix(1.2, *[(i * .09, _bell(f, .7, 7), .8) for i, f in enumerate(notes)], (.42, _bell(2093, .7, 9), .5))


def _yawn():           # long "aaahh" up then down, breathy
    up = _voice(230, 330, .5, "a", breath=.6)
    down = _voice(330, 190, .8, "o", breath=.7)
    return _cat(up, down)


def _look():           # tiny curious "hm?"
    return _cat(_voice(260, 380, .25, "m"))


def _happy():          # bright "ding-ding!"
    return _mix(.8, (0, _bell(880, .5, 8), .9), (.12, _bell(1109, .7, 7), 1.0))


def _wave():           # "hi-hi!" bloops
    return _cat(_glide(500, 800, .1), _glide(600, 1000, .12), gap=.07)


SOUNDS = {
    "laugh": _laugh, "dance": _dance, "spin": _spin, "clap": _clap, "surprised": _surprised, "sad": _sad,
    "confused": _confused, "nod": _nod, "shake": _shake, "facepalm": _facepalm, "bow": _bow, "love": _love,
    "yawn": _yawn, "look": _look, "happy": _happy, "wave": _wave,
}


def _clip(name):
    """sounds/<name>.wav as floats at RATE, or None when there is no clip for that reaction."""
    try:
        with wave.open(str(CLIPS / f"{name}.wav"), "rb") as w:
            a = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float) / 32767
            a = a.reshape(-1, w.getnchannels()).mean(1)
            sr = w.getframerate()
    except (OSError, EOFError, wave.Error):
        return None
    if sr != RATE:
        a = np.interp(np.linspace(0, len(a) - 1, int(len(a) * RATE / sr)), np.arange(len(a)), a)
    return a


def _with_clip(name, clip):
    """How a voice clip is used: mostly as it is, a few get a little extra."""
    secs = len(clip) / RATE
    if name == "love":       # giggle over rising chimes
        return _mix(1.2, *[(i * .09, _bell(f, .7, 7), .45) for i, f in enumerate((784, 988, 1175, 1568))], (.05, clip, 1.0))
    if name == "facepalm":   # a soft slap on the forehead, then the sigh
        slap = _noise(.08, .2, 7) * np.exp(-_t(.08) * 40) + _glide(140, 90, .08, (1,))
        return _mix(.25 + secs, (0, slap, .7), (.25, clip, 1.0))
    return clip


def make(name):
    clip = _clip(name)
    return SOUNDS[name]() if clip is None else _with_clip(name, clip)


# ------------------------------------------------------------------ sounds she invents (body.clean_sound)
def render_notes(notes):
    """A list of cleaned notes (body.clean_sound) -> samples at RATE."""
    placed = []
    for i, n in enumerate(notes):
        L, hz, to = n["length"], n.get("pitch", 440.0), n.get("to")
        kind = n["type"]
        if kind == "chime":
            snd = _bell(hz, L + .3, 6 / max(.2, L))
        elif kind == "pluck":
            snd = _pluck(hz, max(L, .15))
        elif kind == "tone":
            snd = _glide(hz, to or hz, L)
        elif kind == "voice":
            snd = _voice(hz, to or hz, L, n.get("vowel", "a"), breath=.15)
        elif kind == "boing":     # springy slide with a wobble
            snd = _glide(hz, to or hz * 2, L, (1, .3), curve=.5) * (1 + .5 * np.sin(2 * np.pi * 18 * _t(L)))
        elif kind == "whoosh":
            snd = _noise(L, .6, i + 3) * np.sin(np.linspace(0, np.pi, int(RATE * L)))
        elif kind == "pop":
            snd = _glide(700, 180, .07, (1,)) * np.exp(-_t(.07) * 30)
        elif kind == "clap":
            snd = _noise(.07, .1, i + 11) * np.exp(-_t(.07) * 45)
        else:                     # drum: a soft kick
            snd = _glide(160, 50, .25, (1,)) * np.exp(-_t(.25) * 12) + .2 * _noise(.25, .3, i) * np.exp(-_t(.25) * 30)
        placed.append((n["at"], snd, n["volume"]))
    end = max(at + len(snd) / RATE for at, snd, _ in placed) + .05
    return _mix(min(end, 6.0), *placed)


def move_sound_path(name):
    return store.current().sfx / f"move_{name}.wav"


def save_move_sound(name, sound, say=None):
    """Make and save the sound of an invented move. sound = body.clean_sound output; say(text) -> (samples, rate)
    speaks in her own voice (voice.NeuralVoice.synth). Returns the file, or None if it couldn't be made."""
    store.current().sfx.mkdir(parents=True, exist_ok=True)
    if sound.get("say"):
        if not say:
            return None
        samples, rate = say(sound["say"])
        a = np.asarray(samples, dtype=float).reshape(-1)
        if rate != RATE:
            a = np.interp(np.linspace(0, len(a) - 1, int(len(a) * RATE / rate)), np.arange(len(a)), a)
    else:
        a = render_notes(sound["notes"])
    path = move_sound_path(name)
    _write(path, a)
    return path


def _file(name):
    """The .wav for a reaction, or for one of her invented moves (move_<name>.wav)."""
    return store.current().sfx / f"{name}.wav" if name in SOUNDS else move_sound_path(name)


# ------------------------------------------------------------------ files + playback
def _write(path, samples):
    pcm = (np.clip(_norm(samples) * VOLUME, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def build(force=False):
    """Write data/sfx/<reaction>.wav for every reaction (only when missing or VERSION changed)."""
    store.current().sfx.mkdir(parents=True, exist_ok=True)
    stamp = store.current().sfx / "version.json"
    try:
        current = json.loads(stamp.read_text())["version"] == VERSION
    except Exception:
        current = False
    for name in SOUNDS:
        path, src = store.current().sfx / f"{name}.wav", CLIPS / f"{name}.wav"
        stale = src.exists() and path.exists() and src.stat().st_mtime > path.stat().st_mtime
        if force or not current or stale or not path.exists():
            _write(path, make(name))
    stamp.write_text(json.dumps({"version": VERSION}))
    return store.current().sfx


def enabled():
    """The '🔊 Reaction sounds' switch in Nova's settings (on unless turned off)."""
    try:
        return bool(json.loads(store.current().settings.read_text(encoding="utf-8")).get("reaction_sounds", True))
    except Exception:
        return True


def length(name):
    """How long a reaction's (or invented move's) sound lasts in seconds (0 when it has none or sounds are off)."""
    if not enabled() or (name not in SOUNDS and not move_sound_path(name).exists()):
        return 0.0
    try:
        with wave.open(str(_file(name)), "rb") as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return 2.0          # not built yet: assume the longest

def play(name):
    """Play a reaction's (or invented move's) sound without waiting. Returns True if one started."""
    if not enabled() or (name not in SOUNDS and not move_sound_path(name).exists()):
        return False
    path = _file(name)
    if not path.exists():
        try:
            build()
        except Exception:
            return False
    try:
        import winsound
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return True
    except Exception:
        return False


if __name__ == "__main__":      # python sfx.py  -> rebuild and play them all
    import time
    build(force=True)
    for n in SOUNDS:
        print(n)
        play(n)
        time.sleep(2.2)
