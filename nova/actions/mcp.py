"""🔌 Connectors: Nova uses Model Context Protocol (MCP) servers as extra tools.

An MCP server is a small program (or web address) that offers tools, e.g. GitHub, a calendar, Spotify, a database.
The list lives in data/mcp.json, in the same shape Claude Desktop and other apps use, so a server's README snippet can
be pasted as it is:

    {"mcpServers": {
        "files":  {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/me/Documents"]},
        "github": {"url": "https://api.githubcopilot.com/mcp/", "headers": {"Authorization": "Bearer ..."}},
        "notes":  {"command": "python", "args": ["notes_server.py"], "trusted": true, "disabled": false}}}

Each server's tools become Nova tools named mcp_<server>_<tool>. Safety: a connector tool asks first (Allow / No)
unless the server marks it read-only (annotations.readOnlyHint), the server is listed with "trusted": true, or the
autonomy setting is Full access. From the phone it always asks unless read-only.

No extra package: the protocol is JSON-RPC 2.0, over a program's stdin/stdout ("stdio") or HTTP POST ("streamable
HTTP", answered as JSON or as server-sent events).
"""
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request

from nova.actions import tools

PROTOCOL = "2025-06-18"
CLIENT = {"name": "nova", "version": "1.0"}
CALL_TIMEOUT = 60        # one tool call
START_TIMEOUT = 40       # start-up + initialize + tools/list (npx may download the server the first time)
MAX_RESULT = 6000
MAX_TOOLS_PER_SERVER = 40
NAME_OK = re.compile(r"[^a-zA-Z0-9_]")


class MCPError(Exception):
    pass


def config_file():
    return tools.DATA / "mcp.json"


def load_config():
    try:
        d = json.loads(config_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"mcpServers": {}}
    except ValueError as e:
        raise MCPError(f"data/mcp.json isn't valid JSON: {e}") from e
    return d if isinstance(d, dict) else {"mcpServers": {}}


def check_config(d):
    """Problems with a config the user typed, as one readable line each (empty = fine)."""
    if not isinstance(d, dict) or not isinstance(d.get("mcpServers", {}), dict):
        return ['It must look like {"mcpServers": {"name": {"command": "...", "args": [...]}}}.']
    probs = []
    for name, c in d.get("mcpServers", {}).items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,30}", name):
            probs.append(f"'{name}': use a short name with only letters, numbers, - and _.")
        elif not isinstance(c, dict):
            probs.append(f"'{name}': must be an object.")
        elif not (isinstance(c.get("command"), str) and c["command"].strip()) and not (
                isinstance(c.get("url"), str) and re.match(r"https?://", c["url"])):
            probs.append(f"'{name}': needs a \"command\" (a program to run) or a \"url\" (http/https).")
        elif "args" in c and not (isinstance(c["args"], list) and all(isinstance(a, str) for a in c["args"])):
            probs.append(f"'{name}': \"args\" must be a list of text.")
        elif any(k in c and not isinstance(c[k], dict) for k in ("env", "headers")):
            probs.append(f"'{name}': \"env\" and \"headers\" must be objects.")
    return probs


def save_config(d):
    probs = check_config(d)
    if probs:
        raise MCPError(" ".join(probs))
    f = config_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    tmp.replace(f)


# ------------------------------------------------------------------ JSON schemas from servers -> what brains accept

def _clean_schema(sch, defs=None, depth=0):
    """Make a server's inputSchema safe for every brain: no $schema/$id, local $refs inlined, always an object."""
    if not isinstance(sch, dict) or depth > 8:
        return {}
    defs = defs if defs is not None else {**sch.get("$defs", {}), **sch.get("definitions", {})}
    if "$ref" in sch and isinstance(sch["$ref"], str):
        ref = sch["$ref"].split("/")[-1]
        return _clean_schema(defs.get(ref, {}), defs, depth + 1)
    out = {}
    for k, v in sch.items():
        if k in ("$schema", "$id", "$defs", "definitions", "$comment"):
            continue
        if isinstance(v, dict):
            out[k] = ({pk: _clean_schema(pv, defs, depth + 1) for pk, pv in v.items()} if k == "properties"
                      else _clean_schema(v, defs, depth + 1))
        elif isinstance(v, list) and k in ("anyOf", "oneOf", "allOf", "prefixItems"):
            out[k] = [_clean_schema(x, defs, depth + 1) for x in v]
        else:
            out[k] = v
    return out


