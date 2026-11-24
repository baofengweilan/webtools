"""webtools.fetch - tiered page fetching.

Tier 0: curl_cffi with rotating TLS fingerprints (0.3-2s, free)
Tier 1a: Stack Exchange API for SO questions (~1s, free, 300/day)
Tier 1b: GitHub API for repos/issues (~1s, free with token)
Tier 1c: Nexus Mods API when keys/nexus.key exists (~1s)
Tier 2: Camoufox browser (15-90s, for Cloudflare/JS-only pages) - tried
        BEFORE Firecrawl by design
Tier 3: Firecrawl scrape (2-8s, 1 credit) - last resort relay
        (never used for accelerated domains: github/nexusmods/mod.io)

Cache in ~/.webtools/cache, keys in ~/.webtools/keys.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests

from ._paths import cache_dir, read_key

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def clean(text) -> str:
    return WS.sub(" ", TAG.sub("", str(text or ""))).strip()


_own_proxy: str | None = None


def _detect_proxy():
    from .proxy_probe import live_proxy
    for name in ("WEBTOOLS_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
                 "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(name)
        if value and value.strip() and value.strip() != _own_proxy:
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
# NOTE: deliberately NOT injecting HTTP(S)_PROXY into the environment -
# curl_cffi would then route through the local MITM proxy and fail cert
# verification. fetch_tier0 goes direct first and only falls back to the
# proxy (verify=False) when direct TLS fails (poisoned DNS etc).
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,::1")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost,::1")

DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
EXTERNAL_OPENER = (urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    if PROXY else DIRECT_OPENER)


def urllib_open(req, timeout):
    """urllib with fallback. Accelerated domains (Watt Toolkit covers GitHub)
    go through the system proxy first - direct connections to
    raw.githubusercontent get reset; everything else stays direct-first. The
    proxy's MITM CA lives in the Windows cert store, which urllib validates."""
    accel = is_accel(req.full_url)
    order = (EXTERNAL_OPENER, DIRECT_OPENER) if accel else (DIRECT_OPENER, EXTERNAL_OPENER)
    last = None
    for opener in order:
        try:
            return opener.open(req, timeout=timeout)
        except Exception as e:
            last = e
    raise last


ACCEL_SUFFIXES = ("github.com", "githubusercontent.com", "nexusmods.com", "mod.io")


def is_accel(url):
    """Domains the user accelerates with Watt Toolkit: never fetched via
    tavily/firecrawl relay, proxy-first for plain HTTP, browser for CF walls."""
    host = urlparse(url).netloc.lower().split(":")[0].removeprefix("www.")
    return any(host == s or host.endswith("." + s) for s in ACCEL_SUFFIXES)

_last_proxy_check = 0.0


def refresh_proxy():
    """Re-detect the system proxy every 30s; rebuild the urllib opener only.
    Never touches os.environ - curl_cffi must stay direct by default."""
    global PROXY, EXTERNAL_OPENER, _own_proxy, _last_proxy_check
    now = time.time()
    if now - _last_proxy_check < 30:
        return
    _last_proxy_check = now
    detected = _detect_proxy()
    if detected == PROXY:
        return
    PROXY = detected
    _own_proxy = detected
    EXTERNAL_OPENER = (urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": detected, "https": detected}))
        if detected else DIRECT_OPENER)


def opener_for(url: str):
    host = urllib.parse.urlparse(url).netloc.lower()
    if host.startswith(LOCAL_HOSTS):
        return DIRECT_OPENER
    return EXTERNAL_OPENER


# ------------------------------------------------------------------ parsers
class TextExtractor(HTMLParser):
    SKIP_TAGS = {"script", "style", "noscript", "svg", "head", "iframe", "template"}
    BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title = (self.title + data).strip()
        if self.skip:
            return
        stripped = data.strip()
        if stripped:
            self.parts.append(stripped + " ")

    def text(self):
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n", raw)
        return raw.strip()


