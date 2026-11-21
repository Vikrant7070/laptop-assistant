"""🔌 Connectors (MCP): config checks, a real stdio server process, an HTTP server with sessions + SSE, schemas,
approvals, crash recovery, timeouts and the tool picker."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from nova import protocol
from nova.actions import mcp
from nova.brain import cloud, toolpick
from nova.core import app

FAKE = str(Path(__file__).with_name("fake_mcp_server.py"))


@pytest.fixture(autouse=True)
def no_servers_left():
    yield
    mcp.stop_all()


def use(servers, wait=True):
    mcp.save_config({"mcpServers": servers})
    mcp.start(wait=wait)


def stdio(**extra):
    return {"command": sys.executable, "args": ["-I", FAKE], **extra}


def test_config_is_checked():
    assert mcp.check_config({"mcpServers": {"ok": {"command": "npx", "args": ["-y", "x"]}}}) == []
    assert mcp.check_config({"mcpServers": {"web": {"url": "https://example.com/mcp"}}}) == []
    bad = mcp.check_config({"mcpServers": {"a b": {"command": "x"}, "nothing": {}, "argsy": {"command": "x", "args": "-y"},
                                           "ftp": {"url": "ftp://x"}, "env": {"command": "x", "env": ["A=1"]}}})
    assert len(bad) == 5
    with pytest.raises(mcp.MCPError):
        mcp.save_config({"mcpServers": {"nothing": {}}})
    assert not mcp.config_file().exists()                 # a bad config is never saved


def test_stdio_server_connects_and_lists_every_page():
    use({"fake": stdio()})
    s = mcp.summary()["servers"][0]
    assert s["status"] == "connected", s["error"]
    assert s["tools"] == ["add", "shout", "crash", "slow", "fail"]          # two tools/list pages
    names = [t["name"] for t in mcp.tools_list()]
    assert names == ["mcp_fake_add", "mcp_fake_shout", "mcp_fake_crash", "mcp_fake_slow", "mcp_fake_fail"]
    add = mcp.tools_list()[0]
    sch = add["input_schema"]
    assert "$schema" not in sch and "$defs" not in sch
    assert sch["properties"]["b"] == {"type": "number", "description": "second"}      # $ref resolved
    assert sch["required"] == ["a", "b"] and sch["type"] == "object"
    crash = next(t for t in mcp.tools_list() if t["name"] == "mcp_fake_crash")
    assert crash["input_schema"]["properties"] == {} and crash["input_schema"]["required"] == []
    shout = next(t for t in mcp.tools_list() if t["name"] == "mcp_fake_shout")
    assert shout["description"] == "[fake connector] Make text loud. Second line."


def wrapped(server, text):
    """Connector answers reach the model marked as untrusted outside text (roadmap R2)."""
    return protocol.untrusted(f"connector {server}", text)


def test_calls_and_results():
    use({"fake": stdio()})
    assert mcp.call("mcp_fake_add", {"a": 2, "b": 3}) == wrapped("fake", "5")
    assert mcp.call("mcp_fake_shout", {"text": "hi"}) == wrapped("fake", "HI\n[image returned - I can't show it here]")
    assert mcp.call("mcp_fake_fail", {}) == "Error from the connector: nope"
    assert mcp.call("mcp_nobody_x", {}).startswith("Error in mcp_nobody_x")
    assert mcp.owns("mcp_fake_add") and not mcp.owns("open_app") and not mcp.owns("mcp_nobody_x")


def test_read_only_and_trusted_tools_need_no_ok():
    use({"fake": stdio()})
    assert mcp.is_safe("mcp_fake_add") and not mcp.is_safe("mcp_fake_shout")
    use({"fake": stdio(trusted=True)})
    assert mcp.is_safe("mcp_fake_shout")


def test_crashed_server_is_restarted_on_next_call():
    use({"fake": stdio()})
    out = mcp.call("mcp_fake_crash", {})
    assert out.startswith("Error in mcp_fake_crash") and "stopped" in out and "boom" in out
    assert mcp.call("mcp_fake_add", {"a": 1, "b": 1}) == wrapped("fake", "2")                   # started again by itself


def test_slow_tool_times_out(monkeypatch):
    use({"fake": stdio()})
    monkeypatch.setattr(mcp, "CALL_TIMEOUT", 1)
    out = mcp.call("mcp_fake_slow", {})
    assert "didn't answer within 1 s" in out


def test_missing_program_and_disabled_servers():
    use({"ghost": {"command": "no-such-program-xyz"}, "off": stdio(disabled=True)})
    got = {s["name"]: s for s in mcp.summary()["servers"]}
    assert got["ghost"]["status"] == "failed" and "Can't find the program" in got["ghost"]["error"]
    assert got["off"]["status"] == "disabled"
    assert mcp.tools_list() == []


def test_broken_json_file_is_reported(isolated):
    (isolated / "mcp.json").write_text("{oops", encoding="utf-8")
    mcp.start(wait=True)
    assert "isn't valid JSON" in mcp.summary()["error"]


# ------------------------------------------------------------------ HTTP ("streamable HTTP") server

class FakeHttpMCP:
    def __init__(self, need_auth=False):
        self.seen = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.seen.append((body.get("method"), dict(self.headers)))
                if need_auth and self.headers.get("Authorization") != "Bearer good":
                    self.send_response(401)
                    self.end_headers()
                    return
                if "id" not in body:                     # notification
                    self.send_response(202)
                    self.end_headers()
                    return
                m = body["method"]
                if m == "initialize":
                    return self.json({"jsonrpc": "2.0", "id": body["id"], "result": {
                        "protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "web"}}},
                        session="sess-42")
                assert self.headers.get("Mcp-Session-Id") == "sess-42"
                assert self.headers.get("MCP-Protocol-Version") == "2025-06-18"
                if m == "tools/list":
                    return self.sse([{"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [
                        {"name": "weather", "description": "Weather for a city",
                         "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}}}]}}])
                if m == "tools/call":
                    return self.sse([{"jsonrpc": "2.0", "method": "notifications/progress", "params": {}},
                                     {"jsonrpc": "2.0", "id": body["id"], "result": {
                                         "content": [{"type": "text", "text": f"Sunny in {body['params']['arguments']['city']}"}]}}])

            def json(self, obj, session=None):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if session:
                    self.send_header("Mcp-Session-Id", session)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def sse(self, msgs):
                data = "".join(f"event: message\ndata: {json.dumps(x)}\n\n" for x in msgs).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"


def test_http_server_with_session_and_sse():
    web = FakeHttpMCP()
    try:
        use({"web": {"url": web.url}})
        s = mcp.summary()["servers"][0]
        assert s["status"] == "connected", s["error"]
        assert mcp.call("mcp_web_weather", {"city": "Pune"}) == wrapped("web", "Sunny in Pune")
        assert "notifications/initialized" in [m for m, _ in web.seen]
    finally:
        web.server.shutdown()


def test_http_auth_problem_is_explained():
    web = FakeHttpMCP(need_auth=True)
    try:
        use({"web": {"url": web.url, "headers": {"Authorization": "Bearer bad"}}})
        s = mcp.summary()["servers"][0]
        assert s["status"] == "failed" and "HTTP 401" in s["error"]
        use({"web": {"url": web.url, "headers": {"Authorization": "Bearer good"}}})
        assert mcp.summary()["servers"][0]["status"] == "connected"
    finally:
        web.server.shutdown()


# ------------------------------------------------------------------ inside Nova

def test_brains_see_connector_tools():
    use({"fake": stdio()})
    names = [t["name"] for t in app.active_tools("cloud:google:gemini-x")]
    assert "mcp_fake_add" in names and "open_app" in names
    picked = toolpick.pick("add two numbers")
    assert "mcp_fake_add" in picked
    assert "mcp_fake_shout" in picked                        # a server's tools come as a group
    assert [t["name"] for t in toolpick.schemas(["mcp_fake_add"])] == ["mcp_fake_add"]
    g = cloud._schema(next(t for t in mcp.tools_list() if t["name"] == "mcp_fake_add"), "google")
    assert "additionalProperties" not in g and g["properties"]


def test_connector_approvals(brain):
    use({"fake": stdio()})
    app.save_settings({**app.settings(), "autonomy": "careful"})
    fake, _ = brain([("", [("mcp_fake_add", {"a": 2, "b": 2})]), ("It's 4.", []),
                     ("", [("mcp_fake_shout", {"text": "hey"})]), ("Shouted.", [])])
    out, _ = app.answer("add 2 and 2")
    assert not out.get("pending") and out["reply"] == "It's 4."           # read-only: no question
    assert "4" in json.dumps(fake.seen[1][-1])                              # the real server's answer reached her
    out, _ = app.answer("shout hey")
    assert out["pending"][0]["label"] == "Use fake: shout"                 # anything else asks first
    out, _ = app.confirm_pending(True)
    assert out["reply"] == "Shouted." and "HEY" in json.dumps(fake.seen[-1][-1])


def test_full_access_runs_connectors_but_phone_still_asks(brain):
    use({"fake": stdio()})
    app.save_settings({**app.settings(), "autonomy": "full"})
    brain([("ok", [("mcp_fake_shout", {"text": "a"})]), ("Done.", []),
           ("ok", [("mcp_fake_shout", {"text": "b"})]), ("Done.", [])])
    out, _ = app.answer("shout a")
    assert not out.get("pending")
    out, _ = app.answer("shout b", quiet=True, remote=True)
    assert out.get("pending")


def test_mcp_api(client):
    r = client.post("/api/mcp", json={"config": "{nope"})
    assert r.status_code == 400 and "valid JSON" in r.get_json()["error"]
    r = client.post("/api/mcp", json={"config": json.dumps({"fake": stdio()})})        # bare servers object works too
    j = r.get_json()
    assert r.status_code == 200 and j["servers"][0]["status"] == "connected"
    assert json.loads(j["config"])["mcpServers"]["fake"]["command"] == sys.executable
    r = client.get("/api/mcp")
    assert "fake" in r.get_json()["config"]
