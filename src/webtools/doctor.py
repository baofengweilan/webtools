"""webtools.doctor - environment self-check."""
import json
import os
import sys
from pathlib import Path

from ._paths import home, keys_dir, read_key


def _mcp_registrations():
    """Report which known clients already have a webtools MCP entry."""
    out = {}
    home_dir = Path.home()
    candidates = {
        "zcode": home_dir / ".zcode" / "cli" / "config.json",
        "claude-desktop": Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json",
        "cursor": home_dir / ".cursor" / "mcp.json",
        "claude-code": home_dir / ".claude.json",
    }
    for name, path in candidates.items():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            out[name] = None
            continue
        servers = (data.get("mcpServers")
                   or (data.get("mcp") or {}).get("servers") or {})
        out[name] = "webtools" in servers
    return out


def run_doctor():
    info = {"python": sys.version.split()[0], "home": str(home()), "checks": {}}
    checks = info["checks"]

    try:
        import curl_cffi  # noqa: F401
        checks["curl_cffi"] = True
    except Exception:
        checks["curl_cffi"] = False

    for name in ("tavily", "firecrawl", "nexus"):
        checks[f"key:{name}"] = bool(read_key(name))

    try:
        from camoufox.pkgman import camoufox_path
        checks["camoufox_browser"] = str(camoufox_path(download_if_missing=False))
    except Exception as e:
        checks["camoufox_browser"] = f"unavailable ({type(e).__name__})"

    try:
        from .fetch import PROXY
        info["proxy"] = PROXY
    except Exception:
        info["proxy"] = None

    info["mcp_registered"] = _mcp_registrations()
    info["keys_dir"] = str(keys_dir())
    return info


if __name__ == "__main__":
    print(json.dumps(run_doctor(), ensure_ascii=False, indent=1))