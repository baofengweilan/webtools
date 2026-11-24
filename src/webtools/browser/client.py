"""camofox_tools.py - client for the Camoufox browser service (port 9377).

Library:
  ensure_camofox()            -> start the service if not running
  camofox_visit(url, wait_s)  -> {"tabId", "url", "title", "snapshot"}
  camofox_act(tab_id, action, params) -> dict
  camofox_snapshot(tab_id)    -> dict
  camofox_close_tab(tab_id)   -> bool
  camofox_list_tabs()         -> list
  camofox_import_cookies(cookies) / camofox_export_cookies()

Actions: navigate {url} · click {selector|text|ref} · type {selector|ref, text}
         press {key} · scroll {dy} · snapshot · eval {expression}

CLI (each opens/closes its own tab):
  python camofox_tools.py visit "<url>" [--wait 8] [--act click --text "登录"]
  python camofox_tools.py cookies-export [--out cookies.json]
  python camofox_tools.py cookies-import cookies.json
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .._paths import home

PORT = int(os.environ.get("CAMOFOX_PORT", "9377"))
BASE = f"http://127.0.0.1:{PORT}"


# localhost must never go through the system proxy (WinINET registry proxy
# makes plain urlopen() 404 on 127.0.0.1)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _call(method, path, payload=None, timeout=90):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") \
        if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with OPENER.open(req, timeout=timeout) as resp:
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


def ensure_camofox(timeout=90):
    try:
        _call("GET", "/health", timeout=3)
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
            _call("GET", "/health", timeout=3)
            return True
        except Exception:
            continue
    raise RuntimeError("camofox server did not come up")


def camofox_visit(url, wait_s=8, user_id="tools"):
    """Open a tab, let it render, return the accessibility snapshot."""
    ensure_camofox()
    created = _call("POST", "/tabs", {"url": url, "userId": user_id,
                                      "locale": "zh-CN"}, timeout=90)
    tab_id = created.get("tabId")
    time.sleep(max(1.0, min(float(wait_s), 30)))
    snap = _call("GET", f"/tabs/{tab_id}/snapshot", timeout=45)
    snap.setdefault("tabId", tab_id)
    return snap


def camofox_snapshot(tab_id):
    return _call("GET", f"/tabs/{tab_id}/snapshot", timeout=45)


def camofox_act(tab_id, action, params=None, wait_s=4):
    out = _call("POST", f"/tabs/{tab_id}/act",
                {"action": action, "params": params or {}}, timeout=90)
    if wait_s and action in ("navigate", "click", "press", "scroll"):
        time.sleep(max(0.5, min(float(wait_s), 20)))
    return out


def camofox_close_tab(tab_id):
    return bool(_call("DELETE", f"/tabs/{tab_id}", timeout=20).get("closed"))


def camofox_list_tabs():
    return _call("GET", "/tabs", timeout=15).get("tabs", [])


def camofox_import_cookies(cookies):
    ensure_camofox()
    return _call("POST", "/cookies/import", {"cookies": cookies}, timeout=60)


def camofox_export_cookies():
    ensure_camofox()
    return _call("GET", "/cookies/export", timeout=60).get("cookies", [])


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="Camoufox browser interaction")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("visit")
    p.add_argument("url")
    p.add_argument("--wait", type=float, default=8)
    p.add_argument("--act", choices=["click", "type", "press", "scroll"])
    p.add_argument("--text"); p.add_argument("--selector"); p.add_argument("--ref")
    p.add_argument("--key", default="Enter"); p.add_argument("--dy", type=int, default=600)

    sub.add_parser("tabs")
    p = sub.add_parser("close"); p.add_argument("tab_id", type=int)
    p = sub.add_parser("cookies-export"); p.add_argument("--out", default=None)
    p = sub.add_parser("cookies-import"); p.add_argument("file")

    args = ap.parse_args()
    if args.cmd == "visit":
        snap = camofox_visit(args.url, wait_s=args.wait)
        tab = snap["tabId"]
        print(f"tabId={tab} url={snap.get('url')} title={snap.get('title')}")
        if args.act:
            params = {}
            if args.text: params["text"] = args.text
            if args.selector: params["selector"] = args.selector
            if args.ref: params["ref"] = args.ref
            if args.act == "type": params["text"] = args.text or ""
            if args.act == "press": params["key"] = args.key
            if args.act == "scroll": params["dy"] = args.dy
            camofox_act(tab, args.act, params)
            snap = camofox_snapshot(tab)
            print(f"after act url={snap.get('url')}")
        print(snap.get("snapshot") or "")
    elif args.cmd == "tabs":
        for t in camofox_list_tabs():
            print(f"{t['tabId']}\t{t.get('userId')}\t{t.get('url')}\t{t.get('title')}")
    elif args.cmd == "close":
        print(camofox_close_tab(args.tab_id))
    elif args.cmd == "cookies-export":
        cookies = camofox_export_cookies()
        out = args.out or str(home() / "state" / "camofox_cookies.json")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(cookies, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        print(f"{len(cookies)} cookies -> {out}")
    elif args.cmd == "cookies-import":
        cookies = json.loads(Path(args.file).read_text(encoding="utf-8"))
        print(camofox_import_cookies(cookies))


if __name__ == "__main__":
    _cli()
