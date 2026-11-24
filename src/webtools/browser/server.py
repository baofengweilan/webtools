"""webtools.browser.server - HTTP service hosting the Camoufox anti-detect browser.

Port 9377. Launched detached (pythonw) by camofox_tools.ensure_camofox() or
bulk_fetch.ensure_camofox(); binds exclusively - a second copy exits quietly.

Endpoints (all JSON):
  GET    /health                         -> {"ok": true, "tabs": n, "browser": bool}
  POST   /tabs       {"url","userId","sessionKey","locale"} -> {"tabId": id, "url": ...}
  GET    /tabs                              -> {"tabs": [...]}
  GET    /tabs/{id}/snapshot?userId=...      -> {"tabId", "url", "title", "snapshot"}
  POST   /tabs/{id}/act {"action","params"}  -> action result
  DELETE /tabs/{id}?userId=...               -> {"closed": true}
  POST   /cookies/import {"cookies":[...]}   -> {"imported": n}
  GET    /cookies/export                     -> {"cookies": [...]}

Actions: navigate {url} · click {selector|text|ref} · type {selector|ref, text}
         press {key} · scroll {dy} · snapshot {} · eval {expression}
All browser work runs on one worker thread (playwright sync API is
thread-bound); HTTP handlers enqueue ops and block on the result future.
"""
import json
import logging
import os
import queue
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .._paths import home

STATE_DIR = home() / "logs"
LOG_PATH = STATE_DIR / "camofox_server.log"
PORT = int(os.environ.get("CAMOFOX_PORT", "9377"))

STATE_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")],
    force=True,
)
log = logging.getLogger("camofox")

_lock = threading.Lock()
_next_tab_id = 1


class BrowserWorker(threading.Thread):
    """Owns the playwright loop and every Camoufox object."""

    def __init__(self):
        super().__init__(daemon=True, name="camofox-browser")
        self.q: queue.Queue = queue.Queue()
        self.playwright = None
        self.browser = None
        self.context = None
        self.tabs: dict[int, dict] = {}   # tab_id -> {"page": Page, "userId": str}
        self.started_at = None

    # ---- op plumbing -------------------------------------------------
    def submit(self, fn, *args, timeout=90):
        fut: Future = Future()
        self.q.put((fn, args, fut))
        try:
            return fut.result(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("browser worker busy")

    def run(self):
        while True:
            fn, args, fut = self.q.get()
            try:
                fut.set_result(fn(*args))
            except Exception as e:
                log.warning("op failed: %s: %s\n%s", type(e).__name__, e,
                            traceback.format_exc(limit=4))
                fut.set_exception(e)

    # ---- lifecycle ---------------------------------------------------
    def _launch_locked(self):
        if self.browser is not None:
            return
        from camoufox.sync_api import Camoufox

        headless = os.environ.get("CAMOFOX_HEADLESS", "1") != "0"
        # A dead-but-configured system proxy must not hijack the browser:
        # if WinINET points at a proxy whose port is not answering (Watt
        # exited/crashed), force Firefox direct (network.proxy.type=0) so
        # pages render instead of showing "proxy server is refusing
        # connections".
        launch_kwargs = dict(headless=headless, humanize=True,
                             i_know_what_im_doing=True)
        try:
            import winreg
            from ..proxy_probe import proxy_alive
            keypath = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, keypath) as key:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            sys_proxy = (server if "://" in server else "http://" + server) \
                if (enabled and server) else None
            if sys_proxy and not proxy_alive(sys_proxy):
                launch_kwargs["prefs"] = {"network.proxy.type": 0}
                log.warning("system proxy %s is dead -> forcing browser direct",
                            sys_proxy)
        except Exception as exc:
            log.warning("proxy pre-check skipped: %s", exc)

        self._cm = Camoufox(**launch_kwargs)
        self.browser = self._cm.__enter__()
        self.context = self.browser.contexts[0] if self.browser.contexts \
            else self.browser.new_context()
        self.started_at = time.time()
        log.info("browser up (headless=%s)", headless)

    def ensure_browser(self):
        with _lock:
            if self.browser is not None:
                return True
            try:
                self._launch_locked()
                return True
            except Exception as e:
                log.error("launch failed: %s: %s", type(e).__name__, e)
                self.browser = None
                raise

    # ---- operations (always called on the worker thread) -------------
    def op_health(self):
        return {"ok": True, "browser": self.browser is not None,
                "tabs": len(self.tabs),
                "uptime_s": int(time.time() - self.started_at) if self.started_at else 0}

    def op_open(self, url, user_id="", locale="zh-CN", timeout_s=75):
        self.ensure_browser()
        page = self.context.new_page()
        global _next_tab_id
        with _lock:
            tab_id = _next_tab_id
            _next_tab_id += 1
        self.tabs[tab_id] = {"page": page, "userId": user_id}
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
        except Exception as e:
            log.info("goto %s -> %s: %s", url, type(e).__name__, e)
        return {"tabId": tab_id, "url": page.url}

    def op_snapshot(self, tab_id):
        entry = self.tabs.get(tab_id)
        if not entry:
            raise KeyError(f"no such tab: {tab_id}")
        page = entry["page"]
        snap = ""
        try:
            snap = page.locator("body").aria_snapshot()
        except Exception:
            try:
                snap = page.inner_text("body")
            except Exception as e:
                snap = f"(snapshot unavailable: {type(e).__name__})"
        return {"tabId": tab_id, "url": page.url, "title": page.title(),
                "snapshot": snap or ""}

    def op_act(self, tab_id, action, params):
        entry = self.tabs.get(tab_id)
        if not entry:
            raise KeyError(f"no such tab: {tab_id}")
        page = entry["page"]
        params = params or {}

        def loc():
            if params.get("ref"):
                return page.locator(f"aria-ref={params['ref']}")
            if params.get("selector"):
                return page.locator(params["selector"])
            if params.get("text"):
                return page.get_by_text(params["text"]).first
            raise ValueError("act needs ref / selector / text")

        if action == "navigate":
            page.goto(params["url"], wait_until="domcontentloaded", timeout=75000)
            return {"url": page.url}
        if action == "click":
            loc().click(timeout=15000)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            return {"url": page.url}
        if action == "type":
            box = loc()
            box.click(timeout=10000)
            box.fill(params.get("text", ""))
            return {"ok": True}
        if action == "press":
            page.keyboard.press(params.get("key", "Enter"))
            return {"ok": True}
        if action == "scroll":
            page.mouse.wheel(0, int(params.get("dy", 600)))
            return {"ok": True}
        if action == "snapshot":
            return self.op_snapshot(tab_id)
        if action == "eval":
            return {"value": page.evaluate(params.get("expression", "() => null"))}
        raise ValueError(f"unknown action: {action}")

    def op_close(self, tab_id):
        entry = self.tabs.pop(tab_id, None)
        if not entry:
            return {"closed": False}
        try:
            entry["page"].close()
        except Exception:
            pass
        return {"closed": True}

    def op_list(self, user_id=None):
        out = []
        for tid, entry in list(self.tabs.items()):
            if user_id and entry.get("userId") != user_id:
                continue
            page = entry["page"]
            try:
                title = page.title()
            except Exception:
                title = ""
            out.append({"tabId": tid, "url": page.url, "title": title,
                        "userId": entry.get("userId")})
        return {"tabs": out}

    def op_import_cookies(self, cookies):
        self.ensure_browser()
        norm = []
        for c in cookies or []:
            c = dict(c)
            c.setdefault("name", "")
            c.setdefault("value", "")
            if c.get("domain") and not c["domain"].startswith("."):
                c["domain"] = "." + c["domain"]
            if not c.get("url") and not c.get("domain"):
                continue
            norm.append(c)
        self.context.add_cookies(norm)
        return {"imported": len(norm)}

    def op_export_cookies(self):
        if self.browser is None:
            return {"cookies": []}
        return {"cookies": self.context.cookies()}