def tool_schema(server, t):
    sch = _clean_schema(t.get("inputSchema") or {})
    sch["type"] = "object"
    if not isinstance(sch.get("properties"), dict):
        sch["properties"] = {}
    if not isinstance(sch.get("required"), list):
        sch["required"] = []
    desc = (t.get("description") or t.get("title") or t["name"]).strip().replace("\n", " ")
    return {"name": tool_name(server, t["name"]),
            "description": f"[{server} connector] {desc[:500]}",
            "input_schema": sch}


def tool_name(server, tool):
    n = f"mcp_{NAME_OK.sub('_', server)}_{NAME_OK.sub('_', tool)}"
    return n[:64]


def result_text(res):
    """A tools/call result as text for the brain."""
    parts = []
    for c in (res or {}).get("content") or []:
        kind = c.get("type")
        if kind == "text":
            parts.append(str(c.get("text", "")))
        elif kind == "resource":
            r = c.get("resource") or {}
            parts.append(str(r.get("text") or f"[file {r.get('uri', '')}]"))
        elif kind == "resource_link":
            parts.append(f"[link: {c.get('name') or ''} {c.get('uri', '')}]")
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} returned - I can't show it here]")
    if not parts and (res or {}).get("structuredContent") is not None:
        parts.append(json.dumps(res["structuredContent"], ensure_ascii=False))
    text = "\n".join(p for p in parts if p).strip() or "(the connector returned nothing)"
    if (res or {}).get("isError"):
        text = "Error from the connector: " + text
    return text[:MAX_RESULT] + (" …(cut)" if len(text) > MAX_RESULT else "")


# ------------------------------------------------------------------ transports

