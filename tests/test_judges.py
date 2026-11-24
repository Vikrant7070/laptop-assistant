"""Manager plan Phase 5: judges for workers' results - the website judge (page, console, phone width, links,
Lighthouse), lint / build, and the research judge. Each has a pass, a fail and a skipped-with-why path.

Hermetic: the headless browser is faked (`sitejudge.dump`), but Nova's own little server, the probe injection, the
link checks, the dev-server start and ruff run for real on tiny fixture projects in a temp folder.
"""
import json
import shutil
import sys
import urllib.request

import pytest

from nova.actions import jobchecks, judges, sitejudge, workers
from tests.test_workers import fake_worker, git, repo, run_job  # noqa: F401  (fixtures)

CONF = dict(judges.DEFAULTS)
WIDE = '<!doctype html><html><head><title>Wide</title></head><body><div class="hero">hi</div>' \
       '<a href="about.html">About</a><a href="https://example.com/">out</a></body></html>'


def _site(folder, html=WIDE, about=True):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(html, encoding="utf-8")
    if about:
        (folder / "about.html").write_text("<p>about</p>", encoding="utf-8")
    return folder


@pytest.fixture
def browser(monkeypatch):
    """A fake headless browser: it fetches the page through Nova's server (so the probe injection and the server's
    404 notes are real) and returns what the probe would have measured. Set browser.measured per test."""
    class B:
        measured, pages = {}, []

    def dump(exe, url, secs, checkpoint=None):
        base = url.rsplit("/", 1)[0]
        B.pages.append(urllib.request.urlopen(base + "/", timeout=10).read().decode("utf-8"))
        for asset in B.measured.get("assets", []):
            try:
                urllib.request.urlopen(base + "/" + asset, timeout=10)
            except OSError:
                pass  # intentional: a missing asset is what the test wants the server to note
        r = {"errors": [], "vw": 375, "sw": 375, "wide": "", "links": [], "title": "T", **B.measured}
        r.pop("assets", None)
        return '<html><body><script type="application/json" id="__nova_judge">' + json.dumps(r) + "</script></body></html>"

    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: None if n == "lighthouse" else real(n, *a, **k))
    monkeypatch.setattr(sitejudge, "browser", lambda: "chrome.exe")
    monkeypatch.setattr(sitejudge, "dump", dump)
    return B


def _states(checks):
    return {c["name"]: c["state"] for c in checks}


def _why(checks, name):
    return next(c["why"] for c in checks if c["name"] == name)


# ------------------------------------------------------------------ the website judge

def test_a_good_site_passes(tmp_path, browser):
    browser.measured = {"links": ["about.html", "#top", "mailto:a@b.c", "https://example.com/"]}
    c = sitejudge.site(_site(tmp_path / "web"), "new site", conf=CONF)
    assert _states(c) == {"page": "passed", "console": "passed", "mobile": "passed", "links": "passed",
                          "lighthouse": "skipped"}
    assert _why(c, "mobile") == "no sideways scroll at 375 px"
    assert _why(c, "links") == "1 local link works; 1 link to other sites not checked"
    assert "Lighthouse isn't installed" in _why(c, "lighthouse")
    assert "window.__novaJudge" in browser.pages[0] and browser.pages[0].index("__novaJudge") < browser.pages[0].index("<body")


def test_a_site_that_overflows_at_375_px_fails_with_that_sentence(tmp_path, browser):
    browser.measured = {"sw": 528, "wide": "div.hero", "errors": ["boom"], "links": ["about.html", "gone.html"],
                        "assets": ["missing.png"]}
    c = sitejudge.site(_site(tmp_path / "web"), "new site", conf=CONF)
    assert _states(c)["mobile"] == "failed"
    assert _why(c, "mobile") == "mobile layout overflows at 375 px (the page is 528 px wide; div.hero sticks out)"
    assert _states(c)["console"] == "failed" and "boom" in _why(c, "console") and "missing.png (404)" in _why(c, "console")
    assert _why(c, "links") == "1 broken link: gone.html (404)"
    assert jobchecks.verdict(c) == "failed"


