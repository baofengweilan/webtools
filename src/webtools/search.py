"""webtools.search - unified web search across free-quota providers ONLY.

Default per query (all run in parallel): github + reddit + exactly ONE of
tavily/firecrawl. The two keyed channels never run together: the quota ledger
(~/.webtools/state/quota.json) consumes tavily's 1000/month first and switches
to firecrawl only when tavily reports its quota exhausted. Counters reset each
calendar month.
  firecrawl  1000 credits/month free   key: keys/firecrawl.key or FIRECRAWL_API_KEY
  tavily     1000 credits/month free   key: keys/tavily.key or TAVILY_API_KEY
  github     free API                  token: ~/.git-credentials
  reddit     arctic-shift archive API  free, no key (slow/flaky, enabled)
No pay-per-call-only APIs by design.

Search options: --sites for mod-site scoped search (nexus:<game>, modio:<game>,
github) which never uses relay engines.

Ranking: agreement bonus for multi-provider hits, SOURCE_RANK domain table,
relevance guard for weak matches.
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from curl_cffi import requests as cffi_requests

from ._paths import read_key, state_dir

PROVIDER_TIMEOUT = 14          # seconds per provider
CJK = re.compile(r"[\u4e00-\u9fff]")
WS = re.compile(r"\s+")


# ------------------------------------------------------------------ keys
def gh_token():
    try:
        text = (Path.home() / ".git-credentials").read_text(encoding="utf-8")
        for line in text.splitlines():
            if "github.com" in line:
                cred = line.split("://", 1)[1].split("@", 1)[0]
                return cred.split(":", 1)[1]
    except (OSError, IndexError, ValueError):
        pass
    return None


# ------------------------------------------------------------------ relevance
def query_tokens(query):
    """Latin words >=3 chars + CJK bigrams (CJK pairs match loosely)."""
    tokens = {w.lower() for w in re.findall(r"[A-Za-z0-9]{3,}", query)}
    cjk = CJK.findall(query)
    tokens.update(a + b for a, b in zip(cjk, cjk[1:]))
    return tokens


def row_relevant(tokens, title, snippet):
    """Rows must mention >=2 tokens once the query has >=3 of them."""
    if len(tokens) < 3:
        return True
    hay = f"{title} {snippet}".lower()
    hits = sum(1 for t in tokens if t in hay)
    return hits >= 2


# ------------------------------------------------------------------ ranking
PROVIDER_WEIGHT = {
    "firecrawl": 55,
    "tavily": 50,
    "github": 46,
    "reddit": 40,
}

SOURCE_RANK = [
    (100, ("learn.microsoft.com", "docs.python.org", "developer.mozilla.org",
           "sqlite.org", "docs.godotengine.org", "doc.rust-lang.org",
           "go.dev", "vuejs.org", "react.dev", "angular.dev")),
    (96, ("stackoverflow.com", "superuser.com", "serverfault.com",
          "askubuntu.com", "stackexchange.com")),
    (92, ("github.com", "gitlab.com", "raw.githubusercontent.com")),
    (86, ("reddit.com", "news.ycombinator.com")),
    (72, ("nexusmods.com", "mod.io")),
    (78, ("wikipedia.org", "arxiv.org")),
    (70, ("zhihu.com", "cnblogs.com", "segmentfault.com", "v2ex.com",
          "jianshu.com")),
    (58, ("csdn.net", "juejin.cn", "bilibili.com", "weixin.qq.com",
          "sohu.com", "163.com", "toutiao.com", "baijiahao.baidu.com")),
]


def domain_score(url):
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for score, suffixes in SOURCE_RANK:
        for suffix in suffixes:
            if host == suffix or host.endswith("." + suffix):
                return score
    return 60


def norm_url(url):
    url = (url or "").split("#", 1)[0].rstrip("/")
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    return url.lower()


def rank_rows(rows):
    """Agreement + provider weight + domain quality, then dedupe."""
    grouped: dict[str, dict] = {}
    for row in rows:
        key = norm_url(row.get("url"))
        if not key:
            continue
        g = grouped.setdefault(key, {"row": row, "sources": set()})
        g["sources"].add(row.get("source") or "?")

    scored = []
    for key, g in grouped.items():
        row = g["row"]
        sources = g["sources"]
        score = (len(sources) - 1) * 60 + max(
            PROVIDER_WEIGHT.get(s, 30) for s in sources)
        score += (domain_score(row.get("url") or "") - 60)
        scored.append((score, len(sources), row, sources))

    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored


# ------------------------------------------------------------------ proxy
def _detect_proxy():
    from .proxy_probe import live_proxy
    for name in ("WEBTOOLS_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
                 "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(name)
        if value and value.strip():
            return live_proxy(value.strip())
    try:
        import winreg
        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        if enabled and server:
            return live_proxy(server if "://" in server else "http://" + server)
    except Exception:
        pass
    return None


PROXY = _detect_proxy()
_proxy_dead_until = 0.0          # proxy failing -> prefer direct for 2 min


def http_call(method, url, headers=None, json_body=None,
              timeout=PROVIDER_TIMEOUT, profile="chrome", proxy_first=False):
    """HTTP with two-way fallback: direct first (strict verify), then the
    system proxy (verify=False - its MITM CA is in the Windows store but not
    in curl's bundle). Poisoned DNS (api.github.com cert errors) resolves via
    the proxy; a dead proxy resolves by falling back to direct. proxy_first
    flips the order for services that only work through the Watt proxy."""
    global _proxy_dead_until
    modes = ["proxy", "direct"] if proxy_first else ["direct"]
    if proxy_first:
        if not PROXY:
            modes = ["direct"]
    elif PROXY and time.time() >= _proxy_dead_until:
        modes.append("proxy")
    last = None
    for mode in modes:
        kwargs = {"json": json_body, "headers": headers or {},
                  "timeout": timeout, "impersonate": profile}
        if mode == "proxy":
            kwargs["proxies"] = {"http": PROXY, "https": PROXY}
            kwargs["verify"] = False
        try:
            r = cffi_requests.request(method, url, **kwargs)
            if mode == "proxy":
                # Watt's own upstream-failure page (not a real API answer):
                # treat as an exception so the caller retries/re-rotates
                if r.status_code in (502, 503) and "Could not find any IP" in (r.text or ""):
                    raise RuntimeError("watt upstream down")
                _proxy_dead_until = 0.0
            return r
        except Exception as e:
            last = e
            # only mark the proxy "dead" for connection-level failures;
            # a Watt upstream 5xx is domain-specific, the proxy itself is fine
            if mode == "proxy" and not isinstance(e, RuntimeError):
                _proxy_dead_until = time.time() + 120
    raise last


# ------------------------------------------------------------------ HTTP
def http_post(url, body, headers=None, timeout=PROVIDER_TIMEOUT):
    return http_call("POST", url, headers=headers, json_body=body,
                     timeout=timeout)


def http_get(url, headers=None, timeout=PROVIDER_TIMEOUT, profile="chrome",
             proxy_first=False):
    return http_call("GET", url, headers=headers, timeout=timeout,
                     profile=profile, proxy_first=proxy_first)


# ------------------------------------------------------------------ providers
def search_firecrawl(query, limit):
    key = read_key("firecrawl")
    if not key:
        return []
    r = http_post("https://api.firecrawl.dev/v1/search",
                  {"query": query, "limit": min(limit, 6)},
                  headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        raise RuntimeError(f"firecrawl {r.status_code}")
    payload = r.json()
    data = payload.get("data")
    if isinstance(data, dict):          # old shape: {"web": [...], "news": [...]}
        items = data.get("web") or []
    elif isinstance(data, list):        # new shape: data IS the result list
        items = data
    else:
        items = []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url:
            continue
        out.append({"url": url, "title": (item.get("title") or url)[:200],
                    "snippet": (item.get("description") or
                                item.get("markdown") or "")[:400],
                    "source": "firecrawl"})
    return out


def search_tavily(query, limit):
    key = read_key("tavily")
    if not key:
        return []
    last_error = None
    for profile in ("chrome", "safari", "firefox"):
        try:
            r = http_call("POST", "https://api.tavily.com/search",
                          json_body={"query": query, "max_results": min(limit, 6),
                                     "search_depth": "basic"},
                          headers={"Authorization": f"Bearer {key}"},
                          timeout=PROVIDER_TIMEOUT, profile=profile)
            if r.status_code != 200:
                raise RuntimeError(f"tavily {r.status_code}")
            break
        except RuntimeError:
            raise
        except Exception as e:  # TLS reset etc -> rotate fingerprint
            last_error = e
            continue
    else:
        raise last_error or RuntimeError("tavily failed")
    out = []
    for item in r.json().get("results") or []:
        out.append({"url": item.get("url"), "title": (item.get("title") or "")[:200],
                    "snippet": (item.get("content") or "")[:400],
                    "source": "tavily"})
    return out


def search_github(query, limit):
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "zcode-multi-search"}
    token = gh_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    last_error = None
    for attempt in range(2):  # poisoned DNS is intermittent - retry once
        try:
            r = http_get("https://api.github.com/search/repositories?q="
                         + quote_plus(query)
                         + f"&sort=stars&per_page={min(limit, 6)}",
                         headers=headers)
            if r.status_code != 200:
                raise RuntimeError(f"github {r.status_code}")
            break
        except RuntimeError:
            raise
        except Exception as e:  # SSL/cert errors: DNS poisoning, rotates
            last_error = e
            if attempt == 0:
                time.sleep(0.8)
            continue
    else:
        raise last_error or RuntimeError("github failed")
    out = []
    for item in r.json().get("items") or []:
        out.append({"url": item.get("html_url"),
                    "title": f"{item.get('full_name')}  "
                             f"\u2605{item.get('stargazers_count')}",
                    "snippet": (item.get("description") or "")[:400],
                    "source": "github"})
    return out


TECH_SUBS = ("Windows10", "WindowsHelp", "techsupport")


def search_reddit(query, limit):
    """arctic-shift archive API - requires a subreddit, so sweep a few in
    parallel. The service is flaky (random 422 'Timeout' / TLS resets), so
    each sweep retries twice and partial results are accepted. Works only
    via the Watt proxy; direct returns empty 422s."""
    headers = {"User-Agent": "zcode-multi-search"}

    def one(sub):
        url = ("https://arctic-shift.photon-reddit.com/api/posts/search?"
               f"subreddit={sub}&limit=6&sort=desc&query="
               + quote_plus(query[:120]))
        for attempt in range(3):
            try:
                r = http_get(url, headers=headers, timeout=8,
                             proxy_first=True)
                if r.status_code == 200:
                    rows = []
                    for item in r.json().get("data") or []:
                        permalink = (item.get("permalink")
                                     or f"/comments/{item.get('id')}")
                        rows.append({"url": f"https://www.reddit.com{permalink}",
                                     "title": (item.get("title") or "")[:200],
                                     "snippet": (item.get("selftext") or "")[:300],
                                     "source": "reddit"})
                    return rows
            except Exception:
                pass
            time.sleep(0.7 * (attempt + 1))
        return []

    out = []
    with ThreadPoolExecutor(max_workers=len(TECH_SUBS)) as ex:
        for part in ex.map(one, TECH_SUBS):
            out.extend(part)
    return out


PROVIDERS = {
    "firecrawl": search_firecrawl,
    "tavily": search_tavily,
    "github": search_github,
    "reddit": search_reddit,
}


# ------------------------------------------------------------------ quota
# tavily and firecrawl are NOT used together: one keeps consuming its monthly
# free quota until the API reports exhaustion, then the other takes over.
QUOTA_PATH = state_dir() / "quota.json"
QUOTA_ORDER = ("tavily", "firecrawl")   # 1st choice first (measured faster)


def _quota_load():
    try:
        data = json.loads(QUOTA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    month = time.strftime("%Y-%m")
    if data.get("month") != month:      # new month -> fresh quotas
        data = {"month": month, "providers": {}}
    data.setdefault("providers", {})
    return data


def _quota_save(data):
    try:
        QUOTA_PATH.parent.mkdir(parents=True, exist_ok=True)
        QUOTA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    except OSError:
        pass


def quota_state():
    data = _quota_load()
    return {name: {"used": int((data["providers"].get(name) or {}).get("used", 0)),
                   "exhausted": bool((data["providers"].get(name) or {}).get("exhausted", False))}
            for name in QUOTA_ORDER}


def quota_pick(exclude=()):
    """First keyed provider whose monthly quota is not exhausted."""
    data = _quota_load()
    for name in QUOTA_ORDER:
        rec = data["providers"].get(name) or {}
        if rec.get("exhausted") or name in exclude:
            continue
        if read_key(name):
            return name
    return None


def _quota_exhausted_err(err):
    low = str(err or "").lower()
    return any(m in low for m in ("402", "432", "insufficient", "credit",
                                  "quota", "payment", "out of"))


def _quota_note(name, ok, err=None):
    data = _quota_load()
    rec = data["providers"].setdefault(name, {"used": 0, "exhausted": False})
    if ok:
        rec["used"] = int(rec.get("used", 0)) + 1
    elif _quota_exhausted_err(err):
        rec["exhausted"] = True
    data["providers"][name] = rec
    _quota_save(data)


# ------------------------------------------------------------------ mod sites
SITE_ALIASES = {
    "nexus": "nexusmods.com",
    "nexusmods": "nexusmods.com",
    "modio": "mod.io",
    "mod.io": "mod.io",
    "github": "github.com",
}


def resolve_sites(spec):
    """'nexus:skyrimspecialedition,modio:valheim,github' -> [(domain, game)]"""
    sites = []
    for part in (spec or "").split(","):
        part = part.strip().lower()
        if not part:
            continue
        game = None
        if ":" in part:
            part, game = part.split(":", 1)
        domain = SITE_ALIASES.get(part, part if "." in part else None)
        if domain and (domain, game or None) not in sites:
            sites.append((domain, game or None))
    return sites


def _browser_site_search(domain, query, limit=6, game=None, wait_s=42):
    """Read a site's own search results through Camoufox (no relay engines).
    Nexus: mods listing with ?keywords= (works; the /search page drops params).
    mod.io: game page with ?q= (site currently unreachable via Watt upstream)."""
    import bulk_fetch as bf

    if domain == "nexusmods.com":
        if not game:
            raise ValueError("nexus 站内搜索需要游戏域名，如 "
                             "--sites nexus:skyrimspecialedition")
        # the real search is the listing filter: /games/{game}/mods?title=...
        target = (f"https://www.nexusmods.com/games/{game}/mods?title="
                  + quote_plus(query))
        js = r'''() => Array.from(document.querySelectorAll("a[href*='/mods/']")).map(a => [a.href, (a.textContent||'').trim().replace(/\s+/g,' ')]).filter(x => /\/mods\/\d+$/.test(x[0]) && x[1].length > 2)'''
        source = "nexusmods"
    elif domain == "mod.io":
        if not game:
            raise ValueError("mod.io 站内搜索需要游戏 slug，如 "
                             "--sites modio:valheim")
        target = f"https://mod.io/g/{game}?q={quote_plus(query)}"
        js = r'''() => Array.from(document.querySelectorAll("a[href*='/m/']")).map(a => [a.href, (a.textContent||'').trim().replace(/\s+/g,' ')])'''
        source = "modio"
    else:
        return []

    bf.ensure_camofox()
    uid = f"site{int(time.time() * 1000) % 1000000}"
    created = bf._camofox_api("POST", "/tabs",
                              {"url": target, "userId": uid, "locale": "zh-CN"},
                              timeout=90)
    tab = created.get("tabId")
    if not tab:
        return []
    links = []
    deadline = time.time() + wait_s
    try:
        while time.time() < deadline:
            time.sleep(4)
            try:
                res = bf._camofox_api(
                    "POST", f"/tabs/{tab}/act",
                    {"action": "eval", "params": {"expression": js}},
                    timeout=60)
                links = res.get("value") or []
            except Exception:
                continue
            if len({href for href, _ in links}) >= min(limit, 4):
                break
    finally:
        try:
            bf._camofox_api("DELETE", f"/tabs/{tab}", timeout=20)
        except Exception:
            pass

    rows, seen = [], set()
    for href, title in links:
        href = (href or "").split("#", 1)[0]
        if not href or href in seen:
            continue
        if domain == "nexusmods.com":
            tail = href.split("/mods/")[-1].split("?")[0] if "/mods/" in href else ""
            if not (f"/{game}/mods/" in href and tail.isdigit()):
                continue
        seen.add(href)
        rows.append({"url": href, "title": (title or href)[:200],
                     "snippet": "", "source": source})
        if len(rows) >= limit:
            break
    return rows


def _site_search(query, sites, limit=8):
    """Site-scoped search WITHOUT tavily/firecrawl (user rule for the three
    Watt-accelerated sites): github -> official API, nexus/mod.io -> browser."""
    rows = []
    errors = {}
    for domain, game in sites:
        try:
            if domain == "github.com":
                rows += [dict(row, source="github")
                         for row in search_github(query, limit)]
            elif domain in ("nexusmods.com", "mod.io"):
                rows += _browser_site_search(domain, query, limit, game=game)
            else:
                errors[domain] = "unsupported site"
        except Exception as e:
            errors[domain] = f"{type(e).__name__}: {e}"
    return rows, errors


# ------------------------------------------------------------------ search()
def multi_search(query, limit=5, providers=None, verbose=False, sites=None):
    """Run active providers in parallel, merge + rank, return list[dict].

    Default set: github (free) + exactly ONE of tavily/firecrawl, chosen by
    the monthly quota ledger. Explicit provider lists are honoured except
    that tavily+firecrawl together is reduced to the first one (user rule:
    never consume both quotas in parallel). `sites` adds a domain-scoped
    search ("nexus,modio" or raw domains) via the same keyed engine."""
    auto = providers is None
    if auto:
        providers = ["github", "reddit"]   # reddit participates by default
        pick = quota_pick()
        if pick:
            providers.append(pick)
    else:
        providers = [p.strip() for p in providers if p and p.strip()]
        keyed = [p for p in providers if p in QUOTA_ORDER]
        if len(keyed) > 1:              # enforce one-keyed-channel-at-a-time
            keep = keyed[0]
            providers = [p for p in providers
                         if p not in QUOTA_ORDER or p == keep]
            if verbose:
                print(f"[quota] 只保留 {keep}（tavily/firecrawl 顺序耗额度）",
                      file=sys.stderr)

    selected = {name: PROVIDERS[name] for name in providers if name in PROVIDERS}
    if not selected:
        raise ValueError("no providers selected")

    results: dict[str, list] = {}
    errors: dict[str, str] = {}
    started = time.time()

    def run(name, fn):
        try:
            return name, fn(query, limit), None
        except Exception as e:
            return name, [], f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=len(selected)) as ex:
        futs = [ex.submit(run, name, fn) for name, fn in selected.items()]
        for fut in as_completed(futs):
            name, rows, err = fut.result()
            results[name] = rows
            if err:
                errors[name] = err
                if verbose:
                    print(f"[{name}] FAIL {err}", file=sys.stderr)

    # quota accounting + auto-switch to the other keyed provider
    for name in QUOTA_ORDER:
        if name not in selected:
            continue
        if name not in errors:
            _quota_note(name, True)     # call consumed a credit, rows or not
            continue
        if not _quota_exhausted_err(errors[name]):
            continue
        _quota_note(name, False, errors[name])
        if auto:
            other = next((n for n in QUOTA_ORDER
                          if n != name and n not in selected), None)
            if other and read_key(other):
                try:
                    results[other] = PROVIDERS[other](query, limit)
                    _quota_note(other, True)
                    if verbose:
                        print(f"[quota] {name} 额度耗尽，已改用 {other}",
                              file=sys.stderr)
                except Exception as e:
                    errors[other] = f"{type(e).__name__}: {e}"

    # optional domain-scoped search (mod sites etc.) - no relay engines
    site_rows = []
    if sites:
        domains = resolve_sites(sites)
        if domains:
            site_rows, site_errors = _site_search(query, domains,
                                                  limit=max(limit, 5))
            for domain, err in site_errors.items():
                errors[f"site:{domain}"] = err
                if verbose:
                    print(f"[sites] {domain} FAIL {err}", file=sys.stderr)

    all_rows = [r for rows in results.values() for r in rows] + site_rows
    tokens = query_tokens(query)
    scored = rank_rows(all_rows)
    if site_rows:  # explicit --sites request -> lift those rows to the top
        wanted = {row.get("source") for row in site_rows}
        scored = [(score + (40 if row.get("source") in wanted else 0),
                   agreement, row, sources)
                  for score, agreement, row, sources in scored]
        scored.sort(key=lambda t: (-t[0], t[1]))

    final = []
    seen = set()
    for score, agreement, row, sources in scored:
        key = norm_url(row.get("url"))
        if key in seen:
            continue
        seen.add(key)
        # relevance guard; cross-confirmed rows are exempt
        if agreement < 2 and not row_relevant(tokens, row.get("title") or "",
                                              row.get("snippet") or ""):
            continue
        final.append({
            "url": row.get("url"),
            "title": WS.sub(" ", row.get("title") or "").strip(),
            "snippet": WS.sub(" ", row.get("snippet") or "").strip(),
            "score": score,
            "sources": sorted(sources),
        })
        if len(final) >= limit:
            break

    meta = {"query": query, "elapsed_s": round(time.time() - started, 2),
            "provider_rows": {k: len(v) for k, v in results.items()},
            "provider_errors": errors, "returned": len(final),
            "site_rows": len(site_rows), "quota": quota_state()}
    return final, meta


if __name__ == "__main__":
    # convenience: python -m webtools.search "query"
    import argparse

    ap = argparse.ArgumentParser(description="webtools search (quick mode)")
    ap.add_argument("query", nargs="+")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--sites", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    q = " ".join(a.query)
    rows, meta = multi_search(q, limit=a.limit, sites=a.sites)
    if a.json:
        print(json.dumps({"meta": meta, "results": rows}, ensure_ascii=False, indent=1))
    else:
        print(f"# {q}  ({meta['elapsed_s']}s)")
        for i, r in enumerate(rows, 1):
            print(f"\n{i}. {r['title']}\n   {r['url']}\n   [{'+'.join(r['sources'])}]")
