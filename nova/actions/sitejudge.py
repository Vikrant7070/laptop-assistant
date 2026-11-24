"""The website judge (manager plan Phase 5): open the page the worker made and measure it.

    wanted(folder, kind)  -> does this job get the website judge? ("new site" / "new app", or an index.html / an npm
                             dev script in the folder)
    site(folder, kind, env, checkpoint, conf, built) -> [page, console, mobile, links, lighthouse]

How it opens the page, from the project's own files (nothing is installed):
    built by the build check (dist/ build/ out/ with an index.html) -> served from there
    an index.html in the folder (or public/)                        -> served from the folder
    a package.json "dev" / "start" script                           -> that command, time-limited, with the worker's
                                                                       safety env; its address read from its output
Nova serves it on 127.0.0.1 itself (a proxy in front of a dev server) so a tiny probe script goes into every HTML page:
it notes console errors, script errors and files that failed to load. The page is opened by the headless Chrome or
Edge already on the laptop (own throwaway profile), inside an iframe exactly `width` px wide (Chrome won't make a
window narrower than ~500 px), and the probe measures sideways scroll there. Local links are fetched one by one;
links to other sites aren't (no network for judging). Lighthouse runs only when `lighthouse` is on the PATH.
No browser -> every check skipped, with why.
"""
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from nova.actions import judges
from nova.log import log_exception

NAMES = ("page", "console", "mobile", "links", "lighthouse")
FRAME_PATH = "/__nova_judge__"
DEV_SCRIPTS = ("dev", "start")
NOISE = re.compile(r"\[vite\]|\[hmr\]|websocket|webpack-dev-server|sockjs|favicon", re.I)   # dev-server chatter
ADDRESS = re.compile(r"https?://(?:localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)(?::\d+)?/?")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
RESULT = re.compile(r'<script[^>]*id="__nova_judge"[^>]*>(.*?)</script>', re.S)
HEADS = (re.compile(r"<head\b[^>]*>", re.I), re.compile(r"<html\b[^>]*>", re.I))
TYPES = {".html": "text/html", ".htm": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
         ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
         ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
         ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2", ".wasm": "application/wasm",
         ".txt": "text/plain", ".xml": "application/xml", ".webmanifest": "application/manifest+json"}
SKIP_LINK = re.compile(r"^(?:#|mailto:|tel:|javascript:|data:|sms:)", re.I)
MAX_LINKS = 40

PROBE = r"""(function(){if(window.__novaJudge)return;var R={errors:[]};window.__novaJudge=R;
function add(m){m=String(m).slice(0,200);if(R.errors.length<20&&R.errors.indexOf(m)<0)R.errors.push(m)}
var ce=console.error;console.error=function(){try{add([].map.call(arguments,function(a){return a&&a.message||String(a)}).join(" "))}catch(e){}return ce.apply(console,arguments)};
addEventListener("error",function(e){var t=e.target;if(t&&t!==window&&t.getAttribute)add("failed to load "+(t.getAttribute("src")||t.getAttribute("href")||t.tagName));else add((e.message||"script error")+(e.filename?" ("+e.filename.split("/").pop()+":"+e.lineno+")":""))},true);
addEventListener("unhandledrejection",function(e){var r=e.reason;add("unhandled promise rejection: "+(r&&r.message||r))});
R.measure=function(){var d=document.documentElement,b=document.body;R.vw=innerWidth;R.sw=Math.max(d.scrollWidth,b?b.scrollWidth:0);R.wide="";
if(R.sw>R.vw+1&&b){var a=b.getElementsByTagName("*");for(var i=0;i<a.length;i++){var r=a[i].getBoundingClientRect();if(r.width>0&&r.right>R.vw+1){var el=a[i],c=typeof el.className=="string"?el.className.trim().split(/\s+/)[0]:"";R.wide=el.tagName.toLowerCase()+(el.id?"#"+el.id:"")+(c?"."+c:"");break}}}
R.title=document.title;R.links=[];var l=document.querySelectorAll("a[href]");for(var j=0;j<l.length&&j<200;j++)R.links.push(l[j].getAttribute("href"));R.measure=null;return R}})();"""

FRAME = """<!doctype html><html><head><meta charset="utf-8"><title>judge</title></head><body style="margin:0">
<iframe id="f" src="SRC" style="width:WIDTHpx;height:812px;border:0;display:block"></iframe>
<script>var f=document.getElementById("f"),done=false;function out(R){if(done)return;done=true;var s=document.createElement("script");
s.type="application/json";s.id="__nova_judge";s.textContent=JSON.stringify(R);document.body.appendChild(s)}
f.addEventListener("load",function(){setTimeout(function(){var R;try{var J=f.contentWindow.__novaJudge;R=J?(J.measure?J.measure():J):{noprobe:true}}
catch(e){R={blocked:String(e)}}out(R)},WAIT)});</script></body></html>"""


