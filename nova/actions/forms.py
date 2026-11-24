"""📝 Forms and logins, done the safe way.

- fill_form: types the user's saved form details (name, email, phone, address...) into a form's boxes. The details
  live only in data/form_details.json on this laptop and are typed in here, locally: the brain only ever hears WHICH
  boxes were filled, never the values, so they don't go to a cloud model as text. She never presses Submit.
- help_login: clicks into a site's login box so the browser / password manager offers its saved login, and waits
  while the user picks it. Nova never sees, keeps or types passwords.
- wait_for_code: points at the one-time-code (OTP) box and waits while the user types the code from their phone.
  She doesn't read the code or keep it.
Passwords, OTPs, card / bank numbers and government IDs are never saved here and never typed by Nova
(see tools.type_text / fill_in, which refuse password boxes).
"""
import base64
import hashlib
import json
import re
import threading
import time

from nova import platform
from nova import store
from nova.log import log_exception

_lock = threading.Lock()

# (key, label in the ⚙ dialog, what form boxes for it are called). Order matters: the first match wins, so the
# specific ones ("first name", "PIN code") come before the general ones ("name", "PIN" = a secret).
FIELDS = [
    ("first_name", "First name", r"first\s*name|given\s*name|fname|forename"),
    ("last_name", "Last name", r"last\s*name|surname|family\s*name|lname"),
    ("email", "Email", r"e-?mail"),
    ("phone", "Phone / mobile", r"phone|mobile|contact\s*(no|number)|whatsapp|\btel\b"),
    ("birth_date", "Date of birth", r"birth|\bdob\b"),
    ("address", "Address (street, house)", r"address|street|house|flat|locality|area"),
    ("city", "City", r"city|town|district"),
    ("state", "State", r"\bstate\b|province|region"),
    ("pincode", "PIN code / ZIP", r"pin\s*-?\s*code|pincode|postal|post\s*code|\bzip\b"),
    ("country", "Country", r"country|nation"),
    ("company", "Company / organisation", r"company|organi[sz]ation|employer|college|institute"),
    ("job_title", "Job title", r"job\s*title|designation|position|occupation|role"),
    ("website", "Website / LinkedIn", r"website|linkedin|portfolio|url"),
    # last: "Company name" / "Name of the company" / "Father's name" must not get her name
    ("full_name", "Full name", r"full\s*name|your\s*name|^name\b(?!\s+of)|applicant'?s?\s*name|candidate'?s?\s*name"),
]
# The order the ⚙ dialog shows them in
DIALOG_ORDER = ["full_name", "first_name", "last_name", "email", "phone", "birth_date", "address", "city", "state",
                "pincode", "country", "company", "job_title", "website"]
KEYS = [k for k, _, _ in FIELDS]
_MATCH = [(k, re.compile(rx, re.I)) for k, _, rx in FIELDS]

# Boxes Nova never fills, whatever they are called: secrets stay with the user and their password manager.
SECRET_BOX = re.compile(r"pass(word|code|phrase)?\b|\bpwd\b|\bpin\b|otp|one[\s-]*time|verification|verify|"
                        r"\bcode\b|captcha|security\s*(question|answer)|\bcvv\b|\bcvc\b|card|expiry|account\s*(no|num)|"
                        r"\bifsc\b|aadhaa?r|\bpan\b|passport|\bssn\b|social\s*security|tax\s*id|licen[cs]e", re.I)
# What may never be SAVED as a form detail (someone typing a password into the ⚙ dialog's wrong box).
SECRET_VALUE = re.compile(r"^\d{12}$|^\d{4}\s?\d{4}\s?\d{4}\s?\d{4}$|^[A-Z]{5}\d{4}[A-Z]$")

LOGIN_USER = re.compile(r"e-?mail|user\s*(name|id)?|login|username|phone|mobile|account", re.I)
CODE_BOX = re.compile(r"otp|one[\s-]*time|verification\s*code|security\s*code|\bcode\b|2fa|two[\s-]*(factor|step)|"
                      r"passcode|authenticat", re.I)
NOT_CODE = re.compile(r"pin\s*-?\s*code|postal|zip|promo|coupon|referral|discount|gift|country\s*code", re.I)


# ------------------------------------------------------------------ the profile file: encrypted with DPAPI
# data/form_details.json = {"v": 2, "enc": base64(DPAPI(json {"details": {...}, "asked": [hashes]}))}. Only this
# Windows account can read it. An older plain file is read once and written back encrypted.

