# Nova: your laptop assistant

Nova is a friendly assistant who lives in your laptop. She has two parts:

- **3D Nova on your desktop.** An anime character floats over all your windows and wanders around the screen. She talks in speech bubbles, and when she needs to press a button in an app, she walks over to it and clicks it.
- **The chat window.** You type or talk to her here. She answers out loud and shows live laptop stats.

Her brain is a **local AI model running in Ollama**, so it's free and nothing leaves your laptop.

## Set up the model (one time)
1. Install Ollama from https://ollama.com/download and open it.
2. Download the recommended model in a terminal:
   ```
   ollama pull qwen3-vl:4b-instruct
   ```
   This one model handles chat, tools and vision. On an RTX 4050 (6 GB), Nova replies in about 3–5 seconds.
   Avoid "thinking" models and models bigger than about 4.5 GB on a 6 GB GPU. They don't fit fully on the GPU, so replies take minutes.

## Optional: a cloud brain (Gemini, ChatGPT or Claude)
A big cloud model understands multi-step tasks much better than the small local one. Open **⚙ Model**, pick
**Google Gemini**, **OpenAI ChatGPT** or **Anthropic Claude** under "Who thinks for me?", paste your API key, click
**Save key & load models** and pick a model.
- Keys come from aistudio.google.com/app/apikey, platform.openai.com/api-keys or platform.claude.com/settings/keys.
  A key is checked before it is saved, and saved encrypted (Windows DPAPI) in `data/keys.json`. Only your Windows
  account on this laptop can read it. `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` env vars work too.
- **Backup brain:** if the cloud fails (no internet, wrong key, credit or rate limit ran out), the local Ollama model
  answers instead, and the chat shows "Used the local brain". After a failure Nova uses the local model for
  60 seconds before trying the cloud again. Details are in `data/task_log.txt`.
- **Privacy:** the chat (including open window titles) goes to the company you picked. Screenshots and camera
  photos never do. The local vision model always does the looking, so keep `qwen3-vl:4b-instruct` installed.
- Cloud use is billed by the provider. Gemini's free tier is enough for light use.

## Start Nova
Double-click **`Start Nova.bat`**. 3D Nova appears on your desktop and a tray icon shows up; the chat window starts hidden, so press **Ctrl+Alt+N** (or click her or the tray icon) to open it. `pythonw desktop.py --show` opens the chat window straight away. The first time, pick your model in **⚙ Model**.

| Action | How |
|---|---|
| Open the chat | **Ctrl+Alt+N** from anywhere, click 3D Nova, or click the tray icon |
| Move 3D Nova | Drag her |
| Make her wave | Right-click her |
| Hide 3D Nova | Tray icon → *Show 3D Nova on desktop* |
| Close the chat | The window hides to the tray. Use tray → *Quit Nova* to exit completely |
| Talk to her | Say **"Hey Nova"** (turn on 👂 Hey Nova), or use 🎤 voice typing in the chat |

## What she can do (50+ tools)
| Area | Examples |
|---|---|
| Click buttons like a person | "Click Save in Notepad", "Press the Play button in Spotify" |
| Windows & keyboard | "What's open?", "Switch to Chrome", "Type a poem in Notepad", "Press Ctrl+S" |
| Health & speed | "How is my laptop doing?", "What's eating my memory?", "Close Chrome" |
| Launch | "Open VS Code", "Open YouTube", "Search for…" |
| Files | find, read, write, move, organize Downloads, find big files |
| Apps | "Install VLC", "Uninstall …", "What starts with Windows?" (uses winget) |
| Settings | dark mode, wallpaper, Wi-Fi, volume, brightness, any Settings page |
| Power | lock, sleep, restart, shutdown |
| Personal | notes, reminders, remembers facts about you |
| Anything else | PowerShell, even as administrator (Windows asks you first) |

