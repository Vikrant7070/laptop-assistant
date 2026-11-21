Status: active — 2026-11-18, phases 1–3 built (Phone Link notes, Gmail read-only, iPhone web app); live checks are the user's.

# Nova device network — implementation plan

Hand this whole file to the implementing model. Goal (the user's words): *"every electronic device I have comes under
one net"* — Nova on the laptop stays the **one brain**; other devices are **windows** (you talk to her from them),
**limbs** (she controls them) or **sensors** (they report to her). Never a second copy of Nova's brain or memory.

Build the phases **in order**, one at a time. Each phase is built, tested and committed on its own. Phases 5–6 are
outlines only: **do not start them** until the user answers the questions listed there.

---

## 0. Before you start

### Read first

| File | Why |
|---|---|
| `~/.claude/projects/…/memory/MEMORY.md` + linked notes | Hard-won gotchas (phone remote, look_first, speech streaming, Smart App Control, cloud brains). Especially `nova-remote-connectors-presence.md`. |
| `nova/actions/notifications.py` | `Watcher` (PowerShell WinRT listener → `on_new(list)`), `_parse()` → `{id, app, title, body, time}`, `latest()`, `same_app()`, `is_muted()`. |
| `nova/core/app.py` | Flask `app` on **127.0.0.1:5055** (≈line 1155, `app.run` ≈2983). `on_notifications()` ≈2344, `_speakable()` + `SECRETY` ≈2333 (hides codes), `announce()` ≈1420, `tell_away()` (forwards to Telegram when user away), `answer(text, quiet, remote)` ≈1535, `is_risky()` ≈323, `confirm_pending()` ≈2078, `settings()` ≈54 (defaults dict ≈line 56), `/api/setup` ≈1219, `task_log()`. |
| `nova/actions/phone.py` | Pattern to copy for anything paired: DPAPI-protected secrets via `_protect()`, 6-digit pairing code, `data/phone.json` with atomic `_save()`. |
| `nova/brain/llm.py` | `chat(model, messages, tools, …)` → `(stored, text, calls)`. |
| `nova/actions/tools.py`, `nova/brain/toolpick.py` | How tools are declared (`_tool(...)`) and picked (keyword groups). New tools need both. |
| `tests/conftest.py` | `brain` fixture = scripted `FakeBrain`; use it, never a real model, in tests. |
| `static/index.html` | The ⚙ settings UI (toggles posted to `/api/setup`). |

### Ground rules

- **Branch** `feat/device-network`, one commit per phase. Never commit to `master`.
- **The working tree has the user's uncommitted work** (`nova/actions/forms.py`, `tests/test_forms.py`, edits in
  `tools.py`, `toolpick.py`, `app.py`, `static/index.html`, plus `docs/`, `reports/`, `research_notes/`). Ask the
  user before branching whether to commit that first. Never revert or stash it on your own.
- **Tests**: `python -m pytest -q` must stay green after every phase (≈465+ tests). Add tests for every new piece.
- **No new pip packages** unless the phase says so — use `urllib`, `http.server`, Flask (already there), PowerShell.
  Pin anything added in `requirements.txt` with the tested version.
- **Never bind any server to `0.0.0.0`** or open a router/firewall port. Remote access goes through Tailscale only.
- **Secrets** (tokens, device keys, OAuth refresh tokens) are DPAPI-protected like `phone._protect()`, live in
  `data/`, and never go into logs, chat, the brain's prompt, or test fixtures.
- **Prefer the brain over hard-coded rules** for *wording and judgement* (the user asked for this). Keep code for
  *wiring, safety and speed*: e.g. the `SECRETY` OTP filter stays code, and runs on the brain's output too.
- Stop and ask the user whenever a step below says **👤 USER**.

---

## Phase 1 — Phone notifications and calls through Phone Link

**Outcome**: the iPhone's notifications reach Windows through Microsoft Phone Link; Nova's existing watcher sees them.
For a **phone call** Nova says it at once — *"Rahul is calling you on your phone"* or *"There's a call from an unknown
number on your phone"*. The **brain** decides whether a notification is a call and how to say it; no list of
phrases/names in code. Messages are read like other notifications. The user doesn't want the Phone Link call pop-up
cluttering the screen.

### 1a. Discovery (do this before writing feature code) — 👤 USER

