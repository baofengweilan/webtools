"""webtools.mcp_server - MCP stdio server exposing the webtools pipeline.

Tools:
  web_search    {query, limit?, all?, reddit?, sites?}  health-aware search
  web_research  {query, top?, limit?, reddit?}          search + fetch digest
  web_fetch     {url, browser?}                          tiered page fetch
  quota_status  {}                                       monthly keyed quota ledger
  channel_status {}                                      provider health ledger

Protocol: JSON-RPC 2.0 over stdio, one UTF-8 JSON per line. Thread pool (2
workers). Auto-reload: changed package modules are re-imported before the next
call (edit-friendly during development).

Run: python -m webtools.mcp_server
Register: {"command": "python", "args": ["-m", "webtools.mcp_server"]}
"""
import importlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from . import fetch as fetch_mod
from . import research as research_mod
from . import search as search_mod

WATCHED = (fetch_mod, research_mod, search_mod)
_mtimes = {m.__name__: None for m in WATCHED}


def _maybe_reload():
    for mod in WATCHED:
        path = Path(mod.__file__)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        name = mod.__name__
        if _mtimes[name] is None:
            _mtimes[name] = mtime
        elif mtime != _mtimes[name]:
            _mtimes[name] = mtime
            try:
                importlib.reload(mod)
                print(f"[mcp] reloaded {name}", file=sys.stderr)
            except Exception as e:
                print(f"[mcp] reload {name} failed: {e}", file=sys.stderr)


TOOLS = [
    {
        "name": "web_search",
        "description": ("多渠道并行网页搜索（firecrawl/tavily 有免费额度，github 免费，"
                        "红迪可选）。返回标题+URL+摘要，跨源互证排序。"
                        "sites 参数可做模组站内搜索（不走中转），格式 "
                        "'nexus:skyrimspecialedition,modio:valheim,github'。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "all": {"type": "boolean"},
                "reddit": {"type": "boolean"},
                "sites": {"type": "string"},
            },
        },
    },
    {
        "name": "web_research",
        "description": ("先搜索再抓取前 N 个页面，返回每页摘要与正文节选"
                        "（分层抓取：API 直连 -> curl_cffi -> Camoufox -> Firecrawl）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top": {"type": "integer"},
                "limit": {"type": "integer"},
                "reddit": {"type": "boolean"},
            },
        },
    },
    {
        "name": "web_fetch",
        "description": ("抓取单个网页正文（Tier0 curl_cffi/API -> Tier2 Camoufox 浏览器 -> "
                        "Tier3 Firecrawl 中转；browser=true 直接上浏览器）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "browser": {"type": "boolean"},
            },
        },
    },
    {
        "name": "channel_status",
        "description": "查看各搜索渠道健康状态与冷却时间。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "quota_status",
        "description": "查看 tavily/firecrawl 本月额度账本（已用次数、是否耗尽）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _tool_web_search(args):
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query required")
    sites = str(args.get("sites") or "").strip() or None
    rows, meta = research_mod.web_search(
        query,
        limit=int(args.get("limit") or 5),
        force_all=bool(args.get("all")),
        with_reddit=bool(args.get("reddit")),
        sites=sites)
    return {"query": query, "meta": meta, "results": rows}


def _tool_web_research(args):
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query required")
    results, meta = research_mod.research(
        query,
        top=int(args.get("top") or 4),
        limit=int(args.get("limit") or 8),
        with_reddit=bool(args.get("reddit")))
    return {"query": query, "meta": meta, "results": results}


def _tool_web_fetch(args):
    url = str(args.get("url") or "").strip()
    if not url:
        raise ValueError("url required")
    page = fetch_mod.fetch_one(url, timeout=25,
                               force_browser=bool(args.get("browser")),
                               deadline=time.time() + 220)
    return page


def _tool_channel_status(args):
    try:
        state = json.loads(research_mod.STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    now = time.time()
    out = {}
    for name, rec in state.items():
        cd = rec.get("cooldown_until", 0)
        out[name] = {"streak": rec.get("streak", 0),
                     "cooldown_s": max(0, int(cd - now)),
                     "available": now >= cd}
    return {"channels": out}


def _tool_quota_status(args):
    return {"month": time.strftime("%Y-%m"), "quota": search_mod.quota_state()}


HANDLERS = {
    "web_search": _tool_web_search,
    "web_research": _tool_web_research,
    "web_fetch": _tool_web_fetch,
    "channel_status": _tool_channel_status,
    "quota_status": _tool_quota_status,
}

POOL = ThreadPoolExecutor(max_workers=2)


def _respond(msg_id, result):
    _write_line({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _write_line(obj):
    data = json.dumps(obj, ensure_ascii=False)
    sys.stdout.write(data + "\n")
    sys.stdout.flush()


def _handle(req):
    method = req.get("method")
    msg_id = req.get("id")

    if method == "initialize":
        return _respond(msg_id, {
            "protocolVersion": req.get("params", {}).get(
                "protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "webtools", "version": "0.1.0"},
        })
    if method == "ping":
        return _respond(msg_id, {})
    if method == "tools/list":
        return _respond(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if not handler:
            return _respond(msg_id, {"content": [
                {"type": "text", "text": f"unknown tool: {name}"}],
                "isError": True})
        try:
            result = POOL.submit(handler, args).result(timeout=300)
            text = json.dumps(result, ensure_ascii=False, indent=1)
            return _respond(msg_id, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            text = f"{type(e).__name__}: {e}"
            return _respond(msg_id, {"content": [{"type": "text", "text": text}],
                                     "isError": True})
    if msg_id is not None and method and method.startswith("rpc."):
        return _respond(msg_id, {"error": {"code": -32601,
                                           "message": f"no method {method}"}})
    # notifications (initialized, cancelled...) - nothing to send


def main():
    print(f"[mcp] webtools ready pid={os.getpid()}", file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        _maybe_reload()
        try:
            _handle(req)
        except Exception as e:
            print(f"[mcp] handler crash: {e}", file=sys.stderr)
            if req.get("id") is not None:
                _respond(req["id"], {"content": [
                    {"type": "text", "text": f"server error: {e}"}],
                    "isError": True})


if __name__ == "__main__":
    main()