def test_the_website_judge_skips_with_why(tmp_path, browser, monkeypatch):
    # no index.html, no dev script: nothing to open (a "new site" with no page is a failure, a "new app" is skipped)
    empty = tmp_path / "empty"
    empty.mkdir()
    app = sitejudge.site(empty, "new app", conf=CONF)
    assert app[0] == {"name": "page", "state": "skipped", "why": "no page to open (no index.html, no dev or start script)"}
    assert all(c["state"] == "skipped" for c in app)
    assert sitejudge.site(empty, "new site", conf=CONF)[0]["state"] == "failed"
    # a page with no local links: links skipped, with why
    browser.measured = {}
    c = sitejudge.site(_site(tmp_path / "web", about=False), "", conf=CONF)
    assert _why(c, "links") == "no local links on the page"
    # a page that never finishes loading
    monkeypatch.setattr(sitejudge, "dump", lambda *a, **k: None)
    c = sitejudge.site(tmp_path / "web", "", conf=CONF)
    assert c[0]["state"] == "failed" and "didn't finish loading within 40 s" in c[0]["why"]
    assert [x["state"] for x in c[1:]] == ["skipped"] * 4
    # no headless browser on the laptop: every check skipped, never passed
    monkeypatch.setattr(sitejudge, "browser", lambda: "")
    c = sitejudge.site(tmp_path / "web", "new site", conf=CONF)
    assert [x["name"] for x in c] == list(sitejudge.NAMES)
    assert {x["why"] for x in c} == {"no headless browser (Chrome or Edge) on this laptop"}
    assert jobchecks.verdict(c) == "not checked"


def test_a_page_that_can_t_be_sized_to_phone_width_is_skipped(tmp_path, browser):
    browser.measured = {"vw": 504, "sw": 504}
    c = sitejudge.site(_site(tmp_path / "web"), "", conf=CONF)
    assert _states(c)["mobile"] == "skipped" and "couldn't size the page to 375 px" in _why(c, "mobile")


def test_lighthouse_scores_against_the_threshold(tmp_path, browser, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: "C:/n/lighthouse.cmd" if n == "lighthouse" else None)
    scores = {"performance": 0.95, "accessibility": 0.62, "seo": 0.9}

    def run(cwd, argv, env=None, checkpoint=None, secs=None, name="tests"):
        out = next(a.split("=", 1)[1] for a in argv if a.startswith("--output-path="))
        cats = {k: {"title": k.upper() if k == "seo" else k.title(), "score": v} for k, v in scores.items()}
        open(out, "w", encoding="utf-8").write(json.dumps({"categories": cats}))
        assert env["CHROME_PATH"] == "chrome.exe" and name == "lighthouse" and secs == CONF["lighthouse_secs"]
        return {"name": name, "state": "passed", "why": "done"}
    monkeypatch.setattr(jobchecks, "run_tests", run)
    c = sitejudge.site(_site(tmp_path / "web"), "", conf=CONF)
    assert _states(c)["lighthouse"] == "failed" and _why(c, "lighthouse") == "Lighthouse scores below 80: Accessibility 62"
    scores["accessibility"] = 0.85
    c = sitejudge.site(tmp_path / "web", "", conf=CONF)
    assert _states(c)["lighthouse"] == "passed" and "Performance 95" in _why(c, "lighthouse")


DEV = r'''
import http.server, os, sys
os.chdir(sys.argv[1])
s = http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), http.server.SimpleHTTPRequestHandler)
print("  Local:   http://127.0.0.1:%s/" % os.environ["PORT"], flush=True)
s.serve_forever()
'''


def test_a_dev_script_is_started_and_the_page_judged_through_nova_s_proxy(tmp_path, browser, monkeypatch):
    web = _site(tmp_path / "app")
    (web / "package.json").write_text('{"scripts": {"dev": "vite"}}', encoding="utf-8")
    (tmp_path / "dev.py").write_text(DEV, encoding="utf-8")
    assert "serve" in sitejudge.how(web)              # no node_modules: its index.html is served instead
    monkeypatch.setattr(judges, "npm", lambda folder, script: ([sys.executable, str(tmp_path / "dev.py"), str(web)], ""))
    browser.measured = {"links": ["about.html", "nope.html"]}
    c = sitejudge.site(web, "new app", conf=CONF)
    assert _states(c)["page"] == "passed" and "__novaJudge" in browser.pages[0]      # injected through the proxy
    assert _why(c, "links") == "1 broken link: nope.html (404)"
    # a dev script that dies before the page opens: failed, with its output
    (tmp_path / "dies.py").write_text("print('Error: Cannot find module vite')\nraise SystemExit(1)\n", encoding="utf-8")
    monkeypatch.setattr(judges, "npm", lambda folder, script: ([sys.executable, str(tmp_path / "dies.py")], ""))
    c = sitejudge.site(web, "new app", conf=CONF)
    assert c[0]["state"] == "failed" and "stopped before the page opened" in c[0]["why"] and "vite" in c[0]["why"]