# ------------------------------------------------------------------ cache
def _bulk_cache_dir():
    d = cache_dir() / "bulk"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_read(url, tier, ttl_hours):
    d = _bulk_cache_dir()
    key = hashlib.sha1(f"{tier}|{url}".encode()).hexdigest()
    path = os.path.join(d, f"{key}.json")
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > ttl_hours * 3600:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def cache_write(url, tier, payload):
    d = _bulk_cache_dir()
    key = hashlib.sha1(f"{tier}|{url}".encode()).hexdigest()
    path = os.path.join(d, f"{key}.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
    except OSError:
        pass


# ------------------------------------------------------------------ helpers
CHALLENGE_MARKERS = (
    "just a moment", "cf-browser-verification", "attention required",
    "verify you are human", "ddos protection", "checking your browser",
    "enable javascript and cookies", "captcha", "are you a robot",
)


def looks_blocked(status, html, text):
    """Heuristic: did the origin give us a wall instead of the page?"""
    if status in (401, 403, 429, 503):
        return f"http:{status}"
    low = (html or "").lower()[:5000]
    for marker in CHALLENGE_MARKERS:
        if marker in low:
            return "challenge"
    if status == 200 and len((text or "").strip()) < 120:
        return "empty"
    return None


def _gh_token():
    path = os.path.join(os.path.expanduser("~"), ".git-credentials")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if "github.com" in line:
                    cred = line.strip().split("://", 1)[1].split("@", 1)[0]
                    return cred.split(":", 1)[1]
    except (OSError, IndexError, ValueError):
        return None
    return None


def _fc_key():
    return read_key("firecrawl")


def _tavily_key():
    return read_key("tavily")


SO_QUESTION = re.compile(r"https?://(?:www\.)?stackoverflow\.com/questions/(\d+)")
GITHUB_REPO = re.compile(
    r"https?://(?:www\.)?github\.com/([^/?#]+)/([^/?#]+)"
    r"(?:/(?:blob|raw)/([^/?#]+)/(.+?))?(?:[?#].*)?$"
)
NEXUS_MOD = re.compile(
    r"https?://(?:www\.)?nexusmods\.com/([^/?#]+)/mods/(\d+)")


def fetch_api(url, timeout):
    """Known domains publish content through APIs - use those, not a browser."""
    # GitHub repo/file
    gh = GITHUB_REPO.match(url)
    if gh:
        return _fetch_github(gh.group(1), gh.group(2), url, timeout,
                             branch=gh.group(3), path=gh.group(4))

    # Stack Overflow question
    so = SO_QUESTION.match(url)
    if so:
        return _fetch_se(so.group(1), url, timeout)

    # Nexus Mods mod page (needs nexus.key; free personal API key)
    nexus = NEXUS_MOD.match(url)
    if nexus:
        return _fetch_nexus(nexus.group(1), nexus.group(2), url, timeout)

    return None


def _nexus_key():
    return read_key("nexus")


def _fetch_nexus(game, mod_id, url, timeout):
    """Nexus v1 API: metadata + BBCode description. No key -> None (fall through)."""
    key = _nexus_key()
    if not key:
        return None
    started = time.time()
    headers = {"apikey": key, "User-Agent": "zcode-fetch",
               "Accept": "application/json"}

    def get(suffix):
        req = urllib.request.Request(
            f"https://api.nexusmods.com/v1/games/{game}/mods/{mod_id}{suffix}",
            headers=headers)
        with urllib_open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    try:
        info = get("")
    except Exception:
        return None

    lines = [
        f"{info.get('name')}  v{info.get('version')}  by {info.get('author')}",
        f"game: {info.get('game_name') or game}  "
        f"downloads: {info.get('mod_downloads')}  "
        f"endorsements: {info.get('endorsement_count')}",
        str(info.get("summary") or ""), "",
    ]
    try:
        desc = get(".json")
        lines.append((desc.get("description") or "")[:15000])
    except Exception:
        pass
    text = "\n".join(lines)
    if len(text) < 80:
        return None
    return {"url": url, "tier": 1, "status": 200, "html_len": len(text),
            "text_len": len(text), "title": str(info.get("name") or "")[:120],
            "blocked": None, "profile": "nexus-api",
            "ms": int((time.time() - started) * 1000), "text": text[:20000]}


def _fetch_github(owner, repo, url, timeout, branch=None, path=None):
    started = time.time()
    token = _gh_token()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "zcode-fetch"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def get(p):
        req = urllib.request.Request(p, headers=headers)
        with urllib_open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    if branch and path:
        try:
            raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
            with urllib_open(urllib.request.Request(raw, headers=headers),
                             timeout=timeout) as resp:
                file_text = resp.read().decode("utf-8", "replace")
            if len(file_text) > 80:
                return {"url": url, "tier": 1, "status": 200,
                        "html_len": len(file_text), "text_len": len(file_text),
                        "title": f"{owner}/{repo}/{path}"[:120], "blocked": None,
                        "profile": "github-raw",
                        "ms": int((time.time() - started) * 1000),
                        "text": file_text[:20000]}
        except Exception:
            pass

    try:
        meta = get(f"https://api.github.com/repos/{owner}/{repo}")
    except Exception:
        return None

    lines = [f"{meta.get('full_name')}  ★{meta.get('stargazers_count')}  "
             f"forks {meta.get('forks_count')}  issues {meta.get('open_issues_count')}",
             f"lang: {meta.get('language')}  license: {(meta.get('license') or {}).get('spdx_id')}  "
             f"pushed: {str(meta.get('pushed_at'))[:10]}",
             f"{(meta.get('description') or '').strip()}", ""]
    try:
        readme = get(f"https://api.github.com/repos/{owner}/{repo}/readme")
        content = base64.b64decode(readme.get("content") or "").decode("utf-8", "replace")
        lines.append(content[:8000])
    except Exception:
        pass

    text = "\n".join(lines)
    if len(text) < 120:
        return None
    return {"url": url, "tier": 1, "status": 200, "html_len": len(text),
            "text_len": len(text), "title": str(meta.get("full_name") or "")[:120],
            "blocked": None, "profile": "github-api",
            "ms": int((time.time() - started) * 1000), "text": text[:20000]}


def _fetch_se(qid, url, timeout):
    started = time.time()
    token = _gh_token()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "zcode-fetch"}
    try:
        api_url = f"https://api.stackexchange.com/2.3/questions/{qid}?site=stackoverflow&filter=withbody"
        req = urllib.request.Request(api_url, headers=headers)
        with urllib_open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None
    items = data.get("items") or []
    if not items:
        return None
    item = items[0]
    body_html = item.get("body") or ""
    parser = TextExtractor()
    parser.feed(body_html)
    text = parser.text()
    return {"url": url, "tier": 1, "status": 200, "html_len": len(body_html),
            "text_len": len(text), "title": unescape(item.get("title") or "")[:120],
            "blocked": None if text else "empty:se-api", "profile": "se-api",
            "ms": int((time.time() - started) * 1000), "text": text[:20000]}