1. Ask the user to: open **Phone Link** → pair the iPhone; on the iPhone **Settings → Bluetooth → ⓘ next to the
   laptop** → turn on **Show Notifications** and **Sync Contacts**; in Nova turn on ⚙ → Notifications.
2. Ask them to make **one test call** to the iPhone from another phone (once from a saved contact, once from an
   unsaved number if possible) and to send one test message.
3. While they do it, capture what Windows really reports: run `notifications.latest(10)` in a small script (or add a
   temporary `task_log` of the raw `app/title/body`) and record the results.
4. Write down in this file (section "1a findings"): the app name Phone Link notifications use, what a call looks
   like (title/body for contact vs. unknown), whether the incoming-call box is a toast the listener sees at all.
5. Try the "quiet screen" setting with the user: Windows **Settings → System → Notifications → Phone Link** → *Show
   notification banners* **off**, *Show notifications in notification center* **on**. Check the listener still sees
   calls. If the call box is a separate window that banners-off doesn't hide, tell the user; Phone Link → Settings →
   Features → Calls **off** would hide it but probably also stops the call notification — confirm with one more test
   and let the user choose.

**1a findings (2026-11-18, real iPhone via Phone Link):** the iPhone must have *Show Notifications* on for the
laptop in Settings → Bluetooth, otherwise Windows gets nothing. Then Windows reports the **missed** call as app
`Phone Link`, title `Phone`, body `<saved contact name> Missed Call` (e.g. "Rani 2024 Missed Call"); Windows also has
toast channels `YourPhoneCalling` and `YourPhoneNotifications_com.apple.mobilephone`. Not yet seen live: the
*ringing* toast and an unknown-number call (the user has no unknown caller - tested with dummy text and the real
local brain instead: ~1 s per notification, correct kinds). Still to confirm in 1e: the ringing toast reaches the
listener.

**If the listener never sees calls**, stop and report to the user; don't build 1b–1c on guesses.

### 1b. Brain-written announcements for phone notifications

In `app.py`, inside `on_notifications()` (after mute filtering), split items into **phone items** (app matches the
Phone Link name found in 1a, via `notifications.same_app`) and the rest (unchanged path).

For each phone item, new function `phone_note_line(n)`:
- One short `llm.chat` call on the **local** brain (same model the router uses for quick chat; no tools,
  `num_predict` ≈ 60, think off). System prompt (keep it this simple, no examples of names):
  > A notification just arrived from the user's iPhone. Reply with JSON only:
  > `{"kind": "call" | "missed_call" | "message" | "other", "say": "<one short sentence Nova says out loud>"}`.
  > For a call: say who is calling if the notification shows a saved contact's name; if it shows only a phone number
  > or nothing, say it is an unknown number — don't read the number. Never say codes, OTPs or passwords.
- Parse the JSON leniently (strip code fences; on bad JSON or timeout > 8 s fall back to
  `{"kind": "other", "say": _speakable(n)}`).
- Run `SECRETY` hiding on `say` as well (safety net — code, not brain).
- **Privacy**: always the local brain here, even if a cloud brain is selected, unless a new setting
  `phone_notes_cloud` (default **False**) is on.

### 1c. Calls jump the queue

- `kind == "call"`: don't wait the usual 30 s for `lock`/`mouth.busy()` — wait at most ~2 s, then `announce()`.
  Announce even while napping **only** if `notif_sleep` is on (same rule as now).
- Don't forward call announcements through `tell_away()`/Telegram (the phone is the thing ringing).
- `missed_call`: announce normally (queued), and do forward via `tell_away()`.
- Dedupe: Phone Link may update the same call toast; ignore a second `call` for the same `id` within 60 s.
- New setting `phone_calls` (default **True**) in the `settings()` defaults + a toggle in ⚙ next to Notifications
  ("Tell me about phone calls"). Off → calls are skipped entirely; messages still read.

### 1d. Tests (`tests/test_phone_notes.py`)

FakeBrain cases: call from contact → spoken line comes from the brain; unknown-number call → brain's "unknown" line;
brain returns garbage / raises → fallback line; brain output containing a 6-digit code → hidden; call skips Telegram
forward, missed call doesn't; `phone_calls` off → no announcement; muted Phone Link → nothing; non-Phone-Link
notification never calls the brain; duplicate call id → spoken once; cloud brain selected → local brain still used.