def _read_all():
    """-> {"details": {key: value}, "asked": [hashes]} (blank when missing or unreadable)."""
    blank = {"details": {k: "" for k in KEYS}, "asked": []}
    f = store.current().forms
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return blank
    except (OSError, ValueError):
        log_exception("forms: reading the saved details")
        return blank
    if isinstance(raw, dict) and "enc" in raw:
        try:
            d = json.loads(platform.current().unprotect(base64.b64decode(raw["enc"])).decode("utf-8"))
        except Exception:
            log_exception("forms: the saved details can't be decrypted on this Windows account")
            return blank
    else:                                   # the old plain file: encrypt it now
        d = {"details": raw if isinstance(raw, dict) else {}, "asked": []}
        _write_all(d)
    det = d.get("details") or {}
    return {"details": {k: str(det.get(k) or "").strip() for k in KEYS}, "asked": list(d.get("asked") or [])[-500:]}


def _write_all(d):
    blob = json.dumps({"details": d["details"], "asked": d.get("asked", [])[-500:]}, ensure_ascii=False).encode("utf-8")
    enc = base64.b64encode(platform.current().protect(blob)).decode()
    store.current().write_json(store.current().forms, {"v": 2, "enc": enc})


def load():
    with _lock:
        return _read_all()["details"]


# Field names that are never a form detail, even when asked: passwords, codes, money and ID numbers.
FORBIDDEN_KEY = re.compile(r"pass(word|code|phrase|port)?|\bpwd\b|otp|\bpin\b|cvv|cvc|card|bank|account|ifsc|iban|"
                           r"aadhaa?r|\bpan\b|\bssn\b|social|licen[cs]e|voter|tax|upi|secret|token|api\s*key", re.I)
PAN = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$", re.I)
PASSPORT = re.compile(r"^[A-Z]\d{7}$", re.I)
IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$", re.I)


def _luhn(digits):
    total, alt = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if alt:
            n = n * 2 - 9 if n > 4 else n * 2
        total, alt = total + n, not alt
    return total % 10 == 0


def refuse(key, value):
    """Why this may never be saved ("" = it may). Cards, bank accounts, Aadhaar / PAN / passport, IFSC, codes."""
    v = " ".join(str(value or "").split())
    if FORBIDDEN_KEY.search(str(key or "").replace("_", " ")):
        return "Passwords, codes, card / bank numbers and ID numbers (Aadhaar, PAN, passport) are never kept."
    if not v:
        return ""
    compact = re.sub(r"[\s-]", "", v)
    digits = re.sub(r"\D", "", v)
    only_digits = compact.isdigit()
    what = ""
    if SECRET_VALUE.match(compact) or PAN.match(compact) or PASSPORT.match(compact) or IFSC.match(compact):
        what = "a card or ID number"
    elif only_digits and 13 <= len(digits) <= 19 and (key != "phone" or _luhn(digits) or len(digits) > 15):
        what = "a card number"
    elif only_digits and key == "phone" and len(digits) > 15:
        what = "a card or account number"
    elif only_digits and key not in ("phone", "pincode", "birth_date") and len(digits) >= 4:
        what = "a code or account number"
    if what:
        return f"That looks like {what} in '{_label(key) if key in KEYS else key}' - those aren't kept here."
    return ""


def save(details):
    """Save the ⚙ dialog's details (encrypted). Returns (saved dict, error or ""). A forbidden field (password,
    card, passport...) or a value that looks like one is refused, not silently dropped."""
    details = details or {}
    for k, v in details.items():
        if k not in KEYS and str(v or "").strip() and FORBIDDEN_KEY.search(str(k).replace("_", " ")):
            return None, refuse(k, v)
    clean = {}
    for k in KEYS:
        v = " ".join(str(details.get(k) or "").split())[:200]
        err = refuse(k, v)
        if err:
            return None, err
        clean[k] = v
    with _lock:
        d = _read_all()
        d["details"] = clean
        _write_all(d)
    return clean, ""


def delete_all():
    """Forget every saved detail (and what was asked about)."""
    with _lock:
        _write_all({"details": {k: "" for k in KEYS}, "asked": []})


def saved_keys():
    """Which details are saved (names only - this is all the brain may know)."""
    d = load()
    return [k for k in KEYS if d[k]]


def classify(name):
    """What a form box asks for: one of KEYS, "secret" (never filled), or None (not something Nova knows)."""
    n = " ".join(str(name or "").replace("_", " ").replace("*", " ").split())
    if not n:
        return None
    if re.search(r"user\s*(name|id)|login|sign\s*in", n, re.I):
        return None                        # a login's user name: that's the password manager's job
    postal = re.search(r"pin\s*-?\s*code|pincode|postal|post\s*code|\bzip\b", n, re.I)
    if SECRET_BOX.search(n) and not postal:
        return "secret"
    for key, rx in _MATCH:
        if rx.search(n):
            return key
    return None


