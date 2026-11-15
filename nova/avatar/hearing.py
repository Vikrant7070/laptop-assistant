"""3D Nova hears what your laptop plays - music, videos, games - and reacts with her body.

Privacy: no sound is recorded. Windows' own volume meters (the same ones the volume mixer shows) are read 25 times
a second through pycaw, one per app, so Nova's own voice and sounds are left out. From that loudness alone:
  - music: a steady beat -> its tempo and where the beats fall, so she grooves (head bob, bounce) in time and
    now and then dances at the music's tempo
  - a sudden loud sound after quiet -> she jumps (surprised)
Her reactions to laptop sound are silent: she never talks or plays effects over what you are listening to.
"""
import math
import os
import threading
import time
from collections import deque

import numpy as np

RATE = 25                  # meter readings per second
WINDOW = 8                 # seconds of loudness kept for finding the beat
MIN_BPM, MAX_BPM = 70, 180


class Beat:
    """Finds a beat in a stream of loudness readings (0..1). Pure maths: fed by LaptopEars, tested on its own."""

    def __init__(self):
        self.levels = deque(maxlen=RATE * WINDOW)
        self.times = deque(maxlen=RATE * WINDOW)
        self.music = None              # {"bpm", "period", "last_beat", "amp"} while a steady beat is playing
        self.quiet_since = time.time()
        self.next_check = 0.0
        self.loud_since = None         # when the sound started (gaps under a second don't end it)
        self.last_loud = 0.0

    def add(self, level, now):
        """One meter reading. Returns "surprised" when a sudden loud sound breaks a quiet moment, else None."""
        prev = list(self.levels)[-RATE * 2:]
        self.levels.append(level)
        self.times.append(now)
        event = None
        if prev and len(prev) >= RATE * 2 and max(prev) < 0.03 and level > 0.35:
            event = "surprised"
        if level > 0.03:
            self.loud_since = self.loud_since or now
            self.last_loud = now
        elif now - self.last_loud > 1.0:     # a meter reading of 0 between two drum hits is not silence
            self.loud_since = None
        if now >= self.next_check:
            self.next_check = now + 1.0
            self.music = self._find_beat(now)
        return event

    def _find_beat(self, now):
        if len(self.levels) < RATE * 5 or not self.loud_since or now - self.loud_since < 3:
            return None
        x = np.asarray(self.levels, dtype=float)
        if x.mean() < 0.04:
            return None
        # Onsets: how much louder each reading is than the moment just before it (rises only).
        smooth = np.convolve(x, np.ones(3) / 3, mode="same")
        onset = np.maximum(0, x - np.concatenate([[smooth[0]] * 2, smooth[:-2]]))
        onset -= onset.mean()
        if not onset.any():
            return None
        ac = np.correlate(onset, onset, mode="full")[len(onset) - 1:]
        if ac[0] <= 0:
            return None
        lo, hi = int(RATE * 60 / MAX_BPM), int(math.ceil(RATE * 60 / MIN_BPM))
        lags = np.arange(lo, min(hi, len(ac) - 1) + 1)
        score = ac[lags] / ac[0]
        best = int(lags[np.argmax(score)])
        conf = float(score.max())
        if conf < 0.25:
            return None
        # The beat rarely lasts a whole number of readings (120 BPM = 12.5): fit a parabola through the peak.
        y0, y1, y2 = ac[best - 1], ac[best], ac[best + 1]
        bend = y0 - 2 * y1 + y2
        lag = best + (0.5 * (y0 - y2) / bend if bend < 0 else 0.0)
        # Where the beats fall: the offset whose comb of beats collects the most onset energy.
        n = len(onset)
        combs = []
        for off in range(int(math.ceil(lag))):
            idx = [n - 1 - off - round(k * lag) for k in range(int(n / lag))]
            combs.append(onset[[i for i in idx if i >= 0]].sum())
        off = int(np.argmax(combs))
        # Readings don't come exactly 25 times a second (sleep timing): measure how often they really came.
        rate = (len(self.times) - 1) / max(1e-6, self.times[-1] - self.times[0])
        period = lag / rate
        return {"bpm": round(60 / period), "period": period, "last_beat": self.times[-1] - off / rate,
                "amp": float(min(1.0, x[-RATE * 2:].mean() * 3)), "confidence": round(conf, 2)}

    def phase(self, now):
        """Where in the beat we are (0 and 1 = on a beat; counts two beats, 0..2), or None when there is no music."""
        m = self.music
        if not m or now - self.times[-1] > 1.0:
            return None
        return float(((now - m["last_beat"]) / m["period"]) % 2.0)    # 0..2: two beats, so she can sway L-R


class LaptopEars(threading.Thread):
    """Reads every app's volume meter except Nova's own (this process and the app that started it)."""

    def __init__(self, on_event=None, enabled=lambda: True):
        super().__init__(daemon=True)
        self.beat = Beat()
        self.on_event = on_event
        self.enabled = enabled
        self.skip = {os.getpid(), os.getppid(), 0}
        self.error = None

    def run(self):
        try:
            import comtypes
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
            comtypes.CoInitialize()
        except Exception as e:          # no pycaw / COM: she just doesn't hear the laptop
            self.error = str(e)
            return
        meters, refresh, on, next_on_check = [], 0.0, True, 0.0
        while True:
            now = time.time()
            if now >= next_on_check:
                on, next_on_check = self.enabled(), now + 5
            if not on:
                self.beat.music = None
                time.sleep(1)
                continue
            if now >= refresh:           # apps start and stop playing: look at the list again every few seconds
                refresh = now + 3
                try:
                    meters = [s._ctl.QueryInterface(IAudioMeterInformation) for s in AudioUtilities.GetAllSessions()
                              if s.ProcessId not in self.skip]
                except Exception as e:
                    self.error, meters = str(e), []
            level = 0.0
            for m in meters:
                try:
                    level = max(level, m.GetPeakValue())
                except Exception:
                    refresh = 0
            event = self.beat.add(level, now)
            if event and self.on_event:
                self.on_event(event)
            time.sleep(max(0.0, 1 / RATE - (time.time() - now)))
