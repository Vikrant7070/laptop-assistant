# 🎙 Mic loudness slider — plan (no code yet)

**Goal:** you choose how loud a sound must be before Nova starts recording, with a slider in ⚙ Settings.
Move it one way and she catches soft speech. Move it the other way and she ignores quiet sounds (TV, fan, people
farther away) and only reacts when you speak up.

## How it works today

`WhisperTranscriber._record` (`nova/audio/voice.py:1054`) checks the loudness (RMS) of every 30 ms block:

| What | Rule today | Where |
|---|---|---|
| Speech **starts** | `rms > max(START_MIN 0.0003, room noise × 3.5)` for 3 blocks in a row (~0.1 s) | voice.py:1088 |
| Speech **keeps going** | `rms > max(KEEP_MIN 0.00025, room noise × 2.5, 20% of your voice)` | voice.py:1107 |
| Talk-over check | uses `START_MIN` as its lowest level | voice.py:1560 |

These numbers are fixed in the code. You can't change them from the app.

## The setting

- New setting `mic_level`, **1 to 10, default 5**. At 5 she behaves exactly as she does now.
- Slider label in the UI: **"🎙 How loud I must be: soft voice ◀────▶ loud voice only"**, with the number next to it.
- 1 = catches soft speech. 10 = needs a clearly loud voice.
- It works by multiplying the thresholds: `scale = 2 ** ((mic_level - 5) / 2)`.
  - 1 → ×0.25 (4× more sensitive). 5 → ×1 (today). 10 → ×5.7 (needs about 15 dB more sound).

| Threshold | New value |
|---|---|
| `START_MIN` | `0.0003 × scale` |
| start factor | `3.5 × scale`, but never below 2.0 (so steady room noise alone can't start a recording) |
| `KEEP_MIN` | `0.00025 × scale` |
| keep factor | `2.5 × √scale`. This one changes more gently so a loud setting doesn't cut off the end of your sentence. |

The VoiceProfile "too quiet to be you" check and the Silero VAD / Whisper filters stay as they are. They still
throw out clicks and bumps after recording.

## Steps

1. **voice.py.** Add a `scale` attribute to `WhisperTranscriber` (default 1.0) and a small method that returns the
   four thresholds above. `_record` uses it instead of the fixed numbers. `BargeIn.unexplained` uses the scaled
   `START_MIN`.
2. **app.py: setting and saving.**
   - Add `"mic_level": 5` to the defaults (`app.py:62`).
   - `/api/setup` saves it, clamped to 1–10 (next to `listen_pause`, `app.py:1868`).
   - The state endpoint returns it (`app.py:1806`).
   - Add `MIC_LEVEL = (1, 10)`.
   - `apply_listening()` (`app.py:2910`) sets `whisper.scale` from it, so changes apply right away with no restart.
3. **index.html: the slider.**
   - Put an `<input type="range" id="micLevel" min="1" max="10" step="1">` with a live number label in the
     🎙 listening line.
   - Load it at line 823 and save it at line 940, the same way `listenPause` is handled.
4. **Live meter (optional, but it answers "this much loudness").**
   - Add a thin bar under the slider. It shows how loud the mic is right now, with a marker at the "she starts
     recording" level.
   - `_record` already measures `rms` for each block. Keep the latest value and the current start level on the
     transcriber.
   - A small `/api/mic_level` endpoint returns both. The page asks for it about every 200 ms, only while that
     settings tab is open.
   - This doesn't open a second mic stream, so it adds no CPU and doesn't fight Whisper for the mic.
5. **Tests** (`tests/test_listen_timing.py`):
   - `apply_listening({"mic_level": 99})` is clamped to 10, and 0 is clamped to 1.
   - Saving `mic_level` through `/api/setup` and reading it back works.
   - Synthetic audio: a quiet tone gets recorded at level 2, gives `None` (ignored) at level 9, and works at 5 as it
     does today.
   - At level 10, a loud sentence with a soft ending is not cut off early (checks the gentler keep factor).

## Decided

- **Tab: 👀 Senses** (chosen 2026-11-20). The slider and meter go right under the 🎙 "stops listening after…"
  line (`static/index.html:369-370`), above the ✋ interrupt checkbox.

## Risks

- A high setting with a quiet laptop mic can mean she never hears "Hey Nova". Step 4's meter makes this easy to
  see, and the default stays 5.
- The barge-in (talking over her) uses the same lowest level. A high setting makes interrupting her a little
  harder. That's expected and consistent.
