# Splitting the last four big files

Part B split `tools.py` (3.3k lines) and `app.py` (4.6k). Four files are still over the ~800-line limit in the
plan's definition of done:

| File | Lines | Shape | How much reaches into it | Who else is editing it |
|---|---|---|---|---|
| `nova/audio/voice.py` | 1,824 | 9 classes, each a separate job | 3 assignments (all in `conftest`), 16 names used by Nova | nobody |
| `nova/actions/skills.py` | 1,210 | ~70 functions in clear groups | 41 names used by Nova, 1 patch, 2 assignments | **manager Phase 4** (uncommitted) |
| `nova/avatar/avatar.py` | 944 | one 774-line class (`Nova`) | runs in its own process; 2 patches | nobody |
| `nova/actions/coworker.py` | 815 | background jobs + hidden-browser research | 39 test patches | **manager Phase 4** (rewriting research) |

## The approach

**Reuse what worked twice, but match the technique to the file's shape.**

1. **A module of functions or of separate classes → a package** (`voice`, `skills`). Same method as `tools/` and `app/`:
   - A script moves whole statements; every line keeps its text. Comments that sit above a statement move with it.
   - A name used across modules is written `module.name` and read when the code runs, so a patch on the home module
     reaches every caller.
   - The package's `__init__` answers `voice.<name>` from the home module (`__getattr__`), so callers don't change.
   - A module imports at the top only what it needs while it loads, and the rest at the bottom (no cycles).
   - Anything that **assigns** onto the module (`voice.Mouth = FakeMouth` in `conftest`) is pointed at the home module.
   - Guard tests: no patch or assignment on the package itself, every `voice.<name>` still resolves, and every module
     stays under 800 lines.
2. **One giant class → extract, don't package** (`avatar.py`). Cutting a class into files means mixins or composition,
   which changes how the code is shaped. Instead, move out what isn't really the class: the Win32 plumbing and the
   command dispatcher.
3. **A file someone else is rewriting → let them split it** (`coworker.py`). Phase 4 is moving research into the worker
   system; that is the natural place to split research out.

**Why not the alternatives**
- `from .speak import *` re-exports look simpler, but `monkeypatch.setattr(voice, "Mouth", ...)` would then change a
  copy the real code never reads. Tests would pass while testing nothing; that's the trap the guard tests exist for.
- Leaving the files as they are is reasonable for `avatar.py` (944 is barely over). But `voice.py` mixes four separate
  subsystems (speaking, Windows listening, Whisper, barge-in), and a change to one means scrolling past the others.
- A redesign (interfaces, dependency injection) would be better long-term (see the project review), but it changes
  behaviour. A split should be a pure move, checkable line by line.

**One tool for all of it.** Turn the two split scripts into one `scripts/split_module.py`:
`python scripts/split_module.py nova/audio/voice.py --sections sections.toml`. It takes a section map (start line →
module), does the moves, rewrites the names, splits the imports and writes `__init__`. Then generalise the two guard
tests into one `tests/test_packages.py` that covers every package (`tools`, `app`, `voice`, `skills`).

## 1. `voice.py` → `nova/audio/voice/` (now; low risk)

| Module | Holds | ~Lines |
|---|---|---|
| `text.py` | `_clean_for_speech`, `HINGLISH_WORDS`, `_hindi_script`, `speech_envelope` | 90 |
| `speak.py` | `BrainBalance`, `NeuralVoice`, `Mouth` (the 350-line speaking pipeline) | 430 |
| `windows_ears.py` | `LISTEN_PS1`, `RT_PS1`, `Transcriber`, `Ears`, `command_phrases`, `kill_strays`, `online_speech_on` | 380 |
| `whisper.py` | `_add_nvidia_dlls`, `_words`, `noise_words`, `VoiceProfile`, `_wake_re`, `WhisperTranscriber` | 470 |
| `wake.py` | `_EarsBase`, `WhisperEars`, `set_wake` | 200 |
| `barge_in.py` | `_barge_re`, `other_apps_playing`, `BargeIn` | 200 |

