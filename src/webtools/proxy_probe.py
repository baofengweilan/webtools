"""proxy_probe.py - shared liveness probe for the local Watt Toolkit proxy.

All web-tools scripts share this rule: a proxy is only used when its TCP port
is actually accepting connections. A configured-but-dead proxy (Watt exited or
crashed while WinINET still says ProxyEnable=1, or env/git points at it) is
treated as "no proxy" so callers fall back to direct connections instead of
stalling on connection-refused or reading proxy error pages as content.

Usage:
    from proxy_probe import proxy_alive, live_proxy

    proxy_alive()                          # bool, default Watt port 26561
    live_proxy("http://127.0.0.1:26561")   # -> url if port answers, else None
"""
from __future__ import annotations

import socket
from urllib.parse import urlsplit

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 26561
PROBE_TIMEOUT = 0.8


def _split(proxy: str) -> tuple[str, int]:
    if "://" not in proxy:
        proxy = "http://" + proxy
    parts = urlsplit(proxy)
    return parts.hostname or DEFAULT_HOST, parts.port or 80


def proxy_alive(proxy: str | None = None, timeout: float = PROBE_TIMEOUT) -> bool:
    """True only if the proxy endpoint accepts a TCP connection right now."""
    host, port = _split(proxy) if proxy else (DEFAULT_HOST, DEFAULT_PORT)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def live_proxy(proxy: str | None) -> str | None:
    """Return the proxy URL only if its port answers; otherwise None (direct)."""
    if not proxy:
        return None
    return proxy if proxy_alive(proxy) else None