def value_for(key, details):
    """The value for a box, working out first / last name from the full name (and the other way round)."""
    v = details.get(key, "")
    if v:
        return v
    full = details.get("full_name", "")
    if key == "first_name" and full:
        return full.split()[0]
    if key == "last_name" and len(full.split()) > 1:
        return full.split()[-1]
    if key == "full_name" and details.get("first_name"):
        return " ".join(x for x in (details.get("first_name"), details.get("last_name")) if x)
    return ""


def plan(boxes, details):
    """Decide what goes where. boxes: [{"name", "type", "password", "filled"}...] in page order.
    Returns (to_fill [(box, key)], left_for_user [names], unknown [names]) - no values in the lists' names."""
    to_fill, left, unknown, used = [], [], [], set()
    for b in boxes:
        if b.get("password"):
            left.append(b["name"] or "password")
            continue
        key = classify(b["name"])
        if key == "secret":
            left.append(b["name"])
        elif key is None:
            if b["name"] and not b.get("filled") and b["name"] != "(text box)":
                unknown.append(b["name"])
        elif b.get("filled"):
            continue                       # the user (or the browser) already filled it: leave it alone
        elif key in used:
            left.append(b["name"])         # "Confirm email", "Address line 2": the user checks / types that one
        elif value_for(key, details):
            to_fill.append((b, key))
            used.add(key)
        else:
            unknown.append(b["name"])
    return to_fill, left, unknown


def _label(key):
    return next(lbl for k, lbl, _ in FIELDS if k == key)


# ------------------------------------------------------------------ noticing details the user types (Phase 7)
# The activity log (activity.Sampler, only while it is on) reads the value of a form box that asks for a profile
# detail when the focus leaves it - never a password box, never any other box. New values wait here, in memory only,
# until the page moves on; then Nova asks ONCE with a question card that names the details (labels), never the values.
# Yes saves them encrypted; No forgets them. Either way that value is never asked about again (a hash is kept).

PENDING_SECS = 900
VALID = {
    "email": re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$"),
    "phone": re.compile(r"^\+?[\d\s()-]{7,20}$"),
    "pincode": re.compile(r"^[A-Za-z0-9 -]{3,10}$"),
    "website": re.compile(r"^\S+\.\S+$"),
}
NAME_KEYS = ("full_name", "first_name", "last_name")


def _valid(key, v):
    if key in VALID:
        return bool(VALID[key].match(v))
    if key in NAME_KEYS:
        return bool(re.match(r"^[^\W\d_][^\d@]{0,79}$", v))
    return 2 <= len(v) <= 200 and "@" not in v


def _hash(key, v):
    return hashlib.sha256(f"{key}\0{v.lower()}".encode("utf-8")).hexdigest()[:20]


class Notice:
    def __init__(self):
        self.pending, self.pending_since, self.offered = {}, 0.0, {}

    @staticmethod
    def wanted(box_name):
        """Is this a box whose value Nova may look at (a profile detail, not a secret)?"""
        return classify(box_name) in KEYS

    def seen(self, box_name, value, now=None):
        """The user left a box holding `value`. Kept (in memory) only when it is a new, valid, allowed detail."""
        key = classify(box_name)
        v = " ".join(str(value or "").split())[:200]
        if key not in KEYS or not v or refuse(key, v) or not _valid(key, v):
            return False
        with _lock:
            d = _read_all()
        if d["details"].get(key, "").lower() == v.lower() or _hash(key, v) in d["asked"]:
            return False
        self.pending[key] = v
        self.pending_since = self.pending_since or (now or time.time())
        return True

    def offer(self, now=None):
        """The page moved on: the keys to ask about (each value asked once, ever). Values stay here."""
        now = now or time.time()
        if not self.pending or now - self.pending_since > PENDING_SECS:
            self.forget()
            return []
        with _lock:
            d = _read_all()
            d["asked"] = d["asked"] + [_hash(k, v) for k, v in self.pending.items()]
            _write_all(d)
        self.offered, self.pending, self.pending_since = dict(self.pending), {}, 0.0
        return [k for k in KEYS if k in self.offered]

    def accept(self):
        """Yes: the offered details join the saved ones. -> the labels saved."""
        with _lock:
            d = _read_all()
            d["details"].update({k: v for k, v in self.offered.items() if not refuse(k, v)})
            _write_all(d)
        saved = [_label(k) for k in KEYS if k in self.offered]
        self.offered = {}
        return saved

    def forget(self):
        self.pending, self.pending_since, self.offered = {}, 0.0, {}


def question_text(keys):
    """'your email and phone' - labels only, never values."""
    names = [_label(k).split(" (")[0].split(" /")[0].lower() for k in keys]
    return ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1] if names else ""


notice = Notice()
