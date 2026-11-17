"""A tiny MCP server for the tests: newline-delimited JSON-RPC 2.0 on stdin/stdout, like real stdio servers.

Tools: add (read-only), shout, crash (ends the process), slow (sleeps 3 s), page through tools/list in two pages,
logs on stdout and stderr like sloppy servers do, and pings the client once."""
import json
import sys
import time

TOOLS = [
    {"name": "add", "description": "Add two numbers.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
                     "properties": {"a": {"type": "number"}, "b": {"$ref": "#/$defs/num"}},
                     "required": ["a", "b"], "$defs": {"num": {"type": "number", "description": "second"}}}},
    {"name": "shout", "description": "Make text loud.\nSecond line.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "crash", "description": "Stop the server.", "inputSchema": {"type": "object"}},
    {"name": "slow", "description": "Wait a while.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "fail", "description": "Always fails.", "inputSchema": {"type": "object", "properties": {}}},
]


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    print("fake server starting (a log line on stdout)", flush=True)
    sys.stderr.write("fake server log on stderr\n")
    sys.stderr.flush()
    for line in sys.stdin:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        mid, method, p = m.get("id"), m.get("method"), m.get("params") or {}
        if mid is None:
            continue                                   # notifications
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": p.get("protocolVersion"),
                                                          "capabilities": {"tools": {}},
                                                          "serverInfo": {"name": "fake", "version": "0"}}})
            send({"jsonrpc": "2.0", "id": "srv-1", "method": "ping"})       # a server may ask the client things
        elif method == "tools/list":
            if p.get("cursor") == "page2":
                send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS[2:]}})
            else:
                send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS[:2], "nextCursor": "page2"}})
        elif method == "tools/call":
            name, a = p.get("name"), p.get("arguments") or {}
            if name == "add":
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": str(a["a"] + a["b"])}]}})
            elif name == "shout":
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": a.get("text", "").upper()},
                                                                         {"type": "image", "data": "", "mimeType": "image/png"}]}})
            elif name == "crash":
                sys.stderr.write("boom: crashing on purpose\n")
                sys.stderr.flush()
                sys.exit(3)
            elif name == "slow":
                time.sleep(3)
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "finally"}]}})
            elif name == "fail":
                send({"jsonrpc": "2.0", "id": mid, "result": {"isError": True, "content": [{"type": "text", "text": "nope"}]}})
            else:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool {name}"}})
        elif "id" in m and "result" in m:
            pass
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "no such method"}})


if __name__ == "__main__":
    main()