def _check(name, state, why, detail=""):
    from nova.actions import jobchecks
    return jobchecks._check(name, state, why, detail)


def _all(state, why, names=NAMES):
    return [_check(n, state, why) for n in names]


# ------------------------------------------------------------------ is it a website, and how to open it

def _index(folder):
    for d in ("", "public"):
        p = Path(folder, d, "index.html")
        if p.is_file():
            return str(p.parent)
    return ""


def _dev_script(folder):
    s = judges.scripts(folder)
    return next((n for n in DEV_SCRIPTS if s.get(n)), "")


def looks_like_site(folder):
    return bool(_index(folder) or _dev_script(folder))


def wanted(folder, kind=""):
    return kind in ("new site", "new app") or looks_like_site(folder)


def how(folder, built=""):
    """-> {"serve": folder, "spa": bool} | {"dev": argv, "script": name} | {"why": why it can't be opened}."""
    if built and Path(built, "index.html").is_file():
        return {"serve": built, "spa": True}
    script = _dev_script(folder)
    if script:
        argv, why = judges.npm(folder, script)
        if argv:
            return {"dev": argv, "script": script}
        if not _index(folder):
            return {"why": f"can't start its {script} script: {why}"}
    if _index(folder):
        return {"serve": _index(folder), "spa": False}
    return {"why": "no page to open (no index.html, no dev or start script)"}


def browser():
    """The headless browser already on the laptop: Chrome, else Edge. "" when neither is there."""
    for name in ("chrome", "msedge"):
        exe = shutil.which(name)
        if exe:
            return exe
    for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"), os.environ.get("PROGRAMFILES(X86)", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
            if base and Path(base, rel).is_file():
                return str(Path(base, rel))
    return ""


# ------------------------------------------------------------------ Nova's own little server (static or proxy)