def test_how_the_page_is_opened(tmp_path, monkeypatch):
    web = _site(tmp_path / "s")
    assert sitejudge.how(web) == {"serve": str(web), "spa": False}
    app = tmp_path / "a"
    app.mkdir()
    (app / "package.json").write_text('{"scripts": {"dev": "vite", "build": "vite build"}}', encoding="utf-8")
    assert "no node_modules" in sitejudge.how(app)["why"] or "isn't installed" in sitejudge.how(app)["why"]
    (app / "dist").mkdir()
    (app / "dist" / "index.html").write_text("<p>x</p>", encoding="utf-8")
    assert sitejudge.how(app, built=str(app / "dist")) == {"serve": str(app / "dist"), "spa": True}
    assert sitejudge.wanted(app) and sitejudge.wanted(tmp_path / "nothing", "new site")
    assert not sitejudge.wanted(tmp_path / "nothing", "small fix")


def test_nova_s_server_injects_the_probe_and_stays_in_its_folder(tmp_path):
    web = _site(tmp_path / "web")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    with sitejudge.Server(root=str(web), width=375) as srv:
        assert "width:375px" in urllib.request.urlopen(srv.url + sitejudge.FRAME_PATH).read().decode()
        assert b"__novaJudge" in urllib.request.urlopen(srv.url + "/about.html").read()
        for bad, code in (("/../secret.txt", (403, 404)), ("/gone.css", (404,))):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(srv.url + bad)
            assert e.value.code in code
        assert "/gone.css (404)" in srv.missing
    assert sitejudge.inject("<p>x</p>").startswith("<script>")
    assert sitejudge.inject("<html><head><title>t</title></head>").startswith("<html><head><script>")


# ------------------------------------------------------------------ the code judge: lint and build

def test_lint_passes_fails_or_is_skipped(tmp_path, monkeypatch):
    if not shutil.which("ruff"):
        pytest.skip("ruff isn't installed")
    p = tmp_path / "p"
    p.mkdir()
    (p / "ruff.toml").write_text("line-length = 120\n", encoding="utf-8")
    (p / "ok.py").write_text("print('hi')\n", encoding="utf-8")
    checks, built = judges.code(p, conf=CONF)
    assert checks == [checks[0]] and checks[0]["name"] == "lint" and checks[0]["state"] == "passed" and built == ""
    (p / "bad.py").write_text("import os\n", encoding="utf-8")
    lint = judges.code(p, conf=CONF)[0][0]
    assert lint["state"] == "failed" and "ruff" in lint["why"] and "F401" in lint["detail"]
    none = tmp_path / "none"
    none.mkdir()
    assert judges.code(none, conf=CONF)[0] == [{"name": "lint", "state": "skipped", "why": "the project has no lint set up"}]
    flake = tmp_path / "f"
    flake.mkdir()
    (flake / ".flake8").write_text("[flake8]\n", encoding="utf-8")
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: None if n == "flake8" else real(n, *a, **k))
    assert judges.code(flake, conf=CONF)[0][0]["why"] == "flake8 is set up but isn't installed"


BUILD_OK = "import os\nos.makedirs('dist', exist_ok=True)\nopen('dist/index.html', 'w').write('<p>built</p>')\n"


def test_build_passes_fails_or_is_skipped(tmp_path, monkeypatch):
    p = tmp_path / "app"
    p.mkdir()
    (p / "package.json").write_text('{"scripts": {"build": "vite build"}}', encoding="utf-8")
    checks, built = judges.code(p, conf=CONF)                     # no npm / no node_modules here: skipped
    assert checks[1]["name"] == "build" and checks[1]["state"] == "skipped" and built == ""
    (p / "build.py").write_text(BUILD_OK, encoding="utf-8")
    monkeypatch.setattr(judges, "npm", lambda folder, script: ([sys.executable, "build.py"], ""))
    checks, built = judges.code(p, conf=CONF)
    assert checks[1]["state"] == "passed" and built == str(p / "dist")
    (p / "build.py").write_text("print('error TS2304: Cannot find name foo')\nraise SystemExit(2)\n", encoding="utf-8")
    checks, built = judges.code(p, conf=CONF)
    assert checks[1]["state"] == "failed" and "TS2304" in checks[1]["why"] and built == ""


