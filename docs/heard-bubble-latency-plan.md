# Plan: show what Nova heard sooner

**Problem (user report):** Nova does hear speech, but the bubble showing *what she heard* shows up late, so the
user can't tell what she understood until some time after speaking.

**For:** Sonnet. Do the steps in order. Commit each step on its own. Run `pytest` after each step.

---

## What happens today (traced 2026-11-18)

Path for a request in conversation mode (Whisper installed, which is the normal setup):

1. `app.silent_loop()` calls `transcriber.listen()` (`nova/core/app.py:2764`).
2. `WhisperTranscriber.hear()` → `_record()` (`nova/audio/voice.py:954`) records until **0.9 s of quiet**
   (`END_PAUSE`). Nothing is shown while you talk. Only the ear waves move.
3. `_transcribe()` (`voice.py:1025`) runs Silero VAD (`get_speech_timestamps`), then `_decode()` →
   `model.transcribe(..., beam_size=3, vad_filter=True)` on **large-v3-turbo / CUDA**. `vad_filter=True` runs
   Silero VAD a **second** time. `beam_size=3` is slower than greedy decoding.
4. `_decode` waits on `self.gpu` and shares the **6 GB card with Ollama** (qwen3-vl:4b). When the brain is busy
   (background research, a phone notification being phrased, the previous answer still going), Whisper's decode
   slows down a lot or waits.
5. Only after all that does the 3D bubble get the text: `avatar_client.send("say", text=“…”, secs=4)`
   (`app.py:2771`).
6. **Chat window:** the `🎙 you said` message is sent only by `push_event({"me": text, **out})` at
   `app.py:2938`, which runs **after `answer()` has finished**. Brain turns in `data/task_log.txt` take 4 to 22 s,
   so the chat shows your words **seconds to tens of seconds late**. This is the biggest delay.
7. The 3D bubble can also be replaced too soon. The user's text is up for 4 s, then her own spoken sentences
   (`Mouth._bubble`, `voice.py:296`) take over the same bubble.
8. "Hey Nova, <request>" in one breath: `WhisperEars._handle` (`voice.py:1313`) shows the bubble only after the
   whole sentence has been decoded. In the chat it shows only after the answer (same `push_event` problem, via
   `on_request` → `heard`).

There is **no timing in the task log** for listening, so we can't see how much each part adds up to.

---

## Step 1: Measure first (small, no behavior change)

In `WhisperTranscriber`:
- `_record()`: store `self.speech_end = time.time()` when the loop breaks on the end pause (the time of the last
  voiced block is `last_sound`; also store `self.pause_wait = quiet`).
- `_transcribe()`: time the VAD check and `_decode()` separately. Also time how long it waited for `self.gpu`
  (take the time before `with self.gpu:` and again inside it).
- Put the numbers on the instance: `self.timing = {"pause": …, "vad": …, "gpu_wait": …, "decode": …}`.

In `app.silent_loop()`, right after `kind == "text"`, log:
`task_log(f"HEARD {text[:60]!r} (end pause {pause:.2f}s, vad {vad:.2f}s, gpu wait {gw:.2f}s, decode {dec:.2f}s, {transcriber.using})")`.
Do the same in `WhisperEars._handle` for the one-breath path. (`voice.py` can't call `task_log`, so add an
optional `self.log = None` hook that the app sets to `task_log`, the same way `llm.log = task_log` works.)

Use `getattr(..., 0.0)` everywhere so the Windows `Transcriber` still works.

## Step 2: Show the user's words in the chat right away (biggest win)

In `heard()` (`app.py:2904`):
- Right after `ears._note(...)` and **before** `wants_sleep`, `help_first`, `take_offer`, and `answer`, call
  `push_event({"me": text})`.
- Then take `"me"` out of every later `push_event` in `heard()`: lines `2917`, `2923`, `2938`. They become
  `push_event({**helped})`, `push_event({"reply": said})`, `push_event(out)`.
- Check `static/index.html:487-491`. An event with only `me` and no `reply` or `error` goes to `render(ev)`. Make
  sure `render` does nothing for an empty event (no blank Nova message, no closing the thought box). If it does
  something, change the JS to `if (ev.me && !ev.reply && !ev.error && !ev.say) continue;` after `add("me", …)`.
  Keep the line that puts "what you said" before the thought box.
- Look for tests that check `push_event` payloads with `"me"` (`grep -rn '"me"' tests/`) and update them.

## Step 3: Keep the heard text up until she starts answering

