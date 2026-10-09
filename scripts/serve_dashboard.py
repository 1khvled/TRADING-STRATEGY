"""Serve the dashboard on a local port. Fast + resilient by design.

- Serves the cached dashboard.html INSTANTLY (never waits on Binance).
- Rebuilds in a background thread, at most once per REBUILD_EVERY_S.
- If a rebuild fails, the last-good page keeps serving; the error goes to
  data/serve_dashboard.log (visible via /health).
- /health returns JSON: cache age, last rebuild result, uptime.

Usage: python scripts/serve_dashboard.py [--port 8000]
Open http://localhost:8000  (paper only, localhost)
"""
import sys, os, json, time, argparse, subprocess, threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "dashboard.html")
LOG = os.path.join(ROOT, "data", "serve_dashboard.log")
REBUILD_EVERY_S = 120
REBUILD_TIMEOUT_S = 150

STATE = {"started": time.time(), "last_ok": None, "last_try": None,
         "last_error": None, "builds_ok": 0, "builds_fail": 0,
         "rebuilding": False}
LOCK = threading.Lock()


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}\n"
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def rebuild(reason):
    with LOCK:
        if STATE["rebuilding"]:
            return False
        if STATE["last_try"] and time.time() - STATE["last_try"] < REBUILD_EVERY_S and reason == "stale":
            return False
        STATE["rebuilding"] = True
        STATE["last_try"] = time.time()

    def work():
        try:
            r = subprocess.run(
                [sys.executable, "scripts/build_dashboard.py", "--out", "dashboard.html"],
                cwd=ROOT, capture_output=True, text=True, timeout=REBUILD_TIMEOUT_S)
            with LOCK:
                if r.returncode == 0 and os.path.exists(OUT):
                    STATE["last_ok"] = time.time()
                    STATE["last_error"] = None
                    STATE["builds_ok"] += 1
                else:
                    STATE["last_error"] = (r.stderr or r.stdout or "unknown")[-300:]
                    STATE["builds_fail"] += 1
                    log(f"REBUILD FAIL rc={r.returncode}: {STATE['last_error']}")
        except Exception as e:  # noqa: BLE001 - must never kill the server thread
            with LOCK:
                STATE["last_error"] = str(e)[-300:]
                STATE["builds_fail"] += 1
            log(f"REBUILD EXC: {e}")
        finally:
            with LOCK:
                STATE["rebuilding"] = False

    threading.Thread(target=work, daemon=True).start()
    return True


PRICE_HOSTS = ("https://api.binance.com", "https://data-api.binance.vision")
PRICE_TTL_S = 20
PRICE_CACHE = {"at": 0.0, "px": {}}
PRICE_POLL_INTERVAL_S = 30


def fetch_prices(symbols):
    """Server-side last prices (this host reaches Binance fine).
    Results cached PRICE_TTL_S so bursts of page loads don't hammer the API."""
    syms = sorted({s for s in symbols if s})
    if not syms:
        return {}
    now = time.time()
    with LOCK:
        fresh = {s: p for s, p in PRICE_CACHE["px"].items()
                 if s in syms and now - PRICE_CACHE["at"] < PRICE_TTL_S}
    missing = [s for s in syms if s not in fresh]
    if missing:
        try:
            import requests
        except ImportError:
            return fresh
        fetched = {}

        def one(sym):
            for host in PRICE_HOSTS:
                try:
                    r = requests.get(host + "/api/v3/ticker/price",
                                     params={"symbol": sym}, timeout=5)
                    if r.ok:
                        return sym, float(r.json()["price"])
                except Exception:
                    continue
            return sym, None

        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(8, len(missing))) as ex:
                for sym, px in ex.map(one, missing):
                    if px is not None:
                        fetched[sym] = px
        except Exception as e:  # noqa: BLE001
            log(f"PRICE FETCH EXC: {e}")
        with LOCK:
            PRICE_CACHE["px"].update(fetched)
            PRICE_CACHE["at"] = now
        fresh.update(fetched)
    return fresh


def cache_age():
    try:
        return time.time() - os.path.getmtime(OUT)
    except OSError:
        return None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            with LOCK:
                st = dict(STATE)
            age = cache_age()
            self._send(200, "application/json", json.dumps({
                "ok": True, "uptime_s": round(time.time() - st["started"], 1),
                "cache_age_s": round(age, 1) if age is not None else None,
                "cache_exists": age is not None,
                "builds_ok": st["builds_ok"], "builds_fail": st["builds_fail"],
                "rebuilding": st["rebuilding"], "last_error": st["last_error"],
            }).encode())
            return
        if path == "/rebuild":
            rebuild("manual")
            self._send(202, "text/plain", b"rebuild queued; reload / in a few seconds")
            return
        if path == "/prices":
            from urllib.parse import urlparse, parse_qs
            try:
                qs = parse_qs(urlparse(self.path).query)
                syms = [s.strip().upper() for s in qs.get("symbols", [""])[0].split(",") if s.strip()]
                syms = syms[:25]
            except Exception:
                syms = []
            try:
                px = fetch_prices(syms)
                self._send(200, "application/json", json.dumps(
                    {"ok": True, "prices": px, "ts": time.time()}).encode())
            except Exception as e:  # noqa: BLE001
                log(f"PRICES EXC: {e}")
                self._send(500, "application/json", json.dumps(
                    {"ok": False, "error": str(e)[:200]}).encode())
            return
        if path not in ("/", "/dashboard.html", "/index.html"):
            self._send(404, "text/plain", b"not found")
            return
        age = cache_age()
        if age is None or age > REBUILD_EVERY_S:
            rebuild("stale")  # background; this request still gets the current file
        try:
            with open(OUT, "rb") as f:
                body = f.read()
        except OSError:
            self._send(503, "text/plain",
                       b"dashboard not built yet; try /rebuild then reload in ~30s")
            return
        self._send(200, "text/html; charset=utf-8", body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    log(f"server start port={a.port}")
    rebuild("manual")  # warm the cache in background right away
    try:
        import socket

        class DualStack(ThreadingHTTPServer):
            address_family = socket.AF_INET6
            daemon_threads = True

            def server_bind(self):
                # dual-stack: one socket serves both ::1 and 127.0.0.1,
                # so 'localhost' never stalls on IPv6->IPv4 fallback
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                super().server_bind()

        srv = DualStack(("::", a.port), H)
    except Exception as e:  # noqa: BLE001 - no IPv6 stack? fall back to IPv4
        log(f"dual-stack bind failed ({e}); using IPv4 only")
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"serving dashboard on http://localhost:{a.port}  (cached, auto-rebuild) ", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
