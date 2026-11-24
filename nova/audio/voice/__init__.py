"""Nova's voice and ears - fully offline, using what's built into Windows.

Speaking:  Windows speech voices (prefers "Heera - English (India)"), with 3D Nova moving her mouth.
Listening: Windows' offline speech recognizer waits for "Hey Nova", then writes down what you say next.
           Runs in a small PowerShell helper (listen.ps1); the microphone is only used while it's switched on.

This was one module; it is now a package, one module per job (scripts/split_module.py). `voice.<name>`
still works: it is read from the name's home module on every access. Patch or assign a name in its home module
(`home_of(name)`), never on the package - tests/test_packages.py checks that.
"""
# Import order: each module finds what it needs at import time already there.
from nova.audio.voice import wording, speak, wake, windows_ears, whisper, barge_in  # noqa: F401

_MODULES = {m.__name__.rsplit(".", 1)[1]: m for m in (wording, speak, wake, windows_ears, whisper, barge_in,)}
_HOME = {name: _MODULES[mod] for mod, names in {
    'wording': ['DEVANAGARI', 'HINGLISH_SURE', 'HINGLISH_WORDS', 'SPEECH_FPS', '_clean_for_speech', '_hindi_script', 'speech_envelope'],
    'speak': ['BrainBalance', 'DUCK_LEVEL', 'Mouth', 'NEURAL_DIR', 'NEURAL_MODEL', 'NEURAL_SPEED', 'NEURAL_THREADS', 'NEURAL_VOICE', 'NEURAL_VOICES', 'NeuralVoice', 'ONECORE', 'PREFERRED_VOICES', 'VOICE_CUSHION', 'brain_balance'],
    'wake': ['WhisperEars', '_EarsBase', 'set_wake'],
    'windows_ears': ['Ears', 'LISTEN_PS1', 'RT_PS1', 'Transcriber', 'command_phrases', 'kill_strays', 'online_speech_on'],
    'whisper': ['ANYTIME_SHORT', 'EARS_SLEEP_RE', 'EARS_WAKEUP_RE', 'ENGLISH_ONLY', 'ENGLISH_SURE', 'HINGLISH_HINT', 'JUNK_WORDS', 'NOT_WORDS', 'NOVA_NAMES', 'SHORT_REPLY', 'SURE', 'SURE_HINT', 'SURE_SHORT', 'SURE_WAKE', 'VoiceProfile', 'WAKE_HINT', 'WAKE_NAME', 'WAKE_RE', 'WHISPER_CHOICES', 'WHISPER_GHOSTS', 'WHISPER_HINT', 'WHISPER_JUNK', 'WhisperTranscriber', '_add_nvidia_dlls', '_wake_re', '_words', 'noise_words'],
    'barge_in': ['BARGE_RE', 'BARGE_STOP_RE', 'BargeIn', '_barge_re', 'echo_score', 'echo_words', 'other_apps_playing'],
}.items() for name in names}


def __getattr__(name):
    """voice.<name> -> the current value in its home module (so a patch there is seen by every caller)."""
    mod = _HOME.get(name)
    if mod is None:
        for m in _MODULES.values():          # a module one of them imports (avatar_client, ...)
            if name in vars(m):
                return vars(m)[name]
        raise AttributeError(f"module 'nova.audio.voice' has no attribute {name!r}")
    return getattr(mod, name)


def home_of(name):
    """The module that owns `name`, or None."""
    return _HOME.get(name)
