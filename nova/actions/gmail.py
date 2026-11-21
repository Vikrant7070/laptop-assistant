"""Nova reads your Gmail - read-only. She can search and read mail; she can never send, delete or change anything.

Sign-in is Google's own page (OAuth, installed-app flow with PKCE and a loopback redirect on 127.0.0.1): Nova never sees
your Google password. The one permission asked for is gmail.readonly. The Google "client" (id + secret) you create in
your own Google Cloud project, and the refresh token Google gives back, are stored locked with Windows (DPAPI, only
this Windows account can open them) in data/gmail.json - never in the project, the logs or the chat.

  status()                     - {"configured", "connected", "email"}
  save_client(id, secret)      - the client from your Google Cloud project
  connect(on_url=None)         - opens Google's sign-in in the browser and waits for you to approve (blocking)
  search(query, count)         - newest matching mail: [{id, from, subject, date, snippet}] (Gmail search words work:
                                 "is:unread", "from:amazon", "newer_than:1d")
  read(id, max_chars)          - the text of one mail
  disconnect()                 - revoke Google's access and forget the token
"""
import base64
import hashlib
import html
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from nova import store

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

_lock = threading.Lock()
_access = {"token": "", "until": 0.0}
connecting = {"on": False, "error": ""}      # for the settings page while Google's sign-in is open


class GmailError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- the locked file

def _file():
    return store.current().gmail


def _protect(text, on):
    from nova.brain import cloud       # its DPAPI helper: only this Windows account can decrypt
    if on:
        return base64.b64encode(cloud._dpapi(text.encode(), True)).decode()
    return cloud._dpapi(base64.b64decode(text), False).decode()


def _load():
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(f)


def _secret(key):
    """One locked value from the file ("" when missing or not readable by this Windows account)."""
    v = _load().get(key)
    if not v:
        return ""
    try:
        return _protect(v, False)
    except Exception:
        return ""


def status():
    d = _load()
    return {"configured": bool(d.get("client_id") and d.get("client_secret")),
            "connected": bool(d.get("refresh_token")), "email": d.get("email", ""), "client_id": d.get("client_id", "")}


def configured():
    return status()["configured"]


def connected():
    return status()["connected"]


def save_client(client_id, client_secret):
    client_id, client_secret = str(client_id or "").strip(), str(client_secret or "").strip()
    if not client_id or not client_secret:
        raise GmailError("Both the client ID and the client secret are needed.")
    d = _load()
    d.update(client_id=client_id, client_secret=_protect(client_secret, True))
    _save(d)


# ---------------------------------------------------------------- talking to Google

def _http(method, url, data=None, headers=None, timeout=20):
    """One request; the JSON answer. Raises GmailError (never with a token in its text)."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8", "replace"))
            why = err.get("error_description") or (err.get("error") if isinstance(err.get("error"), str)
                                                    else (err.get("error") or {}).get("message")) or ""
        except Exception:
            why = ""
        raise GmailError(f"Google said {e.code}{': ' + str(why)[:150] if why else ''}", e.code) from None
    except Exception as e:
        raise GmailError(f"couldn't reach Google ({type(e).__name__})") from None
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise GmailError("Google sent something unreadable") from None


def _token_request(**params):
    d = _load()
    params.update(client_id=d.get("client_id", ""), client_secret=_secret("client_secret"))
    return _http("POST", TOKEN_URL, data=params)


def _access_token(force=False):
    with _lock:
        if not force and _access["token"] and time.time() < _access["until"] - 60:
            return _access["token"]
    refresh = _secret("refresh_token")
    if not refresh:
        raise GmailError("Gmail isn't connected yet - connect it in Nova's settings.")
    try:
        got = _token_request(grant_type="refresh_token", refresh_token=refresh)
    except GmailError as e:
        if e.status in (400, 401):
            raise GmailError("Google no longer accepts Nova's Gmail sign-in (it expires after 7 days while the Google "
                             "app is still in 'Testing'). Connect Gmail again in Nova's settings.", e.status) from None
        raise
    with _lock:
        _access.update(token=got.get("access_token", ""), until=time.time() + int(got.get("expires_in") or 3000))
        return _access["token"]


def _api(path, params=None):
    url = f"{API}/{path}" + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    for attempt in (0, 1):
        tok = _access_token(force=attempt == 1)
        try:
            return _http("GET", url, headers={"Authorization": "Bearer " + tok})
        except GmailError as e:
            if e.status == 401 and attempt == 0:
                continue                              # the cached token went stale: one fresh one, then give up
            raise


# ---------------------------------------------------------------- signing in

def _b64url(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


PAGE = ("<html><body style='font-family:sans-serif;text-align:center;margin-top:20vh'><h2>%s</h2>"
        "<p>You can close this tab and go back to Nova.</p></body></html>")


def connect(on_url=None, timeout=240):
    """Google's sign-in, then keep the refresh token. Blocks until you approve (or `timeout` seconds). Returns the
    connected address. Only a request carrying our own random `state` is accepted."""
    d = _load()
    client_id, client_secret = d.get("client_id", ""), _secret("client_secret")
    if not client_id or not client_secret:
        raise GmailError("Save the client ID and secret from your Google Cloud project first.")
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(24)
    got = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = self.path.startswith("/?") and q.get("state", [""])[0] == state and (q.get("code") or q.get("error"))
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if not ok:
                self.wfile.write((PAGE % "That wasn't Nova's sign-in").encode())
                return
            got.update(code=(q.get("code") or [""])[0], error=(q.get("error") or [""])[0])
            self.wfile.write((PAGE % ("Gmail is connected to Nova" if got["code"] else "Sign-in was cancelled")).encode())

    srv = HTTPServer(("127.0.0.1", 0), Handler)       # loopback only, a free port chosen by Windows
    srv.timeout = 1
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})
    connecting.update(on=True, error="")
    try:
        (on_url or webbrowser.open)(url)
        end = time.time() + timeout
        while not got and time.time() < end:
            srv.handle_request()
    finally:
        srv.server_close()
        connecting["on"] = False
    if not got:
        connecting["error"] = "Sign-in timed out."
        raise GmailError("Sign-in timed out - nothing was approved.")
    if got.get("error") or not got.get("code"):
        connecting["error"] = "Sign-in was cancelled."
        raise GmailError("Sign-in was cancelled.")
    try:
        tok = _token_request(grant_type="authorization_code", code=got["code"], redirect_uri=redirect,
                             code_verifier=verifier)
    except GmailError as e:
        connecting["error"] = str(e)
        raise
    if not tok.get("refresh_token"):
        connecting["error"] = "Google gave no long-lived sign-in."
        raise GmailError("Google gave no long-lived sign-in - remove Nova under myaccount.google.com/permissions and "
                         "connect again.")
    with _lock:
        _access.update(token=tok.get("access_token", ""), until=time.time() + int(tok.get("expires_in") or 3000))
    d = _load()
    d.update(refresh_token=_protect(tok["refresh_token"], True), email="")
    _save(d)
    try:
        d["email"] = _api("profile").get("emailAddress", "")
        _save(d)
    except GmailError:
        pass
    return d.get("email", "")


def disconnect():
    """Tell Google to drop Nova's access, then forget the token here (the client stays so you can reconnect)."""
    refresh = _secret("refresh_token")
    if refresh:
        try:
            _http("POST", REVOKE_URL, data={"token": refresh})
        except GmailError:
            pass                                      # already revoked or offline: forget it here anyway
    d = _load()
    d.pop("refresh_token", None)
    d.pop("email", None)
    _save(d)
    with _lock:
        _access.update(token="", until=0.0)


