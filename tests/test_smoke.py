"""Smoke tests: imports, pure helpers, CLI parse, MCP handshake (no network)."""
import json
import subprocess
import sys


def test_imports():
    import webtools  # noqa: F401
    from webtools import search, fetch, research, cli, doctor, mcp_server  # noqa: F401
    from webtools.browser import client, server  # noqa: F401


def test_query_tokens():
    from webtools.search import query_tokens
    toks = query_tokens("windows dns probe finished")
    assert "windows" in toks and "dns" in toks
    cjk = query_tokens("存档丢失修复")
    assert any(len(t) == 2 for t in cjk)


def test_norm_url_dedup():
    from webtools.search import norm_url
    assert norm_url("https://www.example.com/a/") == "example.com/a"


def test_resolve_sites():
    from webtools.search import resolve_sites
    assert resolve_sites("nexus:skyrimspecialedition,github") == [
        ("nexusmods.com", "skyrimspecialedition"), ("github.com", None)]


def test_cli_help():
    out = subprocess.run([sys.executable, "-m", "webtools.cli", "--help"],
                         capture_output=True, text=True)
    assert out.returncode == 0
    assert "search" in out.stdout and "fetch" in out.stdout


def test_mcp_handshake():
    proc = subprocess.Popen(
        [sys.executable, "-m", "webtools.mcp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    try:
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"}}) + "\n")
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["result"]["serverInfo"]["name"] == "webtools"
        proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        names = {t["name"] for t in resp["result"]["tools"]}
        assert {"web_search", "web_fetch", "quota_status"} <= names
    finally:
        proc.kill()