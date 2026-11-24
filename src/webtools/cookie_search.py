"""cookie_search.py - search engines using your real browser's cookies.

Borrowed login state lifts rate limits and CAPTCHAs for bing / baidu / sogou.

Cookie file: JSON array of {name, value, domain, path?} - e.g. exported via
  python camofox_tools.py cookies-export --out cookies.json
or a devtools "Export cookies" extension.

Usage:
  python cookie_search.py --cookies cookies.json --engine bing "查询词"
  python cookie_search.py --engine baidu --limit 10 "查询词"   (no cookies)
"""
import argparse
import html as html_mod
import json
import re
import sys
from urllib.parse import quote_plus

from curl_cffi import requests as cffi_requests

ENGINES = {
    "bing": "https://www.bing.com/search?q={q}&count={n}",
    "baidu": "https://www.baidu.com/s?wd={q}&rn={n}",
    "sogou": "https://www.sogou.com/web?query={q}&num={n}",
}

RESULT_PATTERNS = {
    # each pattern must capture: url, title; bounded windows avoid backtracking
    "bing": re.compile(
        r'<li class="b_algo.{0,3000}?<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>'
        r'(.*?)</a>',
        re.S),
    "baidu": re.compile(
        r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S),
    "sogou": re.compile(
        r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S),
}

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def strip_html(text):
    return WS.sub(" ", TAG.sub("", html_mod.unescape(text or ""))).strip()


def load_cookies(path, engine):
    """Filter an exported cookie jar down to rows usable for the engine host."""
    host_hint = {"bing": ("bing.com", "bing"),
                 "baidu": ("baidu.com",),
                 "sogou": ("sogou.com",)}
    hints = host_hint[engine]
    try:
        raw = json.loads(open(path, encoding="utf-8").read())
    except OSError:
        print(f"(cookie file {path} not readable - searching without cookies)",
              file=sys.stderr)
        return ""
    rows = []
    for c in raw if isinstance(raw, list) else raw.get("cookies", []):
        domain = str(c.get("domain") or "")
        if not any(h in domain for h in hints):
            continue
        rows.append(f"{c.get('name')}={c.get('value')}")
    return "; ".join(rows)


def search(engine, query, limit=10, cookie_file=None):
    url = ENGINES[engine].format(q=quote_plus(query), n=limit)
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/136.0.0.0 Safari/537.36"),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    cookies = load_cookies(cookie_file, engine) if cookie_file else ""
    if cookies:
        headers["Cookie"] = cookies
    r = cffi_requests.get(url, headers=headers, timeout=20,
                          impersonate="chrome", allow_redirects=True)
    if r.status_code != 200:
        raise RuntimeError(f"{engine} HTTP {r.status_code}")
    html = r.text
    if "百度安全验证" in html or "wappass.baidu.com" in html[:2000]:
        raise RuntimeError("baidu captcha wall (cookies missing or stale)")
    if "curated by human" in html.lower() or len(html) < 3000:
        raise RuntimeError(f"{engine} challenge/empty page")

    rows = []
    seen = set()
    for m in RESULT_PATTERNS[engine].finditer(html):
        url_m, title_m = m.group(1), strip_html(m.group(2))
        if url_m.startswith("/link?url=") or "baidu.com/link" in url_m:
            continue  # baidu redirect wrappers - useless without resolution
        if url_m in seen or not title_m:
            continue
        seen.add(url_m)
        if not url_m.startswith("http"):
            url_m = "https://" + url_m.lstrip("/")
        rows.append({"url": url_m, "title": title_m[:200], "source": engine})
        if len(rows) >= limit:
            break
    return rows


def main():
    ap = argparse.ArgumentParser(description="Cookie-backed search")
    ap.add_argument("query", nargs="+")
    ap.add_argument("--engine", default="bing", choices=list(ENGINES))
    ap.add_argument("--cookies", default=None, help="cookie JSON export path")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    query = " ".join(args.query)

    rows = search(args.engine, query, args.limit, args.cookies)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print(f"# {args.engine}: {query}  ({len(rows)} results)")
    for i, r in enumerate(rows, 1):
        print(f"{i}. {r['title']}\n   {r['url']}")
    if not rows:
        print("(no results parsed - try another engine or refresh cookies)")


if __name__ == "__main__":
    main()