# ---------------------------------------------------------------- reading mail

INVISIBLE = dict.fromkeys([0x34F, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0xAD, 0x61C, 0x180E] + list(range(0x2000, 0x200B)), " ")


def _clean(text):
    """Newsletters pad their preview with invisible characters: drop them so the brain gets words, not noise."""
    return " ".join(html.unescape(str(text or "")).translate(INVISIBLE).split())


def _header(msg, name):
    for h in (msg.get("payload") or {}).get("headers") or []:
        if (h.get("name") or "").lower() == name.lower():
            return " ".join(str(h.get("value") or "").split())
    return ""


class Mails(list):
    """The mails found, and Gmail's estimate of how many match in all (so 'only one' is never guessed)."""
    total = 0


def search(query="", count=5):
    """The newest mail matching a Gmail search ("" = the inbox)."""
    count = max(1, min(int(count or 5), 10))
    listing = _api("messages", {"q": query or "in:inbox", "maxResults": count})
    out = Mails()
    out.total = int(listing.get("resultSizeEstimate") or 0)
    for m in (listing.get("messages") or [])[:count]:
        msg = _api(f"messages/{m['id']}", {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
        out.append({"id": m["id"], "from": _header(msg, "From"), "subject": _header(msg, "Subject") or "(no subject)",
                    "date": _header(msg, "Date"), "snippet": _clean(msg.get("snippet"))})
    return out


def _decode(data):
    return base64.urlsafe_b64decode(str(data or "") + "=" * (-len(str(data or "")) % 4)).decode("utf-8", "replace")


def _parts(payload):
    yield payload
    for p in payload.get("parts") or []:
        yield from _parts(p)


def _html_to_text(raw):
    raw = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def body_text(msg):
    """The readable text of a mail: its text/plain part, otherwise the HTML one with the tags stripped."""
    plain = html_ = ""
    for p in _parts(msg.get("payload") or {}):
        data = (p.get("body") or {}).get("data")
        if not data:
            continue
        mime = (p.get("mimeType") or "").lower()
        if mime == "text/plain" and not plain:
            plain = _decode(data)
        elif mime == "text/html" and not html_:
            html_ = _decode(data)
    return (plain.strip() or _html_to_text(html_)).strip()


def read(msg_id, max_chars=4000):
    """One mail: {from, subject, date, text}."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(msg_id or "")):
        raise GmailError("That isn't a mail id - search first.")
    msg = _api(f"messages/{msg_id}", {"format": "full"})
    raw = body_text(msg) or _clean(msg.get("snippet"))
    text = chr(10).join(" ".join(line.translate(INVISIBLE).split()) for line in raw.splitlines())
    text = re.sub(r"\n{3,}", chr(10) * 2, text).strip()
    max_chars = max(200, min(int(max_chars or 4000), 12000))
    return {"from": _header(msg, "From"), "subject": _header(msg, "Subject") or "(no subject)",
            "date": _header(msg, "Date"), "text": text[:max_chars] + ("…" if len(text) > max_chars else "")}


# ---------------------------------------------------------------- one-time codes are never passed on

SECRETY = re.compile(r"\b(otp|one[- ]time|verification|passcode|password|pin|cvv|security code|login code|code)\b", re.I)
CODEISH = re.compile(r"(?<!\w)(?=[A-Z0-9]*\d)[A-Z0-9]{4,8}(?!\w)|(?<!\w)\d{3}[ -]\d{3}(?!\w)")


def hide_codes(text):
    """In mail that talks about a code, password or PIN, the code-looking pieces become (hidden): the brain and the
    voice never get them. (Years and short numbers like 2026 or 15 are left alone only when no such word is near.)"""
    if not SECRETY.search(text or ""):
        return text or ""
    return CODEISH.sub("(hidden)", text)
