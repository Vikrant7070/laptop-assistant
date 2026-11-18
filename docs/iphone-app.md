# Nova on your iPhone

A web app you add to your home screen. Nova stays on the laptop (her brain, memory and tools); the phone is a window
to her. It works at home and away, through **Tailscale** - a private network that only your own devices can join.
Nothing is opened on your router or to the internet.

What you can do: chat with Nova, see her answers, answer her **Yes / No** questions (anything that changes the
laptop asks first, even when Nova's autonomy is "Full"), and press **Stop**.
What it can't do: listen for "Hey Nova" in the background (iOS doesn't allow it), or reach her while the laptop is
off or asleep.

## One-time setup (about 10 minutes)

### 1. Tailscale on the laptop and the iPhone
1. Install **Tailscale** on the laptop (<https://tailscale.com/download>) and sign in. Use any account (Google/Microsoft/Apple).
2. Install **Tailscale** from the App Store on the iPhone and sign in with **the same account**. Turn it on.
3. In the Tailscale admin page (<https://login.tailscale.com/admin/dns>): make sure **MagicDNS** is on, and under
   **HTTPS Certificates** click **Enable HTTPS**. (iOS only lets a home-screen app use the microphone and install
   properly over https.)

### 2. Share Nova's phone page through Tailscale (laptop, once)
Open PowerShell and run:

```
tailscale serve --bg --https=443 http://127.0.0.1:5057
```

This gives Nova's phone page a private address like `https://<laptop>.<something>.ts.net`. It is only reachable from
devices signed in to your Tailscale account. (To undo it later: `tailscale serve reset`.)

### 3. Switch it on in Nova
1. Nova's settings (⚙) → **Phone** → **📲 Nova on my iPhone** → tick **Switch on the iPhone app**.
2. The address to open on your phone appears there.

### 4. Pair the phone
1. On the iPhone, open that address in **Safari**.
2. In Nova's settings click **New pairing code** and type the 6 digits on the phone. (The code works once, for 10
   minutes. Five wrong tries lock pairing for 10 minutes.)
3. In Safari tap **Share → Add to Home Screen → Add**. Open Nova from the new icon.

To remove a phone, click **Remove** next to it in Nova's settings, or **Unpair** inside the phone app.

## How it stays safe
- The phone page is served by a separate small server that listens on `127.0.0.1` only. Tailscale is the only thing
  that connects to it from outside.
- Every request needs a per-device key you get at pairing. "Coming from localhost" (which is where Tailscale's proxy
  comes from) is never trusted by itself. Keys are stored only as hashes in `data/devices.json`.
- Other websites can't talk to it (JSON only, no cross-site access).
- Her answers come back like the Telegram remote: short text, nothing spoken on the empty laptop, risky actions asked first.

## If something's wrong
- **"Can't reach Nova"** on the phone: is the laptop on, awake, with Nova running? Is Tailscale on (on both)?
- **Page won't open**: on the laptop run `tailscale serve status` - it should list `127.0.0.1:5057`.
- **"Not paired"**: pair again with a new code (settings → New pairing code).