def test_settings_are_clamped_and_saved_from_the_workers_page(tmp_path, monkeypatch):
    s = judges.limits({"judges": {"page_secs": 9999, "lighthouse_min": -5, "backed_share": "x", "width": 375.4}})
    assert s["page_secs"] == 180 and s["lighthouse_min"] == 0 and s["backed_share"] == 0.5 and s["width"] == 375
    assert judges.limits({}) == judges.DEFAULTS
    workers.save_settings({"judges_page_secs": "5", "judges_lighthouse_min": "90"})
    assert workers.config()["judges"]["page_secs"] == 10 and workers.config()["judges"]["lighthouse_min"] == 90
    workers.save_settings({"research_max_sources": 2})              # another save keeps the judges' settings
    assert workers.config()["judges"]["lighthouse_min"] == 90
    with pytest.raises(ValueError):
        workers.save_settings({"judges_page_secs": "soon"})


# ------------------------------------------------------------------ the research judge

GOOD = """Research: tea
24 Nov 2026

Headline: Green tea has some caffeine.

1. Green tea has less caffeine than coffee [1][2].
2. It has antioxidants [2][3].
3. Brewing hotter makes it bitter [3].
Disagreements: none found

Sources:
[1] A
    https://www.a.com/tea
[2] B
    https://b.org/x
[3] C
    https://c.net/y
"""


def test_research_judge_pass_fail_and_skipped():
    c = judges.research({}, GOOD, CONF)
    assert _states(c) == {"citations": "passed", "independent": "passed", "backed": "passed", "disagreements": "passed"}
    assert _why(c, "backed") == "2 of 3 findings are backed by 2+ different sites"
    # one site only, a finding without a source, a citation that isn't listed, no disagreements line
    bad = (GOOD.replace("https://b.org/x", "https://a.com/x").replace("https://c.net/y", "https://a.com/y")
           .replace("Disagreements: none found\n", "").replace("bitter [3]", "bitter"))
    c = judges.research({}, bad, CONF)
    assert _why(c, "citations") == '1 of 3 findings cite no source ("Brewing hotter makes it bitter.")'
    assert _why(c, "independent") == "every cited source is from a.com - it needs 2 or more different sites"
    assert _states(c)["backed"] == "failed" and "0 of 3 findings" in _why(c, "backed")
    assert _states(c)["disagreements"] == "skipped"                # with one site there's nothing to disagree
    c = judges.research({}, GOOD.replace("Disagreements: none found\n", "").replace("[2][3]", "[7]"), CONF)
    assert _why(c, "citations") == "cites [7], but only 3 sources are listed"
    assert _states(c)["disagreements"] == "failed" and _states(c)["backed"] == "failed"
    gone = judges.research({"path": "C:/no/such/report.txt"}, conf=CONF)
    assert {x["state"] for x in gone} == {"skipped"} and gone[0]["why"] == "the report file can't be read"


# ------------------------------------------------------------------ the whole way: a worker's site, sent back

def test_a_site_that_overflows_goes_back_to_the_worker_with_that_sentence(repo, fake_worker, browser, monkeypatch):  # noqa: F811
    _site(repo)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "site")
    briefs, real = [], workers.command
    monkeypatch.setattr(workers, "command", lambda w, brief, model, conf, title="": (briefs.append(brief), real(
        w, brief, model, conf, title))[1])
    browser.measured = {"sw": 512, "wide": "div.hero", "links": ["about.html"]}
    job, _ = run_job(repo, meta={"job_type": "new site"})
    assert len(briefs) == 2                                         # sent back once, still overflowing
    assert "- mobile: mobile layout overflows at 375 px (the page is 512 px wide; div.hero sticks out)" in briefs[1]
    rec = workers.load_jobs()[-1]
    assert rec["verdict"] == "failed" and rec["job_type"] == "new site"
    assert "mobile: mobile layout overflows at 375 px" in job["result"] and "still fails - over to you" in job["result"]
