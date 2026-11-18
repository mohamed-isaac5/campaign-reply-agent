"""MCP stdio server exposing the agent's tools to AI clients like Claude.
Hand-rolled minimal JSON-RPC for the MCP handshake: initialize, tools/list, tools/call.
"""
import json
import sys

import db
import engine

TOOLS = [
    {"name": "list_campaigns", "description": "List campaigns with observed reply stats", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_review_queue", "description": "Replies waiting for an operator decision", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_digest", "description": "Today's activity digest", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_thread", "description": "Full thread with classifications and actions", "inputSchema": {"type": "object", "properties": {"thread_id": {"type": "integer"}}, "required": ["thread_id"]}},
]


def call(name, args):
    if name == "list_campaigns":
        return [dict(c, **engine.campaign_stats(c["id"])) for c in db.q("SELECT * FROM campaigns")]
    if name == "get_review_queue":
        return db.q("SELECT a.*, t.contact_name FROM actions a JOIN threads t ON t.id=a.thread_id WHERE a.status IN ('pending','sending','uncertain') ORDER BY a.created_at DESC")
    if name == "get_digest":
        return engine.digest()
    if name == "get_thread":
        tid = args.get("thread_id")
        t = db.q("SELECT * FROM threads WHERE id = ?", (tid,), one=True)
        if t:
            t["messages"] = db.q("SELECT * FROM messages WHERE thread_id = ? ORDER BY created_at", (tid,))
            t["actions"] = db.q("SELECT * FROM actions WHERE thread_id=? ORDER BY created_at,id", (tid,))
        return t
    return {"error": "unknown tool"}


def main():
    db.init()
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method, rid = req.get("method"), req.get("id")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "campaign-reply-agent", "version": "0.1.0"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = req.get("params", {})
            result = {"content": [{"type": "text", "text": json.dumps(call(p.get("name"), p.get("arguments", {})), ensure_ascii=False)}]}
        elif method and method.startswith("notifications/"):
            continue
        else:
            result = {"error": {"code": -32601, "message": "method not found"}}
        if rid is not None:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
