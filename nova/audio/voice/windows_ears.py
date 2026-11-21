"""Listening with Windows' own offline recognizer (a PowerShell helper): Transcriber and Ears."""
import queue
import subprocess
import threading
import time
from pathlib import Path
from nova import store
from nova.avatar import avatar_client
from nova.audio.voice import wake


LISTEN_PS1 = (Path(__file__).with_name("listen.ps1")).read_text(encoding="utf-8")   # PowerShell, kept as a .ps1 file


RT_PS1 = (Path(__file__).with_name("rt_listen.ps1")).read_text(encoding="utf-8")   # PowerShell, kept as a .ps1 file


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
        script = store.current().file("rt_listen.ps1")
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
        why = whisper.noise_words(text)
        if why:
            return why
        if conf == "Low" and len(whisper._words(text)) <= 2 and not whisper.SHORT_REPLY.match(text):
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
            pass  # intentional: an audio/OS call that has a fallback elsewhere


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


class Ears(wake._EarsBase):
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
        script = store.current().file("listen.ps1")
        script.write_text(LISTEN_PS1, encoding="utf-8")
        (store.current().file("phrases.txt")).write_text("\n".join(command_phrases()), encoding="utf-8")
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
                self._note(f"heard 'Hey {whisper.WAKE_NAME}'")
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
                if whisper.noise_words(text):     # "Thank you.", "Hmm." from a cough: nothing to answer or ask about
                    continue
                if (kind == "dict" and conf < 0.45) or (kind == "rt" and conf < 0.2):  # not sure: ask again
                    avatar_client.send("say", text=f"Did you say “{text}”? Say “Hey {whisper.WAKE_NAME}” and try again 🙏", secs=5, timeout=2)
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


from nova.audio.voice import whisper  # noqa: E402  (used when the code runs; no cycles)