### 1e. Live check — 👤 USER
One more real call with Nova running. Record result in the commit message. Commit:
`Announce iPhone calls and notifications via Phone Link`.

---

## Phase 2 — Gmail, read-only

**Outcome**: "Any important emails today?", "Summarise the email from my college", "Find the OTP email". Read-only.
Sending stays as today (`draft_email` writes a draft; the user presses Send).

### 2a. Google setup — 👤 USER (write these steps for them in the README too)
1. console.cloud.google.com → new project "Nova" → enable **Gmail API**.
2. OAuth consent screen → External → add their own address as a test user.
   ⚠ Apps left in **Testing** get refresh tokens that expire after **7 days**. Tell the user to press **Publish app**
   (it stays unverified; Google shows an "unverified app" warning on sign-in, which is expected for personal use).
3. Credentials → OAuth client ID → **Desktop app** → give Nova the client ID + secret (pasted into ⚙, never in chat).

### 2b. `nova/actions/gmail.py` (stdlib only)
- OAuth installed-app flow with **PKCE** and a **loopback redirect** (`http://127.0.0.1:<random port>`, one-shot
  `http.server` that accepts exactly one `/?code=` request, checks `state`). Opens the sign-in page in the default
  browser. Scope: `https://www.googleapis.com/auth/gmail.readonly` only.
- Store refresh token DPAPI-protected in `data/gmail.json` (copy `phone.py` `_load/_save/_protect`). Access token in
  memory only; refresh on 401.
- `search(query, max=10)` → list of `{id, from, subject, date, snippet}` (uses Gmail `q=` syntax, so the brain can
  pass `newer_than:1d`, `from:…`, `is:unread`).
- `read(id, max_chars=4000)` → plain text body (prefer `text/plain` part, else strip HTML).
- `disconnect()` → revoke + delete file.

### 2c. Wiring
- Tools `check_email(query, max)` and `read_email(id)` in `tools.py` + toolpick group (words: email, mail, gmail,
  inbox, unread). Both read-only → not risky. Tool output to the brain: `SECRETY` codes hidden **unless** the user
  explicitly asked for a code (then show only in the chat bubble, never spoken — mirror the existing OTP rule).
- Setting `email_cloud` (default **False**): when a cloud brain is selected and this is off, requests that use email
  tools are routed to the local brain. Explain this in the ⚙ toggle text.
- ⚙ section: client ID/secret fields, Connect / Disconnect button, status ("Connected as …").
- `/api/gmail` GET status, POST connect/disconnect, following the `/api/phone` pattern.
- Optional, last: let `wait_for_code()` also look in Gmail (`newer_than:10m`) when connected.

### 2d. Tests (`tests/test_gmail.py`)
Mock `urllib.request.urlopen`: token exchange, refresh on 401, search/read parsing (multipart, HTML-only, base64url),
loopback rejects wrong `state`, secrets never appear in `task_log`, tools not risky, cloud routing rule.

Commit: `Read Gmail (read-only) with Google sign-in`.

---

## Phase 3 — iPhone web app over Tailscale

**Outcome**: a Nova icon on the iPhone home screen (installable web app). Chat with Nova, Yes/No approvals, her
replies; works at home and away. Phone stays a **window** — the brain stays on the laptop.

### 3a. Network — 👤 USER
Install **Tailscale** on laptop and iPhone (same account); in the Tailscale admin console enable **MagicDNS** and
**HTTPS certificates**. Then on the laptop: `tailscale serve --bg --https=443 http://127.0.0.1:5057`. This gives
`https://<laptop>.<tailnet>.ts.net` (HTTPS is required by Safari for the mic and for home-screen install) while Nova's
server stays bound to **127.0.0.1**.

### 3b. Mobile server
- New `nova/core/mobile.py`: a **second** Flask app on **127.0.0.1:5057** (don't loosen the main app on 5055 — its
  origin checks protect the desktop UI and the avatar). Started from app startup when setting `mobile` is on.
- **Pairing**: ⚙ shows a 6-digit code (like Telegram). Phone posts it to `/m/pair` → gets a random 32-byte device key
  (stored hashed in `data/devices.json`, name + last seen). Every other `/m/*` call needs `Authorization: Bearer
  <key>`; 5 wrong pairing codes → pairing locked for 10 minutes. ⚙ lists devices with a Remove button.