class _Stdio:
    """A server we start ourselves; newline-delimited JSON-RPC on its stdin/stdout. Never shows a console window."""

    def __init__(self, name, cfg):
        self.name, self.cfg = name, cfg
        self.proc = None
        self.waiting = {}            # id -> [Event, response]
        self.lock = threading.Lock()
        self.err_tail = []
        self.dead = False             # its stdout ended: the program has stopped (poll() may not know yet)

    def open(self):
        cmd = [self.cfg["command"], *self.cfg.get("args", [])]
        env = {**os.environ, **{k: str(v) for k, v in (self.cfg.get("env") or {}).items()}}
        exe = cmd[0]
        if os.name == "nt" and not os.path.splitext(exe)[1]:
            import shutil
            found = shutil.which(exe)    # npx / uvx are .cmd files: Popen needs the full name
            if not found:
                raise MCPError(f"Can't find the program '{exe}'. Is it installed (and on PATH)?")
            cmd[0] = found
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         env=env, cwd=self.cfg.get("cwd") or None,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            raise MCPError(f"Couldn't start '{exe}': {e}") from e
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read(self):
        for raw in self.proc.stdout:
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue                  # a server printing logs on stdout: skip the line
            self._got(msg)
        self.dead = True
        for w in list(self.waiting.values()):
            w[0].set()                    # the server ended: wake everyone waiting

    def _read_err(self):
        for raw in self.proc.stderr:
            self.err_tail = (self.err_tail + [raw.decode("utf-8", "replace").strip()])[-5:]

    def _got(self, msg):
        if "method" in msg and "id" in msg:         # the server asks us something (ping, roots/list...)
            self._answer_server(msg)
        elif "id" in msg and msg["id"] in self.waiting:
            w = self.waiting[msg["id"]]
            w[1] = msg
            w[0].set()

    def _answer_server(self, msg):
        if msg["method"] == "ping":
            self._write({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        elif msg["method"] == "roots/list":
            self._write({"jsonrpc": "2.0", "id": msg["id"], "result": {"roots": []}})
        else:
            self._write({"jsonrpc": "2.0", "id": msg["id"],
                         "error": {"code": -32601, "message": "Nova doesn't support that."}})

    def _write(self, msg):
        with self.lock:
            self.proc.stdin.write((json.dumps(msg) + "\n").encode())
            self.proc.stdin.flush()

    def alive(self):
        return bool(self.proc and self.proc.poll() is None and not self.dead)

    def request(self, rid, msg, timeout):
        if not self.alive():
            raise MCPError(self._died())
        w = self.waiting[rid] = [threading.Event(), None]
        try:
            self._write(msg)
            if not w[0].wait(timeout):
                raise MCPError(f"The {self.name} connector didn't answer within {timeout} s.")
            if w[1] is None:
                raise MCPError(self._died())
            return w[1]
        except (BrokenPipeError, OSError) as e:
            raise MCPError(self._died()) from e
        finally:
            self.waiting.pop(rid, None)

    def notify(self, msg):
        if self.alive():
            try:
                self._write(msg)
            except OSError:
                pass

    def _died(self):
        tail = " | ".join(t for t in self.err_tail if t)[-300:]
        return f"The {self.name} connector stopped." + (f" It said: {tail}" if tail else "")

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(3)
            except Exception:
                pass
            if self.proc.poll() is None:
                self.proc.kill()


class _Http:
    """A server at a URL ("streamable HTTP"): every message is a POST; the answer is JSON or an SSE stream."""

    def __init__(self, name, cfg):
        self.name, self.cfg = name, cfg
        self.session = None
        self.version = None

    def open(self):
        pass

    def alive(self):
        return True

    def _post(self, msg, timeout):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   **{k: str(v) for k, v in (self.cfg.get("headers") or {}).items()}}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.version:
            headers["MCP-Protocol-Version"] = self.version
        req = urllib.request.Request(self.cfg["url"], data=json.dumps(msg).encode(), headers=headers, method="POST")
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise MCPError(f"The {self.name} connector refused Nova (HTTP {e.code}) - check its key/token in "
                               "\"headers\".") from e
            raise MCPError(f"The {self.name} connector answered HTTP {e.code}.") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MCPError(f"Can't reach the {self.name} connector at {self.cfg['url']}.") from e
        return r

    def request(self, rid, msg, timeout):
        with self._post(msg, timeout) as r:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self.session = sid
            kind = (r.headers.get("Content-Type") or "").lower()
            if "text/event-stream" in kind:
                data = []
                for raw in r:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        try:
                            got = json.loads("\n".join(data))
                        except ValueError:
                            got = {}
                        data = []
                        if got.get("id") == rid and ("result" in got or "error" in got):
                            return got
                raise MCPError(f"The {self.name} connector closed the stream without an answer.")
            body = r.read()
        try:
            got = json.loads(body)
        except ValueError as e:
            raise MCPError(f"The {self.name} connector sent something that isn't JSON.") from e
        if isinstance(got, list):
            got = next((g for g in got if g.get("id") == rid), {})
        return got

    def notify(self, msg):
        try:
            self._post(msg, 10).close()
        except MCPError:
            pass

    def close(self):
        pass


# ------------------------------------------------------------------ one server

class Server:
    def __init__(self, name, cfg):
        self.name, self.cfg = name, cfg
        self.tools = []               # Nova-format schemas
        self.raw = {}                 # nova tool name -> MCP tool dict
        self.status, self.error = "starting", ""
        self.info = {}
        self._id = 0
        self._id_lock = threading.Lock()
        self.link = _Http(name, cfg) if cfg.get("url") else _Stdio(name, cfg)

    def _rid(self):
        with self._id_lock:
            self._id += 1
            return self._id

    def rpc(self, method, params=None, timeout=None):
        timeout = timeout or CALL_TIMEOUT
        rid = self._rid()
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        got = self.link.request(rid, msg, timeout)
        if got.get("error"):
            e = got["error"]
            raise MCPError(f"{self.name}: {e.get('message', 'error')}"[:300])
        return got.get("result") or {}

    def connect(self):
        try:
            self.link.open()
            init = self.rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": CLIENT},
                            timeout=START_TIMEOUT)
            self.info = init.get("serverInfo") or {}
            if isinstance(self.link, _Http):
                self.link.version = init.get("protocolVersion") or PROTOCOL
            self.link.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
            found, cursor = [], None
            for _ in range(10):                                   # tools/list can come in pages
                page = self.rpc("tools/list", {"cursor": cursor} if cursor else {}, timeout=START_TIMEOUT)
                found += page.get("tools") or []
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            found = [t for t in found if isinstance(t, dict) and t.get("name")][:MAX_TOOLS_PER_SERVER]
            self.raw = {tool_name(self.name, t["name"]): t for t in found}
            self.tools = [tool_schema(self.name, t) for t in found]
            self.status, self.error = "connected", ""
        except MCPError as e:
            self.status, self.error = "failed", str(e)
            self.link.close()
        except Exception as e:
            self.status, self.error = "failed", f"{type(e).__name__}: {e}"
            self.link.close()
        return self

    def call(self, nova_name, args):
        t = self.raw[nova_name]
        if not self.link.alive():                 # it crashed: try once to start it again
            self.link = _Http(self.name, self.cfg) if self.cfg.get("url") else _Stdio(self.name, self.cfg)
            self.connect()
            if self.status != "connected":
                raise MCPError(self.error)
        return result_text(self.rpc("tools/call", {"name": t["name"], "arguments": args or {}}))

    def read_only(self, nova_name):
        ann = (self.raw.get(nova_name) or {}).get("annotations") or {}
        return ann.get("readOnlyHint") is True

    def close(self):
        self.link.close()
        self.status = "stopped"


