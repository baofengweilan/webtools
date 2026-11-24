"""Central path & key resolution. No personal paths hardcoded.

Layout (all under WEBTOOLS_HOME, default ~/.webtools):
  keys/<name>.key     - API keys (tavily, firecrawl, nexus)
  cache/              - page cache
  state/              - quota ledger, channel health
"""
import os
from pathlib import Path


def home() -> Path:
    env = os.environ.get("WEBTOOLS_HOME")
    if env and env.strip():
        return Path(env).expanduser()
    return Path.home() / ".webtools"


def keys_dir() -> Path:
    return home() / "keys"


def cache_dir() -> Path:
    return home() / "cache"


def state_dir() -> Path:
    return home() / "state"


def ensure_dirs() -> None:
    for d in (keys_dir(), cache_dir(), state_dir()):
        d.mkdir(parents=True, exist_ok=True)


def read_key(name: str):
    """env <NAME>_API_KEY first (e.g. TAVILY_API_KEY), then keys/<name>.key."""
    env = os.environ.get(name.upper() + "_API_KEY")
    if env and env.strip():
        return env.strip()
    try:
        value = (keys_dir() / f"{name}.key").read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None