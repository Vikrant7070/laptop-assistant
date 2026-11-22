"""Clicking like a person (UI Automation) and filling forms / logins without the values reaching the brain."""


import re
import time


from nova import platform
from nova.actions.registry import tool
from nova.actions.tools import common, windows


CLICKABLE = {"ButtonControl", "MenuItemControl", "HyperlinkControl", "TabItemControl", "ListItemControl",
             "CheckBoxControl", "RadioButtonControl", "EditControl", "ComboBoxControl", "TreeItemControl",
             "SplitButtonControl", "MenuBarControl", "ImageControl"}


def _target_window(window):
    if window:
        return windows._find_window(window)
    for w in windows._windows():  # z-order: topmost first; skip Nova herself
        if w[1] not in ("Nova", "Nova 3D", "Nova ear", "tk") and w[2].lower() not in ("pythonw.exe", "textinputhost.exe"):
            return w
    return None


def _scan(window, limit=200, offscreen=False, contains=""):
    """Clickable things in a window, in page order. offscreen=True also returns items scrolled out of view."""
    import uiautomation as auto
    w = _target_window(window)
    if not w:
        return None, []
    root = auto.ControlFromHandle(w[0])
    items, q = [], contains.lower().strip()
    # "video" = video links: on YouTube (and most video sites) their names end with the length, e.g. "… 4 minutes, 22 seconds"
    video = q in ("video", "videos", "views", "song", "songs")
    duration = re.compile(r"\b\d+ (second|minute|hour)s?\b", re.I)
    for ctrl, _depth in auto.WalkControl(root, maxDepth=40):
        if len(items) >= limit:
            break
        try:
            if ctrl.ControlTypeName not in CLICKABLE:
                continue
            hidden = ctrl.IsOffscreen
            if hidden and not offscreen:
                continue
            name = (ctrl.Name or "").strip()
            if not name and ctrl.ControlTypeName != "EditControl":
                continue
            if video:
                if ctrl.ControlTypeName != "HyperlinkControl" or not duration.search(name):
                    continue
            elif q and q not in name.lower():
                continue
            r = ctrl.BoundingRectangle
            if (r.width() <= 0 or r.height() <= 0) and not hidden:
                continue
            items.append({"name": name or "(text box)", "type": ctrl.ControlTypeName.replace("Control", ""),
                          "x": (r.left + r.right) // 2, "y": (r.top + r.bottom) // 2, "offscreen": hidden, "ctrl": ctrl})
        except Exception:
            continue
    return w, items


@tool("List buttons, links, tabs and text boxes in a window (default: the window in front), numbered "
      "in page order. 'contains' filters by text; contains='video' lists only videos (YouTube etc.).",
      friendly="Looked at the buttons", hints="what can I click on this page, buttons links",
      props={"window": common.S, "contains": common.S}, required=[])
def list_clickable(window="", contains=""):
    w, items = _scan(window, limit=300, contains=contains)
    if not w:
        return f"No open window matches '{window}'."
    if not items:
        return f"I couldn't find clickable items{' containing ' + repr(contains) if contains else ''} in '{w[1]}'."
    lines = [f"{n}. {i['name'][:70]} [{i['type']}]" for n, i in enumerate(items[:60], 1)]
    return f"Clickable items in '{w[1]}' (top of page first):\n" + "\n".join(lines)


def _bring_into_view(item, w):
    """Scroll an off-screen item into view and return its fresh centre point."""
    ctrl = item["ctrl"]
    try:
        ctrl.GetScrollItemPattern().ScrollIntoView()
    except Exception:
        for _ in range(15):  # no scroll pattern: wheel down until it shows up
            if not ctrl.IsOffscreen:
                break
            scroll("down", 5, w[1])
            time.sleep(0.25)
    time.sleep(0.6)
    r = ctrl.BoundingRectangle
    return (r.left + r.right) // 2, (r.top + r.bottom) // 2


@tool("Scroll a window up or down with the mouse wheel (amount 5 ≈ half a page).", friendly="Scrolled",
      props={"direction": {"type": "string", "enum": ["up", "down"]}, "amount": common.N, "window": common.S},
      required=["direction"])
def scroll(direction="down", amount=5, window=""):
    """Turn the mouse wheel over a window (amount = wheel notches; ~5 is about half a page)."""
    w = _target_window(window)
    if w:
        left, top, right, bottom = platform.current().window_rect(w[0])
        windows._focus(w[0])
        _move_mouse_smoothly((left + right) // 2, (top + bottom) // 2 + 60, 0.2)
    sign = -1 if direction == "down" else 1
    for _ in range(max(1, min(int(amount), 40))):
        platform.current().mouse(0x0800, sign * 120)               # MOUSEEVENTF_WHEEL
        time.sleep(0.04)
    return f"Scrolled {direction} {amount} notches" + (f" in '{w[1]}'." if w else ".")


@tool("Type into a text box in a window and press Enter - use this to SEARCH on a website (YouTube, Google, "
      "Amazon...) or fill a form field. 'box' is the box's name; leave it empty for the page's search box.",
      friendly="Typed into a box", risky="Type into a box",
      hints="search for something on a website, type in the search box",
      props={"text": common.S, "box": common.S, "window": common.S, "press_enter": common.B}, required=["text"])
def fill_in(text, box="", window="", press_enter=True):
    """Click a text box (e.g. a website's Search box), replace its text, and press Enter - one reliable step."""
    w, items = _scan(window, limit=500)
    if not w:
        return f"No open window matches '{window}'."
    boxes = [i for i in items if i["type"] in ("Edit", "ComboBox")]
    q = box.lower().strip()
    if q:
        match = (next((i for i in boxes if i["name"].lower() == q), None)
                 or next((i for i in boxes if q in i["name"].lower()), None))
    else:  # no name given: the page's own search/text box, not the browser's address bar
        page = [i for i in boxes if "address" not in i["name"].lower()]
        match = next((i for i in page if "search" in i["name"].lower()), None) or (page[0] if page else None)
    if not match:
        return f"No text box{' named ' + repr(box) if box else ''} in '{w[1]}'. Boxes I see: " + \
            ", ".join(i["name"] for i in boxes)[:500]
    if _is_password(match["ctrl"]):
        return NO_SECRETS
    windows._focus(w[0])
    _nova_click(match["x"], match["y"], label=match["name"])
    time.sleep(0.3)
    if _focused_is_password():
        return NO_SECRETS
    platform.current().press("ctrl+a")
    time.sleep(0.1)
    windows._type_into_focus(text)
    if press_enter:
        time.sleep(0.2)
        platform.current().press("enter")
    return f"Typed '{text}' into '{match['name']}'" + (" and pressed Enter." if press_enter else ".")


NO_SECRETS = ("That's a password box - I don't type passwords. Your browser's password manager can fill it "
              "(click the box and pick your saved login), or type it yourself. Use help_login to let me open it and wait.")


def _is_password(ctrl):
    try:
        return bool(ctrl.IsPassword)
    except Exception:
        return False


def _box_value(ctrl):
    try:
        return ctrl.GetValuePattern().Value or ""
    except Exception:
        return ""


def _focused_is_password():
    try:
        import uiautomation as auto
        return _is_password(auto.GetFocusedControl())
    except Exception:
        return False


def _form_boxes(window):
    """The text boxes and drop-downs of a form, top of the page first (also ones scrolled out of view)."""
    w, items = _scan(window, limit=600, offscreen=True)
    if not w:
        return None, []
    for _ in range(3):      # Chrome builds a fresh page's accessibility tree only once something asks for it
        if any(i["type"] in ("Edit", "ComboBox") and "address and search" not in i["name"].lower() for i in items):
            break
        time.sleep(1.0)
        w, items = _scan(window, limit=600, offscreen=True)
    boxes = []
    for i in items:
        if i["type"] not in ("Edit", "ComboBox") or "address and search" in i["name"].lower():
            continue                       # (the browser's own address bar)
        boxes.append({**i, "password": _is_password(i["ctrl"]), "filled": bool(_box_value(i["ctrl"]).strip())})
    return w, boxes


@tool("Fill in a form on the screen (sign-up, application, delivery address...) with the user's saved "
      "details - name, email, phone, birth date, address, city, state, PIN code, company... You never see the "
      "values; they stay on the laptop. Passwords, codes and ID/card numbers are left for the user. It never "
      "submits: ask the user to check the form before you press Submit.", friendly="Filled in the form",
      risky="Fill in the form with your saved details",
      hints="fill in this form, fill out the form with my details, sign up form, application form, my address "
            "name email", props={"window": common.S}, required=[])
def fill_form(window=""):
    from nova.actions import forms
    details = forms.load()
    if not any(details.values()):
        return ("I don't have any form details saved yet. The user can add them in ⚙ Settings → 📝 Forms "
                "(name, email, phone, address...). They stay on this laptop.")
    w, boxes = _form_boxes(window)
    if not w:
        return f"No open window matches '{window}'."
    if not boxes:
        return f"I don't see any form boxes in '{w[1]}'."
    to_fill, left, unknown = forms.plan(boxes, details)
    if not to_fill:
        return ("Nothing on this form matches the details I have. Boxes I see: "
                + ", ".join(b["name"] for b in boxes)[:400])
    from nova.avatar import avatar_client
    windows._focus(w[0])
    time.sleep(0.3)
    done = []
    for box, key in to_fill:
        value = forms.value_for(key, details)
        try:
            x, y = _bring_into_view(box, w) if box["offscreen"] else (box["x"], box["y"])
            if box["type"] == "ComboBox":         # a drop-down: focus it and type, the browser picks the option
                box["ctrl"].SetFocus()
                if avatar_client.running():
                    avatar_client.send("go_point", x=x, y=y, timeout=15)
                    avatar_client.send("finish_point")
            else:
                _nova_click(x, y, label=forms._label(key))
                time.sleep(0.25)
                if _focused_is_password():        # (never - but if a page lies about its box, don't type there)
                    continue
                platform.current().press("ctrl+a")
                time.sleep(0.05)
            platform.current().write(value, delay=0.015)
            time.sleep(0.15)
            done.append(box["name"])
        except Exception:
            unknown.append(box["name"])
    out = f"Filled {len(done)} box(es) in '{w[1]}' with the user's saved details: {', '.join(done)}."
    if left:
        out += f" Left for the user (passwords, codes, ID/card numbers, confirm boxes): {', '.join(left)}."
    if unknown:
        out += f" Not filled - I don't have these: {', '.join(unknown[:12])}."
    return out + " I did NOT submit it: ask the user to check the form, and only press Submit / Next if they say so."


def _gone(ctrl, title, hwnd, w_title_now):
    """Has the page moved on? (the box is gone or hidden, or the window's title changed)"""
    try:
        if w_title_now() != title:
            return True
        r = ctrl.BoundingRectangle
        return not ctrl.Exists(0, 0) or r.width() <= 0
    except Exception:
        return True


def _title_of(hwnd):
    return platform.current().title(hwnd)[:511]


@tool("Help the user sign in to a website: you click the login box so their browser / password "
      "manager offers the saved login, then wait while they pick it and sign in. You never ask for, see or type "
      "a password.", friendly="Helped you sign in",
      hints="log in sign in login to the site account, my password, help me log in",
      props={"window": common.S, "wait_secs": common.N}, required=[])
def help_login(window="", wait_secs=150):
    """Open a site's login box so the password manager offers the saved login; wait while the user signs in."""
    w, boxes = _form_boxes(window)
    if not w:
        return f"No open window matches '{window}'."
    pw = next((b for b in boxes if b["password"]), None)
    user = next((b for b in boxes if not b["password"] and LOGIN_RX.search(b["name"])), None)
    target = user or pw
    if not target:
        return (f"I don't see a login box in '{w[1]}'. The user may need to click 'Sign in' first "
                "(click_element), then call help_login again.")
    from nova.avatar import avatar_client
    windows._focus(w[0])
    time.sleep(0.3)
    x, y = _bring_into_view(target, w) if target["offscreen"] else (target["x"], target["y"])
    try:
        target["ctrl"].SetFocus()       # the box is ready at once, before she has walked over
    except Exception:
        pass  # intentional: UI Automation/cleanup is best effort; the tool reports its own result
    _nova_click(x, y, label="Login")
    time.sleep(0.4)
    if avatar_client.running():
        avatar_client.send("say", text="Pick your saved login 🔑 (I don't keep passwords) - I'll wait.", secs=8)
    pointing = windows._point_at_focus("Your login 🔑")
    watch, title = (pw or target)["ctrl"], w[1]
    end = time.time() + max(10, min(int(wait_secs or 150), 300))
    try:
        while time.time() < end:
            if _gone(watch, title, w[0], lambda: _title_of(w[0])):
                return (f"The login page in '{title}' has moved on - the user has signed in (or gone to the next "
                        "step, e.g. a one-time code: then use wait_for_code).")
            time.sleep(1.0)
    finally:
        if pointing:
            windows._stop_pointing()
    return (f"Still on the login page in '{title}' after waiting. The user hasn't finished signing in - "
            "ask them if they need help; never ask them for their password.")


LOGIN_RX = re.compile(r"e-?mail|user|login|phone|mobile|account|sign\s*in", re.I)


@tool("A site asks for a one-time code (OTP / verification code from SMS, email or an "
      "authenticator): point at the code box and wait while the user types it. You don't read or keep the code.",
      friendly="Waited for your code",
      hints="otp verification code one time password two factor 2fa sms code authenticator code enter the code",
      props={"window": common.S, "wait_secs": common.N}, required=[])
def wait_for_code(window="", wait_secs=180):
    """Point at the one-time-code (OTP) box and wait while the user types the code. The code itself is not read."""
    from nova.actions import forms
    w, boxes = _form_boxes(window)
    if not w:
        return f"No open window matches '{window}'."
    codes = [b for b in boxes if forms.CODE_BOX.search(b["name"]) and not forms.NOT_CODE.search(b["name"])]
    if not codes:       # sites that use 4-8 one-digit boxes often leave them unnamed
        blank = [b for b in boxes if b["name"] in ("(text box)", "") and not b["password"]]
        codes = blank if 4 <= len(blank) <= 8 else []
    if not codes:
        return f"I don't see a code box in '{w[1]}'. Is the site asking for a one-time code yet?"
    first, last = codes[0], codes[-1]
    from nova.avatar import avatar_client
    windows._focus(w[0])
    time.sleep(0.3)
    x, y = _bring_into_view(first, w) if first["offscreen"] else (first["x"], first["y"])
    try:
        first["ctrl"].SetFocus()        # the cursor is in the box at once - the user may type before she walks over
    except Exception:
        pass  # intentional: UI Automation/cleanup is best effort; the tool reports its own result
    _nova_click(x, y, label="Code box")
    if avatar_client.running():
        avatar_client.send("say", text="Type the code from your phone here 🔢 - I'll wait.", secs=8)
    pointing = windows._point_at_focus("Your code 🔢")
    title = w[1]
    end = time.time() + max(10, min(int(wait_secs or 180), 300))
    try:
        while time.time() < end:
            if _gone(first["ctrl"], title, w[0], lambda: _title_of(w[0])):
                return "The code went in and the page moved on. (I didn't read the code.)"
            if len(codes) > 1 and _box_value(last["ctrl"]).strip():
                return ("The user typed the code in all the boxes. (I didn't read it.) If the page didn't move on "
                        "by itself, ask before pressing Verify / Submit.")
            if len(codes) == 1 and len(re.sub(r"\D", "", _box_value(first["ctrl"]))) >= 4:
                time.sleep(2.0)          # let them finish typing
                return ("The user typed the code. (I didn't read it.) If the page didn't move on by itself, ask "
                        "before pressing Verify / Submit.")
            time.sleep(0.8)
    finally:
        if pointing:
            windows._stop_pointing()
    return "No code was typed while I waited. Ask the user whether the code arrived or they need it sent again."


# Video player buttons (YouTube names them like "Full screen (f)", "Pause keyboard shortcut k", "Mute (m)").
VIDEO_BUTTONS = {
    "fullscreen": ["full screen", "fullscreen"],
    "exit_fullscreen": ["exit full screen", "exit fullscreen"],
    "pause": ["pause"],
    "play": ["play"],
    "next": ["next"],
    "mute": ["mute"],
    "unmute": ["unmute"],
    "captions": ["subtitles", "captions"],
    "theater": ["theater mode", "default view"],
}


VIDEO_SITES = ("youtube", "netflix", "prime video", "hotstar", "jiocinema", "vimeo", "twitch")


def _video_window():
    for w in windows._windows():
        if any(s in w[1].lower() for s in VIDEO_SITES):
            return w
    return None


@tool("Control a playing video (YouTube etc.) with its own player buttons: make the VIDEO full screen "
      "(not the browser window), exit full screen, play, pause, next video, mute, unmute, captions, theater mode.",
      friendly="Pressed a video button", hints="youtube video full screen pause play next video captions subtitles",
      props={"action": {"type": "string", "enum": list(VIDEO_BUTTONS)}}, required=["action"])
def video_control(action):
    """Press a video player's own button: fullscreen, exit_fullscreen, play, pause, next, mute, unmute, captions, theater."""
    if action not in VIDEO_BUTTONS:
        return f"action must be one of: {', '.join(VIDEO_BUTTONS)}"
    w = _video_window()
    if not w:
        return "I don't see a video open (YouTube, Netflix, ...)."
    _, items = _scan(w[1], limit=2000)
    buttons = [i for i in items if i["type"] == "Button"]
    wanted = VIDEO_BUTTONS[action]

    def find(words, avoid=()):
        for i in buttons:
            n = i["name"].lower()
            if any(n.startswith(k) for k in words) and not any(n.startswith(a) for a in avoid):
                return i
        return None

    # The same button changes name with its state (Pause <-> Play, Full screen <-> Exit full screen, Mute <-> Unmute).
    if action == "fullscreen" and find(VIDEO_BUTTONS["exit_fullscreen"]):
        return "The video is already full screen."
    if action == "exit_fullscreen" and not find(VIDEO_BUTTONS["exit_fullscreen"]):
        return "The video isn't full screen."
    if action == "pause" and not find(["pause"]) and find(["play"]):
        return "The video is already paused."
    if action == "play" and find(["pause"]):
        return "The video is already playing."
    if action == "mute" and find(["unmute"]):
        return "The video is already muted."
    match = find(wanted, avoid=("exit",) if action == "fullscreen" else ("unmute",) if action == "mute" else ())
    if not match:
        return f"I couldn't find the {action.replace('_', ' ')} button on '{w[1][:60]}'."
    windows._focus(w[0])
    time.sleep(0.2)
    _move_mouse_smoothly(match["x"], match["y"] - 120, 0.2)  # wake the player so its buttons show
    time.sleep(0.3)
    _nova_click(match["x"], match["y"], label=match["name"])
    return f"Pressed '{match['name']}' on '{w[1][:60]}'."


@tool("Click the Nth item (1 = first) whose name contains some text, scrolling to it if needed. "
      "To play a video: contains='video' (e.g. 'play the first video' -> number=1, 'the 7th video' -> number=7).",
      friendly="Clicked an item", risky="Click an item", hints="play the first video, open the second result",
      props={"contains": common.S, "number": common.N, "window": common.S}, required=["contains", "number"])
def click_nth(contains, number, window=""):
    """Click the Nth clickable item (in page order) whose name contains some text - e.g. the 7th YouTube video."""
    w, items = _scan(window, limit=500, offscreen=True, contains=contains)
    if not w:
        return f"No open window matches '{window}'."
    n = int(number)
    if n < 1 or n > len(items):
        return f"There are only {len(items)} items containing '{contains}' in '{w[1]}'."
    item = items[n - 1]
    windows._focus(w[0])
    x, y = _bring_into_view(item, w) if item["offscreen"] else (item["x"], item["y"])
    time.sleep(0.2)
    _nova_click(x, y, label=item["name"])
    return f"Clicked #{n} containing '{contains}': '{item['name'][:80]}' in '{w[1]}'."


def _move_mouse_smoothly(x, y, secs=0.35):
    sx, sy = platform.current().cursor_pos()
    steps = 22
    bend = (x - sx) * 0.08
    for i in range(1, steps + 1):
        t = i / steps
        e = t * t * (3 - 2 * t)  # ease in-out, like a hand
        platform.current().set_cursor(int(sx + (x - sx) * e), int(sy + (y - sy) * e - bend * (t - t * t) * 4))
        time.sleep(secs / steps)


def _mouse_click(x, y, button="left", double=False):
    down, up = (0x0008, 0x0010) if button == "right" else (0x0002, 0x0004)
    _move_mouse_smoothly(x, y)
    for _ in range(2 if double else 1):
        platform.current().mouse(down)
        time.sleep(0.05)
        platform.current().mouse(up)
        time.sleep(0.08)


def _nova_click(x, y, button="left", double=False, label=""):
    """Nova walks over, points at the spot, and the real mouse clicks it."""
    from nova.avatar import avatar_client
    if avatar_client.running():
        if label:
            avatar_client.send("say", text=f"Clicking “{label[:40]}”!", secs=3)
        avatar_client.send("go_point", x=x, y=y, timeout=15)
        avatar_client.send("clickthrough", on=True)
    _mouse_click(x, y, button, double)
    if avatar_client.running():
        avatar_client.send("clickthrough", on=False)
        avatar_client.send("finish_point")


@tool("Click a button/menu/link/tab/text box by its name in a window. Nova walks over and presses it. "
      "Call list_clickable first if you don't know the exact name.", friendly="Clicked a button",
      risky="Click something",
      props={"name": common.S, "window": common.S, "button": {"type": "string", "enum": ["left", "right"]}, "double": common.B},
      required=["name"])
def click_element(name, window="", button="left", double=False):
    w, items = _scan(window, limit=500, offscreen=True)
    if not w:
        return f"No open window matches '{window}'."
    q = name.lower().strip()
    visible_first = sorted(items, key=lambda i: i["offscreen"])
    match = (next((i for i in visible_first if i["name"].lower() == q), None)
             or next((i for i in visible_first if i["name"].lower().startswith(q)), None)
             or next((i for i in visible_first if q in i["name"].lower()), None))
    if not match:
        sample = ", ".join(i["name"][:40] for i in items if not i["offscreen"])[:900]
        return f"No '{name}' in '{w[1]}'. Some things I can click: {sample}"
    windows._focus(w[0])
    time.sleep(0.3)
    x, y = _bring_into_view(match, w) if match["offscreen"] else (match["x"], match["y"])
    _nova_click(x, y, button, double, match["name"])
    return f"Clicked '{match['name']}' ({match['type']}) in '{w[1]}'."


@tool("Click at screen coordinates (pixels).", friendly="Clicked on screen", risky="Click on the screen",
      props={"x": common.N, "y": common.N, "button": {"type": "string", "enum": ["left", "right"]}, "double": common.B},
      required=["x", "y"])
def click_at(x, y, button="left", double=False):
    _nova_click(int(x), int(y), button, double)
    return f"Clicked at ({int(x)}, {int(y)})."
