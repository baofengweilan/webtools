"""webtools CLI - search / research / fetch / visit / status / doctor / serve."""
import argparse
import json
import sys


def _cmd_search(args):
    from .search import multi_search
    query = " ".join(args.query)
    providers = args.providers.split(",") if args.providers else None
    rows, meta = multi_search(query, limit=args.limit, providers=providers,
                              verbose=args.verbose, sites=args.sites)
    if args.json:
        print(json.dumps({"meta": meta, "results": rows}, ensure_ascii=False,
                         indent=1))
        return 0
    print(f"# {query}  ({meta['elapsed_s']}s; rows: {meta['provider_rows']}; "
          f"errors: {meta['provider_errors']})")
    for i, r in enumerate(rows, 1):
        print(f"\n{i}. {r['title']}\n   {r['url']}\n   [{'+'.join(r['sources'])}]"
              f"  {r['snippet'][:160]}")
    return 0


def _cmd_research(args):
    from .research import research
    query = " ".join(args.query)
    results, meta = research(query, top=args.top, limit=args.limit)
    if args.json:
        print(json.dumps({"meta": meta, "results": results}, ensure_ascii=False,
                         indent=1))
        return 0
    print(f"# {query} ({meta['total_elapsed_s']}s)")
    for i, r in enumerate(results, 1):
        print(f"\n{i}. {r['title']}\n   {r['url']}  [tier {r['fetch_tier']}, "
              f"{r['text_len']} chars]")
        if r.get("excerpt"):
            print("   " + r["excerpt"][:400].replace("\n", "\n   "))
    return 0


def _cmd_fetch(args):
    from .fetch import cli_fetch
    cli_fetch(args.urls, browser=args.browser, timeout=args.timeout,
              concurrency=args.concurrency, json_out=args.json,
              no_cache=args.no_cache)
    return 0


def _cmd_status(args):
    from .search import quota_state
    from .research import _load_state
    print(json.dumps({"quota": quota_state(),
                      "channels": _load_state()}, ensure_ascii=False, indent=1))
    return 0


def _cmd_visit(args):
    from .browser.client import (camofox_visit, camofox_act,
                                 camofox_snapshot, camofox_close_tab)
    snap = camofox_visit(args.url, wait_s=args.wait)
    tab = snap["tabId"]
    print(f"tabId={tab} url={snap.get('url')} title={snap.get('title')}")
    if args.act:
        params = {}
        if args.text:
            params["text"] = args.text
        if args.selector:
            params["selector"] = args.selector
        if args.ref:
            params["ref"] = args.ref
        if args.act == "press":
            params["key"] = args.key
        if args.act == "scroll":
            params["dy"] = args.dy
        camofox_act(tab, args.act, params)
        snap = camofox_snapshot(tab)
        print(f"after act url={snap.get('url')}")
    print(snap.get("snapshot") or "")
    return 0


def _cmd_doctor(args):
    from .doctor import run_doctor
    info = run_doctor()
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=1))
    else:
        print(f"python: {info['python']}")
        print(f"home:   {info['home']}")
        print(f"proxy:  {info.get('proxy')}")
        for k, v in info["checks"].items():
            print(f"  {k}: {v}")
    return 0


def _cmd_serve(args):
    from . import mcp_server
    mcp_server.main()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="webtools",
        description="Free-quota web search & fetch toolkit for AI agents")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="multi-provider web search")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--sites", default=None,
                   help="site-scoped search, e.g. nexus:<game>,modio:<game>,github")
    p.add_argument("--providers", default=None,
                   help="comma list: firecrawl,tavily,github,reddit")
    p.add_argument("--reddit", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=_cmd_search)

    p = sub.add_parser("research", help="search + fetch top pages -> digest")
    p.add_argument("query", nargs="+")
    p.add_argument("--top", type=int, default=4)
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_research)

    p = sub.add_parser("fetch", help="tiered page fetch")
    p.add_argument("urls", nargs="+")
    p.add_argument("--browser", action="store_true")
    p.add_argument("--timeout", type=int, default=25)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=_cmd_fetch)

    p = sub.add_parser("status", help="quota ledger + channel health")
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser("visit", help="open a page in the Camoufox browser")
    p.add_argument("url")
    p.add_argument("--wait", type=float, default=8)
    p.add_argument("--act", choices=["click", "type", "press", "scroll"])
    p.add_argument("--text")
    p.add_argument("--selector")
    p.add_argument("--ref")
    p.add_argument("--key", default="Enter")
    p.add_argument("--dy", type=int, default=600)
    p.set_defaults(func=_cmd_visit)

    p = sub.add_parser("doctor", help="environment self-check")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_doctor)

    p = sub.add_parser("serve", help="run the MCP stdio server")
    p.set_defaults(func=_cmd_serve)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())