- Endpoints: `/m/chat` → `answer(text, quiet=True, remote=True)` (so `is_risky` asks for RISKY tools even on Full —
  same as Telegram; reuse `PHONE_TAG`-style tag but say "from the Nova app on the user's iPhone"); `/m/confirm`
  → `confirm_pending(approved, remote=True)`; `/m/events` (long-poll, last 50 events since id); `/m/status`.
- Since requests arrive from 127.0.0.1 via `tailscale serve`, **never** treat "local" as trusted — always require
  the key.

### 3c. Page: `static/mobile/`
`index.html` (phone-width chat, big Yes/No buttons for approvals, status line: busy/asleep), `manifest.webmanifest`
(name Nova, standalone, icons 180/192/512 from the existing Nova art), `sw.js` (cache the shell only — never cache
`/m/*`). Store the device key in `localStorage`. Add-to-home-screen instructions shown on first open in Safari.

### 3d. Voice (sub-step, after text works)
Hold-to-talk with `MediaRecorder` (Safari gives `audio/mp4`) → `/m/voice` → faster-whisper (PyAV decodes AAC; check)
→ same path as `/m/chat`. Reply audio: Kokoro WAV for the reply text, returned as a URL the page plays. Don't play on
the laptop speakers for mobile requests.

### 3e. Tests (`tests/test_mobile.py`)
Flask test client: no key → 401; bad pairing code lockout; pair → key works; removed device → 401; chat goes through
`answer(..., remote=True)`; risky tool asks even on Full; events long-poll; server refuses to start on a non-loopback
host.

Commit: `Nova web app for iPhone over Tailscale`.

---

## Phase 4 — iPhone reports (battery, charging, home/away) via Shortcuts

**Outcome**: "What's my phone battery?" → "62 %, charging, 10 minutes ago." "Tell me when my phone is charged."

- Endpoint `/m/report` on the mobile server (same device key). JSON body, all optional: `battery` (0–100),
  `charging` (bool), `wifi` (network name), `place` ("home"/"away"), `lat`/`lon`, `model`, `ios`, `focus`.
  Keep **only the latest** report per device in `data/devices.json` (+ timestamp). Location fields are dropped
  unless setting `phone_location` (default **False**) is on.
- Tool `phone_status()` → latest report with its age; the brain must always mention how old it is.
- Alerts: on each report, if battery crossed ≤ 20 % (not charging) or reached 100 % while charging, `announce()` it
  once. "Tell me when my phone is charged" = a one-shot watch the brain can set (reuse the reminder machinery if one
  exists; otherwise a small `phone_watch(event)` tool).
- 👤 USER: write `docs/iphone-shortcuts.md` with exact steps for a shortcut "Nova report" (Get Battery Level, Get
  Network Details, Is Charging → Get Contents of URL, POST JSON, header `Authorization: Bearer <key>`) and personal
  automations: every hour, battery level falls below 20 %, charger connected/disconnected, arrive/leave home — each
  set to **Run Immediately**. Note: reports can be late in Low Power Mode; Nova can't ask the phone on demand.

Tests: report stored/overwritten, location dropped when off, stale-age text, alert fires once per crossing.
Commit: `iPhone status reports via Shortcuts`.

---

## Phase 5 — Nova node for another computer (outline — DO NOT START)

Waiting on the user: **is the other computer Windows, Mac or Linux; laptop or desktop; always on?** If it is an
always-on desktop with a better GPU, discuss making it the hub instead.

Shape when approved: a small standalone `node/` program (no brain) on the other PC, reachable only over Tailscale,
paired with a key like Phase 3. Hub tools gain an optional `device` argument (default "this laptop") for a safe
first set: `open_app`, `screenshot`, `status` (on/CPU/battery), `copy_file`. Every action shows a small notice on the
other PC; risky actions ask on whichever device the user is using. Wake-on-LAN only for wired desktops.

## Phase 6 — Home Assistant (outline — DO NOT START)

Waiting on the user: **which smart devices they own** and whether they want to run Home Assistant. Shape: Nova talks
to Home Assistant's API (long-lived token, DPAPI) instead of each brand; locks/power/heating are risky → always ask.

---

## Done means

Each phase: tests green, the 👤 live check done with the user and noted in the commit, settings documented in
README, and anything learned the hard way added to Nova's memory notes.