## Voice: talking with Nova
- **She speaks out loud** in a natural offline voice (Kokoro, see *Personality and voice* below), or in Windows' **Heera (English, India)** voice if the Kokoro files aren't installed. Her 3D mouth moves while she talks, and she speaks even when the chat window is hidden. Switch this with **🔊 Voice**. It works offline.
- **Say "Hey Nova"** after turning on **👂 Hey Nova**. You can say the whole thing in one breath, like "Hey Nova, what's my battery?", or say "Hey Nova", wait for her "I'm listening… 👂" bubble, and then speak. She answers out loud, and the conversation also shows in the chat.
- **🎤 in the chat** starts Windows voice typing (Win+H) for longer messages. It's more accurate.

Notes:
- **Tasks with several steps:** ask for the whole thing at once, like "open Notepad and type a shopping list" or "open YouTube, search for lofi music and play the first video". Nova does one step at a time, and after each step she looks at the screen to check it worked (you'll see "👀 Checking that worked…"). She keeps going until the task is finished, and tells you if she gets stuck. Checking needs **Screen** switched on under ⚙ Model; without it she only checks which windows are open.
- **Hearing you clearly:** all listening is done by Whisper (`large-v3-turbo`), running offline on your laptop's graphics card in about half a second per sentence, so no audio leaves the laptop. If the graphics card is full, Nova switches by herself to the smaller `small.en` model on the processor.
- **Reactions:** 3D Nova acts out how she feels. She laughs after a joke, claps when a multi-step task works, looks sad or facepalms when she gets something wrong, bows when you thank her, and shows a "?" when she didn't understand. Ask her to "dance", "laugh", "spin", "clap" or "bow" and she will. Left alone she glances around, stretches and yawns late at night. Right-click her for a random reaction. All 16: laugh, dance, spin, clap, surprised, sad, confused, nod, shake, facepalm, bow, love, yawn, look, happy, wave.
- **Dragging windows:** say "move Notepad to the right" or "put Chrome in the top-left corner". Nova walks to the window's title bar, takes hold of it and carries it there (left, right, center, top, bottom, or any corner).
- **Knowledge bank:** Nova keeps a profile of you and a list of lessons from her own mistakes in `data/knowledge.json`, and before every answer reads the parts that matter for it: your name and how you like things done, plus the facts and lessons closest in meaning to the request (a small embedding model on the CPU, `retrieval.py`; downloaded once, ~34 MB, into `~/.cache/nova-embed`). So the bank can grow without slowing her down. She adds to it when you tell her about yourself, when you correct her, and when a task fails. Open **🧠 Knowledge** in the chat window to read, add or delete anything. She never stores passwords, PINs, ID numbers, email addresses or phone numbers. With a cloud brain, that summary is sent with each message; with the local brain it stays on the laptop.
- **Personality and voice:** Nova is a "desi dost": a warm, slightly filmy Indian friend who answers Hinglish in Hinglish and English in Indian English. She speaks with a natural offline voice (Kokoro, voice `hf_alpha`) that runs on the processor. Its two model files live in `~/.cache/nova-voice/` (`kokoro-v1.0.int8.onnx` and `voices-v1.0.bin`, from the kokoro-onnx project's model-files-v1.0 release). If they are missing, she uses the Windows voice instead. To change the voice or speed, edit `NEURAL_VOICE` / `NEURAL_SPEED` in `voice.py`; her character is the `PERSONA` text in `app.py`.
- **English-only mode (on):** Nova listens for English only and answers in English with a little desi flavour. This skips the per-sentence language check, so listening is a bit quicker and a short "Hi Nova" is never mistaken for Hindi. To bring back Hindi and Hinglish, set `ENGLISH_ONLY = False` in `voice.py` and restart Nova; the next point then applies.
- **Hindi and Hinglish:** you can speak to Nova in English, Hindi or a mix, for example "Hey Nova, Chrome kholo" or "Nova, abhi kitne baje hain?". Hindi is written down in English letters, and she answers Hindi in Hinglish written the same way; her voice turns the common Hindi words back into proper Hindi pronunciation. "Nova, so jao" puts her to sleep, and "haan" / "nahi" work for yes / no. It writes down each thing said near the laptop and throws it away unless it starts with "Hey Nova". Windows' own recognizer is used only if Whisper isn't installed.
- **Mic indicator:** Windows shows a microphone icon in the taskbar while "Hey Nova" is switched on. It's off by default.
- **Accuracy:** the built-in recognizer is decent but not perfect, especially with accents. Speak clearly, or use 🎤 for important requests.
- **Interrupting:** typing or pressing 🎤 stops her mid-sentence.

## Camera: letting Nova see you
1. Install a model that can see:
   ```
   ollama pull qwen3-vl:8b
   ```
   You can also use it as Nova's main model, so one model does everything.
2. In **⚙ Model**, tick **📷 Let Nova see me through the camera**. It's off by default.
3. Ask "How do I look?" or "What am I holding?"

What happens with the camera:
- **When she looks:** only when you ask, or when a request needs it. She never watches all the time.
- **What you see:** every photo she takes appears in the chat, so you always know what she saw. Your camera light turns on while she looks.
- **Where photos go:** they stay in memory and go to the local vision model only. They're never saved to disk or sent online.
- **If the main model can't see:** if your main model is text-only (like DeepSeek), Nova passes the photo to any installed vision model and uses its description.

## Screen: letting Nova see your screen
Tick **🖥 Let Nova see my screen** in **⚙ Model**, then ask things like "What's on my screen?", "Read this error for me" or "What am I looking at?". You can also say them after "Hey Nova".

- **Out of the way:** she hides her chat window and 3D body for half a second, so the picture shows your apps and not her.
- **What you see:** each screenshot she takes appears in the chat. Click it to enlarge.
- **Where screenshots go:** they stay in memory and go to the local vision model only. They're never saved or sent online, even if you use a cloud chat model.
- **Secrets:** she's told never to read out passwords, API keys or tokens she sees. Still, close sensitive files before asking.

## How much she asks first (⚙ Model)
- **Careful** (default): she asks before anything that changes things, including every click.
- **Trusted:** she just does it, and asks only before shutdown, uninstalling, emptying the Recycle Bin or admin commands.
- **Full access:** she never asks. A small local model can misunderstand you, so use this one carefully.

## How it's built
- `desktop.py` runs the app: the chat window, tray icon, hotkey, and it starts 3D Nova.
- `avatar.py` is 3D Nova, a see-through always-on-top window that walks, talks and points. She listens for commands on port 5056.
- `static/nova3d.html` is her 3D model (Three.js). Open it at http://127.0.0.1:5055/static/nova3d.html to spin her around. If you change her look, click **Export frames** to update the desktop version.
- `app.py` is the server and conversation loop. `llm.py` talks to Ollama. `cloud.py` talks to Gemini, OpenAI and Claude. `tools.py` holds all the tools.
- `static/index.html` is the chat window. `data/` stores notes, memories and settings.

Clicking uses Windows UI Automation to find buttons by name. It works with most apps, including Settings, Notepad, Office, Explorer and browsers. Games and some custom apps don't expose their buttons this way.

## Safety notes
- Nova's local servers (ports 5055 and 5056) answer only to her own pages on this laptop: requests from other
  websites, or with a foreign host name (DNS rebinding), get "403 Forbidden".
- Text the model passes to tools (app names, search words, file paths) is quoted before it reaches PowerShell, so a
  web page or window title can't smuggle in commands. **Trusted** and **Full access** still let her run PowerShell
  without asking; use **Careful** if you're unsure.
- Everything in `data/` (your profile, the task log, settings, encrypted keys) is personal and is git-ignored.

## Tests
```
pip install -r requirements-dev.txt
python -m pytest -q
```
The tests never touch your real `data/` folder, keyboard, browser, voice or 3D Nova. `docs/REVIEW.md` has the
review, the full test plan (including the manual checks) and what was fixed.

