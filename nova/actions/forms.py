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
import json
import re
import threading
import time

from nova.shared import DATA

FILE = DATA / "form_details.json"
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


def load():
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        return {k: str(d.get(k) or "").strip() for k in KEYS}
    except (OSError, ValueError):
        return {k: "" for k in KEYS}


def save(details):
    """Save the ⚙ dialog's details. Returns (saved dict, error or "")."""
    clean = {}
    for k in KEYS:
        v = " ".join(str((details or {}).get(k) or "").split())[:200]
        if v and SECRET_VALUE.match(v.replace(" ", "") if k not in ("phone",) else v):
            return None, f"That looks like a card or ID number in '{dict((a, b) for a, b, _ in FIELDS)[k]}' - those aren't kept here."
        clean[k] = v
    with _lock:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    return clean, ""


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