The 4 s bubble can run out during a long think, or get replaced by her first spoken sentence.
- In `silent_loop` (`app.py:2771`) and `WhisperEars._handle` (`voice.py:1313`), send the heard text with
  `secs=30` instead of 4.
- Nova's first spoken sentence already replaces the bubble (`Mouth._bubble` → avatar `say`). That is the right
  moment for it to go. If the turn ends **without** speech (error, a quiet tool result), clear it in `heard()`'s
  exit path: `avatar_client.send("say", text="", timeout=2)` only when `mouth.busy()` is false and nothing was
  spoken.
- Don't touch the thought cloud. It is a separate element (`think_aloud`).

## Step 4: Make the Whisper decode faster

Do these one at a time, using the Step 1 log numbers:
1. **Don't run VAD twice.** `_transcribe` already checks speech with `get_speech_timestamps`. Set
   `vad_filter=False` in `_decode_now` (`voice.py:949`). Or pass the timestamps already found, clip the audio to
   the first start and last end plus 0.2 s padding, and decode only that. Clipping also removes the 0.9 s of
   trailing quiet and the 0.35 s of pre-roll from what Whisper reads.
2. **Greedy decoding for requests:** `beam_size=1` (keep `beam_size=3` only for `again_in_english`, which is rare).
   The filters on `avg_logprob`, `no_speech_prob` and `compression_ratio` still protect against bad readings.
   Watch the `SURE` and `SURE_SHORT` limits: greedy decoding gives slightly lower log-probabilities. Re-check
   `tests/` that cover `doubt()`.
3. `without_timestamps=True` in `_decode_now`. We never use segment times, and this speeds up decoding.
4. Leave `END_PAUSE` at 0.9 s for now. It was set to stop requests being cut off. Only after Steps 1 to 4.3, if
   the log shows the pause is still the biggest part, try 0.7 s (the wake loop already uses 0.7).

## Step 5: Live "hearing…" text while the user talks (optional, do last)

`_record()` already supports `peek` callbacks with partial audio (used for the wake word at 0.5, 0.9 and 1.5 s).
- Add a peek to `listen()` too. Every ~1.2 s of speech, start a thread that calls `quick_english(np.concatenate(chunks))`.
  It already skips itself if the full decode holds the GPU (`self.gpu.acquire(blocking=False)`).
- Send the partial result as `avatar_client.send("say", text=f"“{partial}…”", secs=3)`. To do that, add an
  `on_partial` callback hook on `WhisperTranscriber` that the app sets. `voice.py` already imports `avatar_client`
  for the ears, so either way works, but a hook is easier to test.
- Run at most one peek at a time (a `self.peeking` flag, like `WhisperEars._peek`). **Never** while
  `self_voice()` is true.
- GPU budget: each quick look is ~0.2 s on the card, and the card is shared with Ollama. Skip the peeks when
  `convo["thinking"]` is true or the GPU lock is taken. If the Step 1 log shows the full decode getting slower
  because of the peeks, turn this off.
- The final text replaces the partial text as soon as `listen()` returns (Step 3 code).

---

## Tests to add (`tests/test_heard_latency.py`)

- `heard()` sends `{"me": text}` **before** `answer()` is called: patch `answer` to record the `push_event` calls
  seen so far, and check that the "me" event is already there.
- Later events from `heard()` have no `"me"` key (no duplicate chat bubble).
- `_decode_now` passes `vad_filter=False`, `beam_size=1` and `without_timestamps=True` to a fake model.
- `_record` sets `speech_end` and `timing` on a fake `sounddevice` stream (see how existing voice tests fake
  `sd.InputStream`, if any; otherwise patch `sounddevice.InputStream` with a class that yields loud then quiet blocks).
- The silent loop's avatar `say` uses the longer `secs`.

## Check by hand afterwards

Start Nova, say "Hey Nova", wait, then say a request while the chat window is open:
- The `🎙` line appears in the chat at the same moment as the 3D bubble, before her thought box fills.
- `data/task_log.txt` has a `HEARD … decode X s` line. With Ollama idle, decode should be well under 1 s on
  large-v3-turbo with beam 1.
- Repeat while background research is running (GPU busy) and compare the `gpu wait` and `decode` numbers.

## Don't change

- The self-voice guard in `_record` (throws away audio while Nova talks).
- `strict` doubt checks and `VoiceProfile` loudness checks (noise rejection).
- The one-GPU-job-at-a-time rule (`self.gpu`). See the 6 GB card note in `WhisperTranscriber.__init__`.
