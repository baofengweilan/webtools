#!/usr/bin/env python3
"""webtools installer / client adapter.

Deterministic setup for AI-agent clients. The AI protocol in AGENTS.md /
CLAUDE.md / AI_INSTALL.md tells an agent to run:
    python install.py --detect --json
    python install.py --client <name>        (repeat per detected client)
    python -m webtools doctor

Actions for every install: ensure ~/.webtools/{keys,cache,state} and write a
README into keys/. MCP registration is idempotent (no duplicate entries).

Usage:
  python install.py --detect [--json]
  python install.py --auto [--with-browser]
  python install.py --client zcode|claude-desktop|cursor|claude-code
  python install.py --uninstall [--client NAME]
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HOME = Path.home()


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA") or (HOME / "AppData" / "Roaming"))


CLIENTS = {
    "zcode": {
        "config": HOME / ".zcode" / "cli" / "config.json",
        "kind": "nested",        # {"mcp": {"servers": {...}}}
        "timeout": 300000,
    },
    "claude-desktop": {
        "config": _appdata() / "Claude" / "claude_desktop_config.json",
        "kind": "flat",          # {"mcpServers": {...}}
    },
    "cursor": {
        "config": HOME / ".cursor" / "mcp.json",
        "kind": "flat",
    },
    "claude-code": {
        "config": HOME / ".claude.json",
        "kind": "flat",
    },
    "cherry-studio": {
        "config": _appdata() / "CherryStudio" / "config.json",
        "kind": "manual",        # schema varies by version -> manual guidance
    },
}


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _entry(kind: str) -> dict:
    if kind == "nested":
        return {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "webtools.mcp_server"],
            "timeoutMs": 300000,
            "enabled": True,
        }
    return {
        "command": sys.executable,
        "args": ["-m", "webtools.mcp_server"],
    }


def _servers_container(data: dict, kind: str) -> dict:
    if kind == "nested":
        return data.setdefault("mcp", {}).setdefault("servers", {})
    return data.setdefault("mcpServers", {})


def detect() -> dict:
    """Return {client: {path: str, detected: bool, installed: bool}}"""
    out = {}
    for name, spec in CLIENTS.items():
        path = spec["config"]
        detected = path.exists()
        installed = False
        if detected:
            data = _load(path)
            if spec["kind"] == "nested":
                servers = (data.get("mcp") or {}).get("servers") or {}
            else:
                servers = data.get("mcpServers") or {}
            installed = "webtools" in servers
        out[name] = {"path": str(path), "detected": detected,
                     "installed": installed, "kind": spec["kind"]}
    return out


ACCOUNTS = [
    ("tavily", "Tavily",
     "https://app.tavily.com/home",
     "1000 credits/month free (web search)"),
    ("firecrawl", "Firecrawl",
     "https://firecrawl.dev",
     "1000 credits/month free (search + scrape relay)"),
    ("nexus", "Nexus Mods",
     "https://www.nexusmods.com/users/myaccount?tab=api",
     "free personal API key (Nexus mod pages, ~1s instead of ~15s)"),
]


def _key_configured(name: str) -> bool:
    env = os.environ.get(name.upper() + "_API_KEY")
    if env and env.strip():
        return True
    try:
        return bool((HOME / ".webtools" / "keys" / f"{name}.key")
                    .read_text(encoding="utf-8").strip())
    except OSError:
        return False


def print_accounts() -> None:
    """Reminder for the user (and the AI) about free API accounts."""
    print("\n== free API accounts (optional, but recommended) ==")
    missing = 0
    for key, service, url, note in ACCOUNTS:
        state = "configured" if _key_configured(key) else "NOT configured"
        if not _key_configured(key):
            missing += 1
        print(f"  {service:12} {state:14} {note}")
        if not _key_configured(key):
            print(f"  {'':12} register: {url}")
    if missing:
        print(f"\n  {missing} account(s) missing - ask the USER to register "
              f"(free tiers), then save each key as ~/.webtools/keys/<name>.key")
        print("  Everything still works without them: missing channels are skipped.")
    else:
        print("\n  all accounts configured")


def ensure_home() -> Path:
    try:
        from webtools._paths import home, ensure_dirs
        ensure_dirs()
        base = home()
    except Exception:
        base = HOME / ".webtools"
        for sub in ("keys", "cache", "state"):
            (base / sub).mkdir(parents=True, exist_ok=True)
    readme = base / "keys" / "README.txt"
    if not readme.exists():
        readme.write_text(
            "Put free API keys here, one key per file, plain text:\n"
            "  tavily.key     https://app.tavily.com/home   (1000 credits/month free)\n"
            "  firecrawl.key  https://firecrawl.dev         (1000 credits/month free)\n"
            "  nexus.key      https://www.nexusmods.com/users/myaccount?tab=api\n"
            "Environment variables also work: TAVILY_API_KEY, FIRECRAWL_API_KEY, NEXUS_API_KEY\n",
            encoding="utf-8")
    return base


def install_client(name: str) -> int:
    spec = CLIENTS.get(name)
    if not spec:
        print(f"unknown client: {name}; known: {', '.join(CLIENTS)}")
        return 2
    if spec["kind"] == "manual":
        print(f"[{name}] manual setup required - open {spec['config']} and add an MCP "
              f"server named 'webtools': command={sys.executable!r} "
              f"args=['-m','webtools.mcp_server']")
        return 0
    path = spec["config"]
    data = _load(path)
    servers = _servers_container(data, spec["kind"])
    servers["webtools"] = _entry(spec["kind"])
    _save(path, data)
    print(f"[{name}] registered -> {path}")
    return 0


def uninstall_client(name: str) -> int:
    spec = CLIENTS.get(name)
    if not spec:
        print(f"unknown client: {name}")
        return 2
    path = spec["config"]
    data = _load(path)
    servers = _servers_container(data, spec["kind"])
    if servers.pop("webtools", None) is None:
        print(f"[{name}] no webtools entry found")
        return 0
    _save(path, data)
    print(f"[{name}] removed from {path}")
    return 0


def maybe_install_browser() -> None:
    print("installing browser extra: pip install camoufox ...")
    subprocess.call([sys.executable, "-m", "pip", "install", "camoufox"])
    print("downloading browser binary: python -m camoufox fetch ...")
    subprocess.call([sys.executable, "-m", "camoufox", "fetch"])


def main() -> int:
    ap = argparse.ArgumentParser(description="webtools installer / adapter")
    ap.add_argument("--detect", action="store_true", help="scan clients")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--auto", action="store_true", help="install into all detected clients")
    ap.add_argument("--client", default=None)
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--with-browser", action="store_true")
    ap.add_argument("--accounts", action="store_true",
                    help="show free API account checklist and exit")
    args = ap.parse_args()

    base = ensure_home()

    if args.accounts:
        print(f"webtools home: {base}")
        print_accounts()
        return 0

    if args.detect:
        info = detect()
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            print(f"webtools home: {base}")
            for name, rec in info.items():
                state = ("installed" if rec["installed"]
                         else "detected" if rec["detected"] else "not found")
                print(f"  {name:15} {state:10} {rec['path']}")
        return 0

    if args.client:
        target = args.client
        return (uninstall_client if args.uninstall else install_client)(target)

    if args.auto:
        info = detect()
        detected = [n for n, r in info.items() if r["detected"]]
        if not detected:
            print("no known client detected; use --client <name> manually")
            return 1
        rc = 0
        for name in detected:
            if info[name]["kind"] == "manual":
                install_client(name)  # prints guidance
            else:
                rc |= install_client(name)
        if args.with_browser:
            maybe_install_browser()
        print("\nnext: restart your client, then check MCP tools "
              "(web_search / web_research / web_fetch / quota_status / channel_status)")
        print_accounts()
        return rc

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())