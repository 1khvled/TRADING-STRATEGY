"""Recurring forward paper cycle, aligned to 4H bar closes.

Usage: python scripts/run_live.py
Runs forward_once + paper_futures_once every 4h (±1min buffer after bar close).
Keep this process alive; dashboard is rebuilt on each page load by serve_dashboard.py.
Stop with Ctrl+C (or close the console). Paper only, no real orders.
"""
import os, subprocess, sys, time
from datetime import datetime, timezone, timedelta

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
CMD_SPOT = [sys.executable, "scripts/forward_once.py", "--max-new", "2"]
CMD_FUT = [sys.executable, "scripts/paper_futures_once.py", "--lev", "3", "--max-new", "1"]
CD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}Z] {msg}", flush=True)

def run_cycle(tag):
    for cmd in (CMD_SPOT, CMD_FUT):
        try:
            r = subprocess.run(cmd, cwd=CD, capture_output=True, text=True, timeout=600)
            out = (r.stdout or "") + (r.stderr or "")
            lines = "\n".join(f"   | {l}" for l in out.splitlines() if l.strip())
            log(f"{tag} {' '.join(cmd[1:])} rc={r.returncode}\n{lines}")
        except Exception as e:
            log(f"{tag} {' '.join(cmd[1:])} ERROR {e}")

def align_to_4h_close(offset_sec=60):
    now = datetime.now(timezone.utc)
    nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=(4 - now.hour % 4) % 4)
    if (nxt - now).total_seconds() < offset_sec:
        nxt += timedelta(hours=4)
    nxt += timedelta(seconds=offset_sec)  # give Binance a minute to settle the bar
    wait = (nxt - now).total_seconds()
    return nxt, max(5, wait)

if __name__ == "__main__":
    log("livepaper loop started")
    run_cycle("INITIAL")
    while True:
        nxt, wait = align_to_4h_close()
        log(f"sleeping {wait:.0f}s -> next close {nxt.strftime('%H:%M:%S')}Z")
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            log("stopped by user")
            sys.exit(0)
        run_cycle("CYCLE")