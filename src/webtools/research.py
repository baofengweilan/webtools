"""webtools.research - health-aware search + fetch digest.

- web_search(): multi_search with per-provider health memory (cooldowns)
- research():   search, then fetch the top pages, return excerpts
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ._paths import state_dir
from .fetch import fetch_one
from .search import multi_search, quota_pick

STATE_PATH = state_dir() / "channels_health.json"


def _load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state):
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    except OSError:
        pass


TRANSIENT_MARKERS = ("ssl", "certificateverify", "timed out", "timeout",
                     "connection", "reset", "temporarily", "dns")


def is_transient_error(err):
    low = str(err).lower()
    return any(m in low for m in TRANSIENT_MARKERS)


def note_result(name, ok, err=None):
    state = _load_state()
    rec = state.setdefault(name, {"streak": 0, "cooldown_until": 0})
    if ok:
        rec["streak"] = 0
        rec["cooldown_until"] = 0
    elif is_transient_error(err or ""):
        rec["cooldown_until"] = max(rec.get("cooldown_until", 0),
                                    time.time() + 90)
    else:
        rec["streak"] = rec.get("streak", 0) + 1
        if rec["streak"] >= 3:
            rec["cooldown_until"] = time.time() + min(
                600 * (2 ** (rec["streak"] - 3)), 7200)
    state[name] = rec
    _save_state(state)


def available_providers(state=None):
    """Free providers not on cooldown, plus ONE quota-picked keyed provider."""
    state = state or _load_state()
    now = time.time()
    ok = []
    for name in ("github", "reddit"):
        rec = state.get(name) or {}
        if now >= rec.get("cooldown_until", 0):
            ok.append(name)
    pick = quota_pick()
    if pick:
        rec = state.get(pick) or {}
        if now >= rec.get("cooldown_until", 0):
            ok.append(pick)
    return ok


def _default_provider_set():
    providers = ["github", "reddit"]
    pick = quota_pick()
    if pick:
        providers.append(pick)
    return providers


def web_search(query, limit=5, force_all=False, with_reddit=False, sites=None):
    """Search, recording per-provider health. Returns (rows, meta)."""
    state = _load_state()
    if force_all:
        providers = _default_provider_set()
    else:
        providers = list(available_providers(state))
        if not providers:
            providers = _default_provider_set()
    if with_reddit and "reddit" not in providers:
        providers.append("reddit")
    rows, meta = multi_search(query, limit=limit, providers=providers,
                              sites=sites)
    for name, err in (meta.get("provider_errors") or {}).items():
        note_result(name, not err, err=err)
    for name, n in (meta.get("provider_rows") or {}).items():
        if n:
            note_result(name, True)
    meta["skipped"] = sorted(
        set(("firecrawl", "tavily", "github", "reddit"))
        - set(providers or [])) if not force_all else []
    return rows, meta


def research(query, top=4, limit=8, with_reddit=False, timeout=25):
    """Search + fetch the top pages -> digest with excerpts."""
    started = time.time()
    rows, meta = web_search(query, limit=limit, with_reddit=with_reddit)
    pages = [r["url"] for r in rows[:top]]

    fetched = {}
    deadline = time.time() + 200
    with ThreadPoolExecutor(max_workers=min(top, 4)) as ex:
        futs = {ex.submit(fetch_one, u, timeout, False, deadline): u for u in pages}
        for fut in as_completed(futs):
            u = futs[fut]
            try:
                fetched[u] = fut.result()
            except Exception as e:
                fetched[u] = {"url": u, "text": "",
                              "blocked": f"error:{type(e).__name__}"}

    results = []
    for row in rows:
        page = fetched.get(row["url"]) or {}
        text = (page.get("text") or "").strip()
        results.append({
            "url": row["url"],
            "title": row.get("title"),
            "snippet": row.get("snippet"),
            "sources": row.get("sources"),
            "fetch_tier": page.get("tier"),
            "fetch_ms": page.get("ms"),
            "blocked": page.get("blocked"),
            "excerpt": text[:1500],
            "text_len": len(text),
        })
    digest_meta = {"query": query, "search_elapsed_s": meta.get("elapsed_s"),
                   "total_elapsed_s": round(time.time() - started, 1),
                   "search_meta": meta, "pages_fetched": len(pages)}
    return results, digest_meta