def inject(html):
    tag = "<script>" + PROBE + "</script>"
    for head in HEADS:
        m = head.search(html)
        if m:
            return html[:m.end()] + tag + html[m.end():]
    return tag + html


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # intentional: the judge's server is quiet; misses are kept in server.missing

    def _send(self, code, body=b"", ctype="text/plain"):
        if ctype.startswith("text/html"):
            body = inject(body.decode("utf-8", "replace")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _miss(self, path, code):
        if not NOISE.search(path):
            self.server.missing.append(f"{path} ({code})")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        srv, path = self.server, urllib.parse.urlsplit(self.path).path
        if path == FRAME_PATH:
            return self._send(200, srv.frame.encode("utf-8"), "text/html")
        if srv.upstream:
            return self._proxy()
        root = Path(srv.root).resolve()
        try:
            target = (root / urllib.parse.unquote(path).lstrip("/")).resolve()
        except (OSError, ValueError):
            return self._send(400)
        if target != root and root not in target.parents:
            return self._send(403)
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file() and srv.spa and not Path(path).suffix:
            target = root / "index.html"
        if not target.is_file():
            self._miss(path, 404)
            return self._send(404, b"not found")
        ctype = TYPES.get(target.suffix.lower(), "application/octet-stream")
        try:
            self._send(200, target.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
        except OSError:
            self._send(500)

    def _proxy(self):
        req = urllib.request.Request(self.server.upstream.rstrip("/") + self.path, method=self.command,
                                     headers={"Accept": self.headers.get("Accept", "*/*")})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return self._send(r.status, r.read(), r.headers.get("Content-Type", "application/octet-stream"))
        except urllib.error.HTTPError as e:
            self._miss(urllib.parse.urlsplit(self.path).path, e.code)
            return self._send(e.code, e.read() or b"", e.headers.get("Content-Type", "text/plain"))
        except (OSError, ValueError) as e:
            return self._send(502, str(e).encode("utf-8", "replace"))


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root="", upstream="", spa=False, width=375, wait=800):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.root, self.upstream, self.spa, self.missing = root, upstream, spa, []
        self.frame = FRAME.replace("SRC", "/").replace("WIDTH", str(int(width))).replace("WAIT", str(int(wait)))
        self.url = f"http://127.0.0.1:{self.server_address[1]}"

    def __enter__(self):
        from nova import runtime
        runtime.spawn("sitejudge.serve", self.serve_forever)
        return self

    def __exit__(self, *a):
        self.shutdown()
        self.server_close()


# ------------------------------------------------------------------ a dev server (npm run dev / start)

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _answers(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except (OSError, ValueError):
        return False


class Dev:
    """Starts the project's dev script and finds its address; stopped (with its whole process tree) on exit."""

    def __init__(self, folder, argv, env, secs, checkpoint=None):
        self.folder, self.argv, self.secs, self.checkpoint = folder, argv, secs, checkpoint
        self.port, self.out, self.proc = _free_port(), [], None
        self.env = {**(env or os.environ), "PORT": str(self.port), "BROWSER": "none", "NO_COLOR": "1",
                    "FORCE_COLOR": "0"}

    def start(self):
        """-> (address, "") or ("", why it didn't start)."""
        from nova import runtime
        shown = " ".join([Path(self.argv[0]).stem] + self.argv[1:])
        try:
            self.proc = subprocess.Popen(self.argv, cwd=str(self.folder), env=self.env, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                         encoding="utf-8", errors="replace",
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            return "", f"{shown} couldn't start ({e.strerror or e})"
        runtime.spawn("sitejudge.dev", lambda: self.out.extend(ANSI.sub("", ln) for ln in self.proc.stdout))
        end, mine = time.time() + self.secs, f"http://127.0.0.1:{self.port}/"
        while time.time() < end:
            if self.checkpoint:
                self.checkpoint()
            if self.proc.poll() is not None:
                return "", f"{shown} stopped before the page opened: " + " ".join("".join(self.out).split())[-160:]
            m = ADDRESS.search("".join(self.out))
            url = m.group(0).replace("0.0.0.0", "127.0.0.1") if m else mine
            if (m or len(self.out) > 0) and _answers(url):
                return url, ""
            time.sleep(0.5)
        return "", f"{shown} didn't open a page within {self.secs} s"

    def stop(self):
        if self.proc and self.proc.poll() is None:
            from nova.actions import jobchecks
            jobchecks._kill(self.proc)


# ------------------------------------------------------------------ the browser run and the measurements

def dump(exe, url, secs, checkpoint=None):
    """Open url in the headless browser (own throwaway profile) -> the page's DOM after it ran, or None (too slow)."""
    from nova.actions import jobchecks
    with tempfile.TemporaryDirectory(prefix="nova-judge-", ignore_cleanup_errors=True) as prof:
        argv = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--disable-sync", "--mute-audio", f"--user-data-dir={prof}",
                "--window-size=1000,900", "--virtual-time-budget=5000", "--dump-dom", url]
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            end = time.time() + secs
            while proc.poll() is None:
                if checkpoint:
                    checkpoint()
                if time.time() > end:
                    jobchecks._kill(proc)
                    return None
                time.sleep(0.2)
            return proc.stdout.read()
        finally:
            if proc.poll() is None:
                jobchecks._kill(proc)


def result(dom):
    """The probe's measurements from the dumped DOM, or None."""
    m = RESULT.search(dom or "")
    if not m:
        return None
    try:
        r = json.loads(m.group(1).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))
    except ValueError:
        return None
    return r if isinstance(r, dict) else None


def links(base, hrefs):
    """Fetch the page's local links -> (bad ["about.html (404)"], checked, outside)."""
    bad, seen, outside = [], set(), 0
    here = urllib.parse.urlsplit(base)
    for href in hrefs or []:
        href = str(href or "").strip()
        if not href or SKIP_LINK.match(href):
            continue
        url = urllib.parse.urldefrag(urllib.parse.urljoin(base + "/", href))[0]
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https") or u.netloc != here.netloc:
            outside += 1
            continue
        if url in seen or len(seen) >= MAX_LINKS:
            continue
        seen.add(url)
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except (OSError, ValueError):
            code = "no answer"
        if code != 200 and not (isinstance(code, int) and code < 400):
            bad.append(f"{href} ({code})")
    return bad, len(seen), outside


def lighthouse(url, exe, conf, env=None, checkpoint=None):
    """Lighthouse performance / accessibility / SEO, when `lighthouse` is installed -> a check."""
    lh = shutil.which("lighthouse")
    if not lh:
        return _check("lighthouse", "skipped", "Lighthouse isn't installed - I don't install anything on my own")
    from nova.actions import jobchecks
    with tempfile.TemporaryDirectory(prefix="nova-lh-", ignore_cleanup_errors=True) as tmp:
        report = Path(tmp, "lh.json")
        argv = [lh, url, "--output=json", f"--output-path={report}", "--quiet",
                "--only-categories=performance,accessibility,seo",
                f"--chrome-flags=--headless=new --user-data-dir={Path(tmp, 'profile')}"]
        c = jobchecks.run_tests(tmp, argv, {**(env or os.environ), "CHROME_PATH": exe}, checkpoint,
                                conf["lighthouse_secs"], name="lighthouse")
        if c["state"] != "passed":
            return c if c["state"] == "skipped" else _check("lighthouse", "skipped", "Lighthouse didn't finish: "
                                                            + c["why"])
        try:
            cats = json.loads(report.read_text(encoding="utf-8")).get("categories") or {}
        except (OSError, ValueError, AttributeError):
            return _check("lighthouse", "skipped", "Lighthouse's report couldn't be read")
    return scores(cats, conf["lighthouse_min"])


def scores(cats, need):
    got = {(v or {}).get("title") or k: round(float((v or {}).get("score") or 0) * 100) for k, v in cats.items()}
    if not got:
        return _check("lighthouse", "skipped", "Lighthouse gave no scores")
    low = {k: v for k, v in got.items() if v < need}
    shown = ", ".join(f"{k} {v}" for k, v in got.items())
    if low:
        return _check("lighthouse", "failed", "Lighthouse scores below " + str(need) + ": "
                      + ", ".join(f"{k} {v}" for k, v in low.items()))
    return _check("lighthouse", "passed", shown)


def measure(srv, exe, conf, env=None, checkpoint=None, base=None):
    """Open the page through Nova's server and turn the probe's numbers into the checks."""
    width = conf["width"]
    dom = dump(exe, srv.url + FRAME_PATH, conf["page_secs"], checkpoint)
    if dom is None:
        return [_check("page", "failed", f"the page didn't finish loading within {conf['page_secs']} s")] + \
            _all("skipped", "the page didn't open", NAMES[1:])
    r = result(dom)
    if not r or r.get("noprobe") or r.get("blocked"):
        return [_check("page", "failed", "the page didn't open (nothing came back from it)")] + \
            _all("skipped", "the page didn't open", NAMES[1:])
    first = [m for m in srv.missing if m.split(" (")[0] in ("/", "/index.html")]
    if first:
        return [_check("page", "failed", f"the page didn't open ({first[0]})")] + \
            _all("skipped", "the page didn't open", NAMES[1:])
    title = str(r.get("title") or "").strip()
    checks = [_check("page", "passed", "it opens" + (f" ('{title[:60]}')" if title else ""))]
    errors = [e for e in r.get("errors") or [] if not (srv.upstream and NOISE.search(e))]
    errors += [f"failed to load {m}" for m in srv.missing
               if not any(m.split(" (")[0].lstrip("/") in e for e in errors)]
    checks.append(_check("console", "failed", f"{len(errors)} console error{'s' if len(errors) != 1 else ''}: "
                         + "; ".join(errors[:3]), "\n".join(errors)) if errors
                  else _check("console", "passed", "no console errors"))
    vw, sw = int(r.get("vw") or 0), int(r.get("sw") or 0)
    if abs(vw - width) > 2:
        checks.append(_check("mobile", "skipped", f"couldn't size the page to {width} px (it was {vw} px)"))
    elif sw > vw + 1:
        wide = str(r.get("wide") or "")
        checks.append(_check("mobile", "failed", f"mobile layout overflows at {width} px (the page is {sw} px wide"
                             + (f"; {wide} sticks out" if wide else "") + ")"))
    else:
        checks.append(_check("mobile", "passed", f"no sideways scroll at {width} px"))
    bad, n, outside = links(base or srv.url, r.get("links"))
    note = f"{outside} link{'s' if outside != 1 else ''} to other sites not checked" if outside else ""
    if bad:
        checks.append(_check("links", "failed", f"{len(bad)} broken link{'s' if len(bad) != 1 else ''}: "
                             + ", ".join(bad[:4])))
    elif n:
        checks.append(_check("links", "passed", f"{n} local link{'s work' if n != 1 else ' works'}" + (f"; {note}" if note else "")))
    else:
        checks.append(_check("links", "skipped", "no local links on the page" + (f" ({note})" if note else "")))
    checks.append(lighthouse(srv.url + "/", exe, conf, env, checkpoint))
    return checks


def site(folder, kind="", env=None, checkpoint=None, conf=None, built=""):
    """The website judge. Never raises except Stop (a crash -> every check skipped, with why)."""
    conf = conf or judges.limits()
    exe = browser()
    if not exe:
        return _all("skipped", "no headless browser (Chrome or Edge) on this laptop")
    way = how(folder, built)
    if way.get("why"):
        return [_check("page", "skipped" if kind not in ("new site",) else "failed", way["why"])] + \
            _all("skipped", "no page to open", NAMES[1:])
    dev = None
    try:
        if way.get("dev"):
            dev = Dev(folder, way["dev"], env, conf["start_secs"], checkpoint)
            url, why = dev.start()
            if not url:
                return [_check("page", "failed", why, "".join(dev.out)[-1500:])] + \
                    _all("skipped", "the page didn't open", NAMES[1:])
            with Server(upstream=url, width=conf["width"]) as srv:
                return measure(srv, exe, conf, env, checkpoint)
        with Server(root=way["serve"], spa=way["spa"], width=conf["width"]) as srv:
            return measure(srv, exe, conf, env, checkpoint)
    except Exception as e:
        if e.__class__.__name__ == "Stopped":
            raise
        log_exception("sitejudge: judging the website", e)
        return _all("skipped", f"the website judge crashed ({e.__class__.__name__})")
    finally:
        if dev:
            dev.stop()