- **Assignments to retarget:** `voice.Mouth = FakeMouth`, `voice.RealMouth = voice.Mouth`, `voice.NEURAL_VOICE = ...`
  (in `conftest` and the voice tests) → `voice.speak.*`. `app/voice_io.py` creates `voice.Mouth()` at import, so the
  `__getattr__` read picks up the fake, as it does today.
- **Optional, same PR:** move the 126 lines of PowerShell (`LISTEN_PS1`, `RT_PS1`) out of Python strings into
  `nova/audio/voice/listen.ps1` and `rt_listen.ps1`, read at runtime and still written to the data folder before they
  run. They then get syntax highlighting and can be edited without escaping.
- **Check:** every public name `voice.X` resolves to the same object type as before; the full suite; one live run with
  voice: wake word, a spoken request, barge-in, and her spoken answer.

## 2. `avatar.py` → extract two pieces (now; low risk, separate PR)

- `nova/avatar/win32.py`: the ctypes structs (`POINT`, `SIZE`, `BLENDFUNCTION`, `BITMAPINFO*`),
  `premultiplied_bgra` and the layered-window blit code from the class (about 150 lines).
- `nova/avatar/commands.py`: the 132-line `Nova.handle()` command dispatcher becomes a table of small functions
  `def cmd_<name>(nova, **args)`. `handle()` becomes a five-line lookup.
- `Nova` keeps behaviour and state, about 600 lines. No package and no `__getattr__`: nothing outside patches it.
- **Check:** the full suite plus a live run (3D body: idle, talk, react, walk, nap, look change).

## 3. `skills.py` → `nova/actions/skills/` (done 2026-11-22)

Done with `scripts/splits/skills.toml`. The match module is `matching.py`: `match` is also a function
(`skills.match`), and `split_module.py` refuses a module name that shadows a top-level name.
`FUZZY_FLOOR` is assigned on `skills.matching`, `_cache` used through `skills.library`, `MAX_ACTIVE` patched on
`skills.format`.

| Module | Holds | ~Lines |
|---|---|---|
| `format.py` | `parse*`, `render*`, `_front`, `slug`, `_value`, `_steps`, `compute_risk` | 470 |
| `match.py` | `_norm`, fillers, `pick`, `match`, `guess_input`, `prompt_block`, `similar` | 230 |
| `library.py` | `load`, `get`, `save`, `delete`, `edit`, `undo`, `history`, `set_status`, `_write` | 360 |
| `stats.py` | `stats_*`, `record_run`, `add_not_for`, `least_used` | 90 |
| `drafts.py` | `steps_from_trace`, `draft_from_steps`, `describe`, `fill` | 110 |

- **Wait for Phase 4:** its worktree has uncommitted edits to `skills.py`. Split straight after it merges, before
  anything else starts editing the file.
- **Watch:** `_cache` and `FUZZY_FLOOR` are module state that other code and `nova/skill_eval.py` set
  (`skills.FUZZY_FLOOR = ...`). These assignments must target `skills.match`.

## 4. `coworker.py` (inside Phase 4)

Phase 4 step 0 moves research into the worker system. The cleanest result is
`nova/actions/research.py` (hidden browser: `open_hidden`, `park`, `unwrap`, `result_links`, `search`, `research`,
about 350 lines), with `coworker.py` keeping jobs and drafts (about 460). The Phase 4 session should do this as part of its
change, since it's rewriting those lines anyway. Its 39 test patches then move with the code they patch.

## Order and size

| PR | What | When | Effort |
|---|---|---|---|
| 1 | `scripts/split_module.py` + `tests/test_packages.py` (generalised guards) | now | S |
| 2 | `voice.py` → package (+ PowerShell files, optional) | now | S–M |
| 3 | `avatar.py` extractions | now, any order with PR 2 | S |
| — | `coworker.py` research split | inside Phase 4 | (Phase 4) |
| 4 | `skills.py` → package | right after Phase 4 merges | S–M |

**Done when:** no file in `nova/` over ~800 lines, all package guards in one test, and each split PR showed the same
before/after result for its public names.