def fetch_firecrawl(url, timeout):
    """Firecrawl scrape - its servers do the request, bypassing our IP's blocks.
    Never used for accelerated domains (github/nexusmods/mod.io): those go
    proxy-first or through the browser instead."""
    if is_accel(url):
        return None
    key = _fc_key()
    body = json.dumps({"url": url, "formats": ["markdown"], "onlyMainContent": True}).encode()
    req = urllib.request.Request("https://api.firecrawl.dev/v1/scrape",
                                 data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "zcode-fetch")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    started = time.time()
    try:
        with urllib_open(req, timeout=max(timeout, 50)) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None
    if not data.get("success", True):
        return None
    doc = data.get("data") or {}
    markdown = doc.get("markdown") or ""
    if len(markdown) < 200:
        return None
    meta = doc.get("metadata") or {}
    text = markdown[:20000]
    return {"url": url, "tier": 1, "status": 200, "html_len": len(markdown),
            "text_len": len(text), "title": str(meta.get("title") or "")[:120],
            "blocked": None, "profile": "firecrawl",
            "ms": int((time.time() - started) * 1000), "text": text}


def fetch_tier0(url, timeout):
    started = time.time()
    last_error = "unknown"
    attempts = [("chrome", None), ("chrome136", None), ("safari", None)]
    if is_accel(url):
        # accelerated domains: Watt proxy first (direct raw.githubusercontent
        # gets reset); proxy TLS uses the MITM CA -> verify=False
        attempts = ([("chrome", {"http": PROXY, "https": PROXY})] if PROXY else []) \
            + attempts
    elif PROXY:
        attempts.append(("chrome", {"http": PROXY, "https": PROXY}))
    for profile, proxies in attempts:
        try:
            kw = {"impersonate": profile, "timeout": timeout,
                  "allow_redirects": True,
                  "headers": {"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}}
            if proxies:
                kw["proxies"] = proxies
                kw["verify"] = False
            r = requests.get(url, **kw)
            html = r.text or ""
            parser = TextExtractor()
            parser.feed(html)
            text = parser.text()
            reason = looks_blocked(r.status_code, html, text)
            # a successful page through the MITM proxy can be a portal page -
            # treat tiny responses through the proxy as failures
            if proxies and len(html) < 3000:
                last_error = "proxy-portal"
                continue
            return {"url": url, "tier": 0, "status": r.status_code,
                    "html_len": len(html), "text_len": len(text),
                    "title": parser.title[:120], "blocked": reason,
                    "profile": profile,
                    "ms": int((time.time() - started) * 1000), "text": text[:20000]}
        except Exception as e:
            last_error = f"{type(e).__name__}"
            continue
    return {"url": url, "tier": 0, "status": 0, "html_len": 0, "text_len": 0,
            "title": "", "blocked": f"error:{last_error}", "profile": "-",
            "ms": int((time.time() - started) * 1000), "text": ""}


def fetch_tier2(url, timeout, deadline=None):
    """Camoufox browser - clears Cloudflare challenges that defeat curl_cffi."""
    started = time.time()
    ensure_camofox()
    uid = f"fetch{int(time.time() * 1000) % 1000000}"
    budget = max(timeout, 140)
    if deadline is not None:
        remaining = deadline - time.time() - 10
        if remaining < 25:
            return {"url": url, "tier": 2, "status": 0, "html_len": 0, "text_len": 0,
                    "title": "", "blocked": "skipped:deadline", "profile": "camoufox",
                    "ms": 0, "text": ""}
        budget = min(budget, int(remaining))

    created = _camofox_api("POST", "/tabs", {
        "url": url, "userId": uid, "sessionKey": "fetch", "locale": "zh-CN",
    }, timeout=budget)
    tab = created.get("tabId")
    if not tab:
        return {"url": url, "tier": 2, "status": 0, "html_len": 0, "text_len": 0,
                "title": "", "blocked": "error:no-tab", "profile": "camoufox",
                "ms": 0, "text": ""}

    best_text = ""
    best_url = url
    deadline_poll = time.time() + budget
    while time.time() < deadline_poll:
        time.sleep(3)
        snap = _camofox_api("GET", f"/tabs/{tab}/snapshot?userId={uid}", timeout=60)
        body = snap.get("snapshot") or ""
        if len(body) > len(best_text):
            best_text = body
            best_url = snap.get("url") or url
        if len(body) > 800 and "just a moment" not in body.lower()[:500]:
            break

    return {"url": url, "tier": 2, "status": 200 if best_text else 0,
            "html_len": len(best_text), "text_len": len(best_text),
            "title": str(best_url)[:120], "blocked": None if best_text else "empty:snapshot",
            "profile": "camoufox", "ms": int((time.time() - started) * 1000),
            "text": best_text[:20000]}


# ------------------------------------------------------------------ camofox client
CAMOFOX_PORT = 9377
_camofox_lock = threading.Lock()


def _camofox_api(method, path, payload=None, timeout=30):
    url = f"http://127.0.0.1:{CAMOFOX_PORT}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with DIRECT_OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _spawn_detached(cmd, cwd):
    """Try progressively lighter creation flags - sandboxed job objects may
    forbid CREATE_BREAKAWAY_FROM_JOB (WinError 5)."""
    flag_sets = [
        (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
         | subprocess.CREATE_BREAKAWAY_FROM_JOB),
        subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        subprocess.CREATE_NO_WINDOW,
        0,
    ]
    last = None
    for flags in flag_sets:
        try:
            subprocess.Popen(cmd, cwd=str(cwd), creationflags=flags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError as e:
            last = e
    raise last


def ensure_camofox(timeout=60):
    """Return True once the camofox HTTP service on 9377 answers /health."""
    with _camofox_lock:
        try:
            _camofox_api("GET", "/health", timeout=3)
            return True
        except Exception:
            pass
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        launcher = str(pythonw if pythonw.exists() else sys.executable)
        _spawn_detached([launcher, "-m", "webtools.browser.server"],
                        Path(__file__).parent.parent)
        waited = 0.0
        while waited < timeout:
            time.sleep(2)
            waited += 2
            try:
                _camofox_api("GET", "/health", timeout=3)
                return True
            except Exception:
                continue
        return False


# ------------------------------------------------------------------ orchestration
DOMAIN_LOCKS: dict[str, threading.Semaphore] = {}
_domain_guard = threading.Lock()
FC_SEM = threading.Semaphore(2)   # firecrawl relay concurrency
T2_SEM = threading.Semaphore(1)   # camoufox browser concurrency


def _domain_lock(url):
    host = urlparse(url).netloc.lower()
    with _domain_guard:
        if host not in DOMAIN_LOCKS:
            DOMAIN_LOCKS[host] = threading.Semaphore(2)
        return DOMAIN_LOCKS[host]


def fetch_one(url, timeout=25, force_browser=False, deadline=None,
              no_cache=False):
    """Best-effort page fetch: API shortcuts -> tier0 -> firecrawl -> browser."""
    refresh_proxy()
    result = None
    if not force_browser and not no_cache:
        cached = cache_read(url, "final", 24)
        if cached:
            return cached

    t0 = fc = t2 = None
    if not force_browser:
        api = fetch_api(url, timeout)
        if api and api.get("text_len", 0) >= 120:
            cache_write(url, "final", api)
            return api
        t0 = fetch_tier0(url, timeout)
        if not t0.get("blocked") and t0.get("text_len", 0) >= 120:
            cache_write(url, "final", t0)
            return t0

    with _domain_lock(url):
        # browser before relay (user order): Camoufox first, Firecrawl last
        if not force_browser:
            with T2_SEM:
                t2 = fetch_tier2(url, timeout, deadline=deadline)
            if t2.get("text_len", 0) >= 200:
                cache_write(url, "final", t2)
                return t2
        with FC_SEM:
            fc = fetch_firecrawl(url, timeout)
        if fc and fc.get("text_len", 0) >= 200:
            cache_write(url, "final", fc)
            return fc

    for cand in (api if not force_browser else None, t0, fc, t2):
        if cand and (result is None or cand.get("text_len", 0) > result.get("text_len", 0)):
            result = cand
    if result:
        return result
    return {"url": url, "tier": -1, "status": 0, "html_len": 0, "text_len": 0,
            "title": "", "blocked": "all-tiers-failed", "profile": "-", "ms": 0,
            "text": ""}


def _print_human(r):
    head = f"[tier {r.get('tier')} | {r.get('status')} | {r.get('ms')}ms | {r.get('profile')}]"
    flag = f" blocked={r['blocked']}" if r.get("blocked") else ""
    print(f"=== {r['url']} {head}{flag}")
    if r.get("title"):
        print(f"    title: {r['title']}")
    text = (r.get("text") or "").strip()
    if text:
        print(text[:900])
        if len(text) > 900:
            print(f"    ... (+{len(text) - 900} chars)")
    else:
        print("    (no text)")


def cli_fetch(urls, browser=False, timeout=25, concurrency=4, json_out=False,
              no_cache=False):
    """Shared entry used by webtools.cli and __main__."""
    deadline = time.time() + 260
    results = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = {ex.submit(fetch_one, u, timeout, browser, deadline,
                          no_cache): u
                for u in urls}
        for fut in as_completed(futs):
            u = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"url": u, "tier": -1, "status": 0, "html_len": 0, "text_len": 0,
                     "title": "", "blocked": f"error:{type(e).__name__}:{e}",
                     "profile": "-", "ms": 0, "text": ""}
            results.append(r)

    by_url = {}
    for r in results:
        by_url.setdefault(r["url"], r)
    ordered = [by_url.get(u) for u in urls]
    if json_out:
        for r in ordered:
            print(json.dumps(r, ensure_ascii=False))
    else:
        for r in ordered:
            _print_human(r)
    return ordered


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="webtools fetch (quick mode)")
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    cli_fetch(a.urls, a.browser, a.timeout, a.concurrency, a.json, a.no_cache)