WORKER = BrowserWorker()
WORKER.start()


def _json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silence default stderr logging
        pass

    def _send(self, code, payload):
        body = _json_bytes(payload)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        qs = parse_qs(parsed.query)
        try:
            if parsed.path == "/health":
                return self._send(200, WORKER.submit(WORKER.op_health, timeout=5))
            if parts[:1] == ["tabs"]:
                if len(parts) == 1:
                    uid = (qs.get("userId") or [None])[0]
                    return self._send(200, WORKER.submit(WORKER.op_list, uid, timeout=15))
                if len(parts) == 3 and parts[2] == "snapshot":
                    tab_id = int(parts[1])
                    snap = WORKER.submit(WORKER.op_snapshot, tab_id, timeout=45)
                    return self._send(200, snap)
            if parsed.path == "/cookies/export":
                return self._send(200, WORKER.submit(WORKER.op_export_cookies, timeout=30))
            self._send(404, {"error": f"no route: {self.path}"})
        except KeyError as e:
            self._send(404, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        try:
            body = self._body()
            if parsed.path == "/tabs":
                out = WORKER.submit(
                    WORKER.op_open,
                    body.get("url") or "about:blank",
                    body.get("userId") or "",
                    body.get("locale") or "zh-CN",
                    int(body.get("timeout_s") or 75),
                    timeout=90)
                return self._send(200, out)
            if len(parts) == 3 and parts[2] == "act":
                tab_id = int(parts[1])
                out = WORKER.submit(WORKER.op_act, tab_id,
                                    body.get("action"), body.get("params"),
                                    timeout=90)
                return self._send(200, out)
            if parsed.path == "/cookies/import":
                out = WORKER.submit(WORKER.op_import_cookies,
                                    body.get("cookies"), timeout=60)
                return self._send(200, out)
            self._send(404, {"error": f"no route: {self.path}"})
        except KeyError as e:
            self._send(404, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        try:
            if len(parts) == 2 and parts[0] == "tabs":
                out = WORKER.submit(WORKER.op_close, int(parts[1]), timeout=20)
                return self._send(200, out)
            self._send(404, {"error": f"no route: {self.path}"})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def main():
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # port taken - an instance is already serving
        log.info("port %d busy, exiting", PORT)
        return
    log.info("camofox server listening on 127.0.0.1:%d", PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