# ------------------------------------------------------------------ all servers

_servers = {}            # name -> Server
_by_tool = {}            # nova tool name -> Server
_lock = threading.RLock()
state = {"loaded_at": 0.0, "error": ""}


def start(wait=False):
    """(Re)connect every enabled server in data/mcp.json, in the background. wait=True: until all have answered."""
    stop_all()
    try:
        cfg = load_config()
        state["error"] = ""
    except MCPError as e:
        state["error"] = str(e)
        return []
    threads = []
    with _lock:
        for name, c in (cfg.get("mcpServers") or {}).items():
            if not isinstance(c, dict) or c.get("disabled") or check_config({"mcpServers": {name: c}}):
                continue
            srv = _servers[name] = Server(name, c)
            th = threading.Thread(target=_connect, args=(srv,), daemon=True, name=f"mcp-{name}")
            th.start()
            threads.append(th)
    state["loaded_at"] = time.time()
    if wait:
        for th in threads:
            th.join(START_TIMEOUT + 5)
    return list(_servers)


def _connect(srv):
    srv.connect()
    with _lock:
        if _servers.get(srv.name) is srv and srv.status == "connected":
            for n in srv.raw:
                _by_tool[n] = srv


def stop_all():
    with _lock:
        for s in _servers.values():
            s.close()
        _servers.clear()
        _by_tool.clear()


def tools_list():
    """Nova-format schemas of every connected connector tool."""
    with _lock:
        return [t for s in _servers.values() if s.status == "connected" for t in s.tools]


def groups():
    """Each server's tools, so the local tool picker brings a server's other tools along with its best match."""
    with _lock:
        return [set(s.raw) for s in _servers.values() if s.status == "connected" and len(s.raw) > 1]


def owns(name):
    return str(name).startswith("mcp_") and name in _by_tool


def server_of(name):
    s = _by_tool.get(name)
    return s.name if s else ""


def is_safe(name):
    """No need to ask first: the server says the tool only reads, or the user marked the server trusted."""
    s = _by_tool.get(name)
    return bool(s and (s.cfg.get("trusted") is True or s.read_only(name)))


def label(name):
    s = _by_tool.get(name)
    if not s:
        return name
    return f"Use {s.name}: {s.raw[name].get('title') or s.raw[name]['name']}"


def call(name, args):
    s = _by_tool.get(name)
    if not s:
        return f"Error in {name}: that connector isn't connected any more."
    try:
        return s.call(name, args if isinstance(args, dict) else {})
    except MCPError as e:
        return f"Error in {name}: {e}"
    except Exception as e:
        return f"Error in {name}: {type(e).__name__}: {e}"


def summary():
    """For the ⚙ settings page."""
    with _lock:
        out = [{"name": s.name, "status": s.status, "error": s.error, "tools": [t["name"] for t in s.raw.values()],
                "server": s.info.get("name", ""), "kind": "url" if s.cfg.get("url") else "program",
                "trusted": s.cfg.get("trusted") is True} for s in _servers.values()]
    try:
        cfg = load_config()
    except MCPError:
        cfg = {"mcpServers": {}}
    for name, c in (cfg.get("mcpServers") or {}).items():
        if isinstance(c, dict) and c.get("disabled") and all(o["name"] != name for o in out):
            out.append({"name": name, "status": "disabled", "error": "", "tools": [], "server": "",
                        "kind": "url" if c.get("url") else "program", "trusted": c.get("trusted") is True})
    return {"servers": out, "error": state["error"]}
