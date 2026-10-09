"""Build a fully self-contained dashboard (single HTML file, zero server logic).

Usage: python scripts/build_dashboard.py [--out dashboard.html]

What gets baked in: book cash/positions/closed/fills/equity snapshots,
backtest rows + R-multiples + cumulative P&L, Monte Carlo summaries.
Live prices are fetched by the BROWSER straight from Binance public API
(CORS-enabled), polled every 30s — so the page works even via file://
and page loads never touch the network server-side. Builder ALSO bakes
a price snapshot, so cards render with real marks even if the browser
can't reach Binance at all. Rebuild to refresh on-chain state.
"""
import sys, os, json, glob, html, sqlite3, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from datetime import datetime, timezone

BOOKS = [
    ("fwd", "data/forward.db", "Forward", "spot"),
    ("spot", "data/paper.db", "Spot paper", "spot"),
    ("fut", "data/futures_paper.db", "Futures 3x", "fut"),
]
START_EQ = None  # resolved from src.config at build time (single source of truth)
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.config import PAPER_START_EQUITY as START_EQ
except Exception:
    START_EQ = 1000.0


def qdb(path):
    out = {"positions": [], "trades": [], "equity": []}
    if not os.path.exists(path):
        return out
    c = sqlite3.connect(path)
    try:
        tabs = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in out:
            if t not in tabs:
                continue
            try:
                desc = [d[0] for d in c.execute('SELECT * FROM "%s" LIMIT 1' % t).description]
            except Exception:
                continue
            out[t] = [dict(zip(desc, r)) for r in c.execute('SELECT * FROM "%s" ORDER BY 1' % t).fetchall()]
    finally:
        c.close()
    return out


def f(x, default=0.0):
    try:
        v = float(x)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def risk_of(p):
    if p.get("risk_usd"):
        return f(p["risk_usd"])
    atr, qty = p.get("entry_atr"), f(p.get("qty"))
    return f(atr) * 2.5 * qty if atr else 0.0


PRICE_HOSTS = ("https://api.binance.com", "https://data-api.binance.vision")


def fetch_prices(symbols):
    """Server-side last prices (this host reaches Binance reliably).
    Concurrent so one slow symbol can't stall the build."""
    syms = sorted({s for s in symbols if s})
    if not syms:
        return {}
    try:
        import requests
    except ImportError:
        return {}

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

    out = {}
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(syms))) as ex:
            for sym, px in ex.map(one, syms):
                if px is not None:
                    out[sym] = px
    except Exception:
        pass
    return out


def latest(pattern):
    c = sorted(glob.glob(pattern))
    return c[-1] if c else None


def load_json(p):
    if not p or not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def snap_book(db):
    opens, closed, fills, snaps = [], [], [], []
    for p in db["positions"]:
        base = {
            "symbol": p.get("symbol"), "side": p.get("side", "LONG"),
            "entry": f(p.get("entry_price")), "qty": f(p.get("qty")),
            "notional": f(p.get("notional")), "stop": f(p.get("stop")),
            "tp": f(p.get("take_profit")), "score": int(p.get("entry_score") or 0),
            "atr": f(p.get("entry_atr")), "risk": risk_of(p),
            "t": str(p.get("entry_time") or ""),
        }
        if p.get("status") == "OPEN":
            base["liq"] = f(p.get("liq_price")) if p.get("liq_price") else None
            base["lev"] = p.get("leverage")
            opens.append(base)
        elif p.get("status") == "CLOSED":
            rk = risk_of(p)
            pnl = f(p.get("pnl"))
            base.update({"exit": f(p.get("exit_price")), "pnl": pnl,
                         "R": (pnl / rk) if rk else 0.0,
                         "reason": str(p.get("exit_reason") or ""),
                         "exit_t": str(p.get("exit_time") or "")})
            closed.append(base)
    for t in db["trades"]:
        fills.append({"t": str(t.get("time") or ""), "symbol": t.get("symbol"),
                      "side": t.get("side"), "qty": f(t.get("qty")),
                      "price": f(t.get("price")), "fee": f(t.get("fee")),
                      "note": str(t.get("note") or "")})
    for e in db["equity"]:
        try:
            snaps.append([str(e.get("time")), f(e.get("equity"))])
        except Exception:
            pass
    cash = snaps[-1][1] if snaps else START_EQ
    return {"cash": cash, "open": opens, "closed": closed,
            "fills": fills[-60:], "snaps": snaps[-120:]}


def snap_backtest(path):
    d = load_json(path) or {}
    rows = d.get("rows", []) or []
    trades = [t for t in (d.get("trades", []) or []) if isinstance(t.get("pnl"), (int, float))]
    trades.sort(key=lambda t: str(t.get("exit_time", "")))
    cum, run = [], 0.0
    for t in trades:
        run += float(t["pnl"])
        cum.append(round(run, 2))
    step = max(1, len(cum) // 400)
    gw = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    R = [float(t.get("R", 0)) for t in trades]
    wins = sum(1 for t in trades if t["pnl"] > 0)
    reasons = {}
    for t in trades:
        reasons[t.get("reason", "?")] = reasons.get(t.get("reason", "?"), 0) + 1
    liq = sum(1 for t in trades if t.get("reason") == "liquidation")
    return {
        "file": os.path.basename(path) if path else None,
        "n": len(trades), "win": (wins / len(trades)) if trades else 0.0,
        "pf": (gw / gl) if gl > 0 else 0.0,
        "expR": (sum(R) / len(R)) if R else 0.0,
        "gross_win": gw, "gross_loss": gl,
        "rows": [{"s": r.get("symbol"), "ret": round(f(r.get("return_pct")), 2),
                  "n": int(r.get("n_trades", 0)), "win": round(f(r.get("win_rate")) * 100),
                  "pf": (None if r.get("profit_factor") in (float("inf"), float("-inf")) else round(f(r.get("profit_factor")), 2)),
                  "dd": round(f(r.get("max_dd")), 1),
                  "liq": int(r.get("liquidations", 0))} for r in rows],
        "cum": cum[::step], "R": [round(x, 3) for x in R],
        "reasons": reasons, "liq": liq,
        "avg_bars": round(sum(t.get("bars", 0) for t in trades) / len(trades), 1) if trades else 0,
    }


def snap_mc(path):
    d = load_json(path) or {}
    if not d:
        return None
    b = d.get("base", {}) or {}
    bs = d.get("bootstrap", {}) or {}
    fe = bs.get("final_equity", {}) or {}
    dd = bs.get("max_dd_pct", {}) or {}
    wf = b.get("walkforward", {}) or {}
    r2 = lambda v: round(float(v or 0), 2)
    return {
        "file": os.path.basename(path), "n": int(b.get("n", 0)),
        "win": round(float(b.get("win_rate", 0)) * 100, 1),
        "pf": r2(b.get("profit_factor")), "expR": r2(b.get("expectancy_R")),
        "sharpe": r2(b.get("sharpe_per_trade")),
        "med": fe.get("median", 0), "p5": fe.get("p5", 0), "p95": fe.get("p95", 0),
        "dd_med": abs(round(float(dd.get("median", 0)), 1)),
        "dd_p95": abs(round(float(dd.get("p95", 0)), 1)),
        "pprofit": round(float(bs.get("prob_profit", 0)) * 100, 1),
        "ruin": round(float(bs.get("prob_ruin_30dd", 0)) * 100, 1),
        "wf1": r2(wf.get("pf1")), "wf2": r2(wf.get("pf2")),
        "sims": int(d.get("sims", 0)), "risk": float(d.get("risk_pct", 0.01)) * 100,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dashboard.html")
    args = ap.parse_args()

    books = {}
    for key, path, label, kind in BOOKS:
        db = qdb(path)
        books[key] = {"label": label, "kind": kind, **snap_book(db)}

    # Bake live prices at build time: the browser may be unable to reach
    # Binance at all, so the page must never depend on client-side fetch.
    open_syms = [p["symbol"] for b in books.values() for p in b["open"]]
    baked_px = fetch_prices(open_syms)
    now = datetime.now(timezone.utc)

    bt_path = max(
        ([p for p in glob.glob("data/backtest_*.json") if "futures" not in p] or [None]),
        key=lambda p: os.path.getsize(p) if p and os.path.exists(p) else -1)
    fb_path = latest("data/futures_backtest_*.json")
    snap = {
        "meta": {"built": now.strftime("%Y-%m-%d %H:%M UTC"),
                 "start": START_EQ},
        "px": baked_px,
        "px_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "books": books,
        "spot": snap_backtest(bt_path),
        "fut": snap_backtest(fb_path),
        "mc": snap_mc(latest("data/montecarlo_*.json")),
        "mc_fut": snap_mc(latest("data/*montecarlo_futures*.json")),
    }

    doc = TEMPLATE.replace("__SNAPSHOT__", json.dumps(snap, allow_nan=False))
    doc = doc.replace("__BUILT__", html.escape(snap["meta"]["built"]))
    open(args.out, "w", encoding="utf-8").write(doc)
    n_open = sum(len(b["open"]) for b in books.values())
    n_cl = sum(len(b["closed"]) for b in books.values())
    print(f"wrote {args.out} ({os.path.getsize(args.out)} bytes) · "
          f"{n_open} open / {n_cl} closed · snapshot {len(json.dumps(snap)) // 1024} KB")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alt Swing — paper dashboard</title>
<style>
:root{
  --bg:#0a0e15; --panel:#101623; --panel2:#151c2b; --line:#212a3d;
  --txt:#e9eef8; --dim:#8d99ae; --faint:#525c72;
  --green:#34d399; --red:#f87171; --teal:#2dd4bf; --violet:#a78bfa; --amber:#fbbf24;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;background:var(--bg);
  color:var(--txt);min-height:100vh;padding:26px 22px 70px;font-size:14px;line-height:1.5;
  -webkit-font-smoothing:antialiased}
body::before{content:"";position:fixed;inset:0;pointer-events:none;
  background:radial-gradient(900px 420px at 12% -10%,rgba(45,212,191,.07),transparent),
             radial-gradient(760px 420px at 92% 4%,rgba(167,139,250,.07),transparent)}
.wrap{max-width:1140px;margin:0 auto;position:relative}
header.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;flex-wrap:wrap;gap:10px}
.brand{display:flex;align-items:center;gap:10px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--teal);box-shadow:0 0 12px var(--teal)}
h1{font-size:16px;font-weight:600;letter-spacing:.3px}
.head-meta{display:flex;gap:10px;align-items:center;font-size:12px;color:var(--dim);flex-wrap:wrap}
.pill{border:1px solid var(--line);background:var(--panel);padding:5px 11px;border-radius:999px;font-size:11px;white-space:nowrap}
.pill.live-ok{border-color:rgba(52,211,153,.4);color:var(--green)}
.pill.live-bad{border-color:rgba(248,113,113,.4);color:var(--red)}
nav.tabs{position:sticky;top:0;z-index:20;display:flex;gap:6px;margin-bottom:20px;padding:10px 0;
  background:linear-gradient(var(--bg) 82%,transparent)}
.tab{cursor:pointer;font-size:12.5px;color:var(--dim);padding:8px 15px;border-radius:10px;
  border:1px solid var(--line);background:var(--panel);user-select:none;font-family:inherit}
.tab b{color:var(--txt);font-weight:600;margin-left:6px;font-size:11px;background:rgba(255,255,255,.07);
  padding:1px 7px;border-radius:99px}
.tab.on{background:rgba(45,212,191,.10);border-color:rgba(45,212,191,.4);color:var(--teal)}
.tab.on b{background:rgba(45,212,191,.15);color:var(--teal)}
.view{display:none}.view.on{display:block}
.hero{background:linear-gradient(160deg,var(--panel2),var(--panel));border:1px solid var(--line);
  border-radius:18px;padding:22px 24px;margin-bottom:20px;display:grid;
  grid-template-columns:minmax(230px,.85fr) 1.15fr;gap:22px}
@media(max-width:860px){.hero{grid-template-columns:1fr}}
.hero-label{font-size:10.5px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);margin-bottom:5px}
.hero-val{font-size:36px;font-weight:650;letter-spacing:-.6px;font-variant-numeric:tabular-nums;line-height:1.1}
.hero-sub{font-size:12px;color:var(--dim);margin-top:7px}
.hero-sub b{color:var(--txt);font-weight:600}
.hgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(115px,1fr));gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:14px;overflow:hidden}
.hcell{background:var(--panel);padding:11px 12px}
.hcell label{display:block;font-size:10px;text-transform:uppercase;letter-spacing:1.1px;color:var(--dim);margin-bottom:3px}
.hcell b{font-size:16px;font-weight:650;font-variant-numeric:tabular-nums}
.hcell i{display:block;font-style:normal;font-size:10.5px;color:var(--faint);margin-top:1px}
section{margin-bottom:24px}
.sec-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px;gap:10px;flex-wrap:wrap}
h2{font-size:11px;text-transform:uppercase;letter-spacing:1.4px;color:var(--dim);font-weight:600}
.sec-note{font-size:11.5px;color:var(--faint)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:17px 19px}
.bookline{display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
.stat-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:13px;overflow:hidden;margin-bottom:13px}
.stat{background:var(--panel);padding:11px 12px}
.stat label{display:block;font-size:10px;text-transform:uppercase;letter-spacing:1.1px;color:var(--dim);margin-bottom:3px}
.stat b{font-size:15.5px;font-weight:650;font-variant-numeric:tabular-nums}
.stat i{display:block;font-style:normal;font-size:10.5px;color:var(--faint);margin-top:1px}
.pos-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(295px,1fr));gap:12px}
.pos-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px;position:relative}
.pos-card.stale{opacity:.75}
.pos-top{display:flex;align-items:center;gap:7px;margin-bottom:7px;flex-wrap:wrap}
.pos-sym{font-weight:650;font-size:14.5px;letter-spacing:.2px}
.pos-px{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums;margin-bottom:9px}
.pos-px .pct{font-size:12.5px;font-weight:600;margin-left:7px;opacity:.8}
.kv-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px 14px;font-size:12px}
.pos-meta{display:flex;gap:11px;flex-wrap:wrap;margin-top:9px;padding-top:8px;border-top:1px solid var(--line);font-size:11px;color:var(--dim)}
.track{position:relative;height:4px;border-radius:3px;background:rgba(255,255,255,.07);margin:0 0 11px}
.track-fill{position:absolute;left:0;top:0;height:100%;border-radius:3px;background:var(--green);opacity:.85}
.track-fill.neg{background:var(--red)}
.track-mark{position:absolute;top:-3px;width:1px;height:10px;background:rgba(255,255,255,.4)}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:10.5px;padding:3px 8px;border-radius:7px;
  background:rgba(255,255,255,.04);border:1px solid var(--line);color:var(--dim);white-space:nowrap}
.chip b{color:var(--txt);font-weight:600}
.chip-spot{border-color:rgba(45,212,191,.3);color:var(--teal);background:rgba(45,212,191,.06)}
.chip-fut{border-color:rgba(167,139,250,.3);color:var(--violet);background:rgba(167,139,250,.07)}
.kv{color:var(--dim)} .kv b{color:var(--txt);font-weight:600;font-variant-numeric:tabular-nums}
.kv i{font-style:normal;font-size:11px}
.empty{color:var(--faint);font-size:12.5px;padding:8px 2px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);font-weight:600;
  padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid rgba(255,255,255,.03);font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:rgba(255,255,255,.018)}
.tbl-scroll{max-height:360px;overflow:auto}
.num{text-align:right}.muted{color:var(--dim)}.faint{color:var(--faint)}
.pos{color:var(--green)}.neg{color:var(--red)}
.sym{font-weight:600}
canvas{width:100%;height:190px;display:block;background:#0c1119;border-radius:10px;border:1px solid var(--line)}
.r-hist{display:flex;align-items:flex-end;gap:8px;height:104px}
.rb{flex:1;text-align:center;min-width:0}
.rb-bar{border-radius:5px 5px 0 0;background:linear-gradient(180deg,#34d399,#10b981);opacity:.8}
.rb-bar.neg{background:linear-gradient(180deg,#f87171,#ef4444)}
.rb span{display:block;font-size:9.5px;color:var(--dim);margin-top:4px;white-space:nowrap}
.rb b{font-size:11px;color:var(--txt)}
.mc-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}
.mc-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.mc-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:11px;font-size:13px;font-weight:600;gap:8px;flex-wrap:wrap}
.mc-grid{display:grid;grid-template-columns:1fr 1fr;gap:11px 16px}
.mc-grid label{display:block;font-size:10px;text-transform:uppercase;letter-spacing:1.1px;color:var(--dim)}
.mc-grid b{font-size:15.5px;font-weight:650;font-variant-numeric:tabular-nums}
.mc-grid i{display:block;font-style:normal;font-size:10.5px;color:var(--faint)}
details{border-top:1px solid var(--line);margin-top:13px;padding-top:9px;font-size:12.5px;color:var(--dim)}
details summary{cursor:pointer;font-size:11.5px;color:var(--dim);list-style:none}
details summary::before{content:"▸ ";color:var(--faint)}
details[open] summary::before{content:"▾ "}
details summary:hover{color:var(--txt)}
footer{margin-top:30px;font-size:11.5px;color:var(--faint);display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:var(--dim);
  background:rgba(255,255,255,.04);padding:2px 6px;border-radius:5px}
.note{font-size:11.5px;color:var(--faint);line-height:1.7}
.btn{cursor:pointer;font-family:inherit;font-size:11.5px;color:var(--teal);padding:5px 12px;border-radius:9px;
  border:1px solid rgba(45,212,191,.35);background:rgba(45,212,191,.07)}
.btn:hover{background:rgba(45,212,191,.14)}
.expect{display:flex;gap:18px;flex-wrap:wrap;font-size:12.5px;margin-bottom:12px;
  padding:10px 12px;background:rgba(255,255,255,.02);border:1px solid var(--line);border-radius:11px}
</style>
</head>
<body>
<div class="wrap">
<header class="top">
  <div class="brand"><span class="dot"></span><h1>Alt Swing</h1></div>
  <div class="head-meta">
    <span class="pill">paper only</span>
    <span class="pill" id="live-pill">prices: connecting…</span>
    <span>snapshot __BUILT__</span>
  </div>
</header>

<nav class="tabs" id="nav"></nav>

<div class="view on" id="v-overview">
  <div class="hero">
    <div>
      <div class="hero-label" id="hero-label">Live paper</div>
      <div class="hero-val" id="hero-eq">—</div>
      <div class="hero-sub" id="hero-sub">—</div>
      <div class="note" style="margin-top:8px">equity = cash + mark-to-market at live price</div>
    </div>
    <div class="hgrid" id="hero-books"></div>
  </div>
  <section>
    <div class="sec-head"><h2>Per-book stats</h2>
      <span class="sec-note">forward + futures are the live books · equity = cash + MTM</span></div>
    <div class="panel tbl-scroll" id="perbook"></div>
  </section>
  <section>
    <div class="sec-head"><h2>Open positions</h2>
      <span class="sec-note"><span id="price-age">live prices: —</span> · <button class="btn" id="btn-px">refresh prices</button></span></div>
    <div class="pos-grid" id="positions"></div>
  </section>
  <section>
    <div class="sec-head"><h2>Closed trades</h2><span class="sec-note">all books · newest first</span></div>
    <div class="panel"><div id="closed-mini"></div></div>
  </section>
</div>

<div class="view" id="v-forward">
  <section>
    <div class="sec-head"><h2>Forward test — live vs backtest</h2><span class="sec-note">forward.db closed trades only</span></div>
    <div class="panel"><div class="expect" id="expect"></div><div id="fwd-table"></div></div>
  </section>
  <section>
    <div class="sec-head"><h2>R-multiple distribution (forward)</h2></div>
    <div class="panel"><div class="r-hist" id="r-hist"></div></div>
  </section>
  <section>
    <div class="sec-head"><h2>Forward cash snapshots</h2><span class="sec-note">cash written by each paper cycle</span></div>
    <div class="panel"><canvas id="ch-fwd"></canvas><div style="height:12px"></div><div class="tbl-scroll" id="fwd-snaps"></div></div>
  </section>
  <section>
    <div class="sec-head"><h2>All fills (latest 60)</h2><span class="sec-note">every fill, all books</span></div>
    <div class="panel tbl-scroll" id="fills"></div>
  </section>
</div>

<div class="view" id="v-backtest">
  <section>
    <div class="sec-head"><h2>Spot backtest</h2><span class="sec-note" id="bt-file"></span></div>
    <div class="panel">
      <div class="stat-row" id="bt-stats" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:13px;overflow:hidden;margin-bottom:13px"></div>
      <canvas id="ch-bt"></canvas>
      <details><summary id="bt-per">per-symbol</summary><div class="tbl-scroll" style="margin-top:9px" id="bt-syms"></div></details>
      <details><summary>method &amp; diagnostics</summary><div style="margin-top:9px" id="bt-method"></div></details>
    </div>
  </section>
  <section>
    <div class="sec-head"><h2>Futures 3x backtest</h2><span class="sec-note" id="btf-file"></span></div>
    <div class="panel">
      <div id="btf-stats" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:13px;overflow:hidden;margin-bottom:13px"></div>
      <div id="btf-rows"></div>
    </div>
  </section>
</div>

<div class="view" id="v-montecarlo">
  <section>
    <div class="sec-head"><h2>Monte Carlo</h2><span class="sec-note">trade bootstrap · compounding · ignores overlap</span></div>
    <div class="mc-row" id="mc-row"></div>
  </section>
</div>

<footer><span>rebuild snapshot: <code>python scripts/build_dashboard.py</code> · prices baked at build, refreshed live when reachable · page auto-reloads every 3 min</span>
<span>paper only — no live orders</span></footer>
</div>

<script>
"use strict";
const SNAP = __SNAPSHOT__;
const START = SNAP.meta.start;
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fm = (v, d=0) => Number(v||0).toLocaleString("en-US",{minimumFractionDigits:d,maximumFractionDigits:d});
const fmg = v => { v=Number(v||0); const a=Math.abs(v);
  if (a>=1000) return v.toLocaleString("en-US",{maximumFractionDigits:0});
  if (a>=10) return v.toFixed(2); if (a>=1) return v.toFixed(3); return v.toPrecision(3); };
const pc = v => (v>=0?"+":"") + v.toFixed(1) + "%";
const ago = iso => { const t=new Date(iso).getTime(); if(!t) return "—";
  const h=(Date.now()-t)/36e5; if(h<1) return Math.max(1,Math.round(h*60))+" min";
  if(h<48) return h.toFixed(1)+"h"; return (h/24).toFixed(1)+"d"; };

/* ---------- prices: baked snapshot first, then live refresh ----------
   Baked prices (fetched by the builder, which reaches Binance reliably)
   render immediately, so cards NEVER show "no live price" when the bake
   succeeded. Browser refresh via same-origin /prices, then direct. */
const HOSTS = ["https://api.binance.com","https://data-api.binance.vision"];
const live = { px:{}, at:0, ok:null, via:"" };
(function initBaked(){
  const baked = SNAP.px || {};
  for(const s of Object.keys(baked)) live.px[s]=baked[s];
  if(Object.keys(baked).length){
    // px_at is already "...Z"; parse directly
    live.at = Date.parse(SNAP.px_at) || Date.now();
    live.ok = true; live.via = "snapshot";
  }
})();
async function fetchPx(sym, host){
  const r = await fetch(host+"/api/v3/ticker/price?symbol="+sym);
  if(!r.ok) throw 0;
  return parseFloat((await r.json()).price);
}
async function refreshPrices(){
  const syms = [...new Set(Object.values(SNAP.books).flatMap(b=>b.open.map(p=>p.symbol)))];
  if(!syms.length){ setPill(true, "no open positions"); return; }
  const errs=[];
  let ok=0, via="";
  // 1) same-origin proxy (no CORS, proven server-side path) — only works over http(s)
  if(location.protocol.startsWith("http")){
    try{
      const r = await fetch("/prices?symbols="+syms.join(","));
      if(r.ok){
        const j = await r.json();
        for(const s of syms){ if(j.prices && j.prices[s]!=null){ live.px[s]=j.prices[s]; ok++; } }
        if(ok) via="server";
        else errs.push("proxy: empty");
      } else errs.push("proxy: HTTP "+r.status);
    }catch(e){ errs.push("proxy: "+(e && e.message ? e.message : e)); }
  } else {
    errs.push("proxy: n/a (page opened as file — use http://127.0.0.1:8000)");
  }
  // 2) direct browser -> Binance for any missing symbols
  if(ok < syms.length){
    await Promise.all(syms.filter(s=>live.px[s]==null).map(async s=>{
      for(const h of HOSTS){ try{ live.px[s]=await fetchPx(s,h); ok++; break; }catch(e){ errs.push("direct "+s+": "+(e&&e.message?e.message:e)); } }
    }));
    if(ok && !via) via="browser";
  }
  live.at = Date.now(); live.ok = ok>0; live.via = via;
  setPill(live.ok, live.ok ? ("live ("+via+") · "+ago(new Date(live.at).toISOString())+" ago") : "feed down ("+ok+"/"+syms.length+")");
  if(!live.ok){
    $("price-age").textContent = "price errors: "+errs.slice(0,3).join(" · ");
    $("price-age").title = errs.join("\n");
  }
  renderPositions(); renderHero(); renderBooks();
}
function setPill(ok, txt){
  const el=$("live-pill"); el.textContent="prices: "+txt;
  el.className="pill "+(ok==null?"":ok?"live-ok":"live-bad");
}
setInterval(refreshPrices, 30000);
setInterval(()=>{ if(live.at) $("price-age").textContent =
  "live prices: "+ago(new Date(live.at).toISOString())+" old"; }, 5000);
$("btn-px").addEventListener("click", refreshPrices);

/* ---------- books / equity ---------- */
function bookEq(b){
  let mv=0, un=0;
  for(const p of b.open){
    const cur = live.px[p.symbol];
    const val = cur ? cur*p.qty : p.notional;
    mv += val;
    if(cur) un += (cur-p.entry)*p.qty;
  }
  return { eq: b.cash+mv, mv, un,
    real: b.closed.reduce((s,c)=>s+c.pnl,0) };
}
function allClosed(){
  const out=[];
  for(const [k,b] of Object.entries(SNAP.books))
    for(const c of b.closed) out.push({...c, book:k});
  out.sort((a,b)=>String(b.exit_t).localeCompare(String(a.exit_t)));
  return out;
}

/* ---------- nav ---------- */
const NAV=[["v-overview","Overview",null],["v-forward","Forward",null],["v-backtest","Backtest",null],["v-montecarlo","Monte Carlo",null]];
function renderNav(){
  const live = Object.entries(SNAP.books).filter(([k])=>k!=="spot").map(([,b])=>b);
  const nOpen = live.reduce((s,b)=>s+b.open.length,0);
  const nCl = live.reduce((s,b)=>s+b.closed.length,0);
  const counts={"v-overview":nOpen,"v-forward":nCl,"v-backtest":SNAP.spot.n,"v-montecarlo":(SNAP.mc?1:0)+(SNAP.mc_fut?1:0)};
  $("nav").innerHTML = NAV.map(([id,label])=>
    `<button class="tab" data-v="${id}">${label}<b>${counts[id]}</b></button>`).join("");
  const show = id => {
    document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("on", x.dataset.v===id));
    document.querySelectorAll(".view").forEach(v=>v.classList.toggle("on", v.id===id));
    try{ history.replaceState(null,"","#"+id); }catch(e){}
  };
  document.querySelectorAll(".tab").forEach(t=>t.addEventListener("click",()=>show(t.dataset.v)));
  const start = (location.hash||"").replace("#","");
  show(NAV.some(([id])=>id===start) ? start : "v-overview");
}

function renderBooks(){
  const rows=Object.entries(SNAP.books).filter(([k])=>k!=="spot").map(([k,b])=>{
    const e=bookEq(b), cl=b.closed;
    const w=cl.filter(c=>c.pnl>0).length;
    const gw=cl.filter(c=>c.pnl>0).reduce((s,c)=>s+c.pnl,0);
    const gl=-cl.filter(c=>c.pnl<=0).reduce((s,c)=>s+c.pnl,0);
    const R=cl.map(c=>c.risk?c.pnl/c.risk:0);
    const ex=R.length?R.reduce((s,v)=>s+v,0)/R.length:0;
    let risk=0,mv=0;
    for(const p of b.open){ const cur=live.px[p.symbol];
      risk+=Math.max(0,((cur||p.entry)-p.stop))*p.qty;
      mv+=cur?cur*p.qty:p.notional; }
    const dep=e.eq?100*mv/e.eq:0, heat=e.eq?100*risk/e.eq:0;
    const okn=b.kind==="fut"?"chip-fut":"chip-spot";
    return `<tr><td><span class="chip ${okn}">${esc(b.label)}</span></td>`+
    `<td class="num">$${fm(e.eq)}</td><td class="num ${e.eq>=START?"pos":"neg"}">${pc((e.eq/START-1)*100)}</td>`+
    `<td class="num ${e.un>=0?"pos":"neg"}">${e.un>=0?"+":""}$${fm(e.un)}</td>`+
    `<td class="num ${e.real>=0?"pos":"neg"}">${e.real>=0?"+":""}$${fm(e.real)}</td>`+
    `<td class="num muted">${b.open.length}/${cl.length}</td>`+
    `<td class="num muted">${cl.length?Math.round(100*w/cl.length)+"%":"—"}</td>`+
    `<td class="num">${cl.length?(gl>0?(gw/gl).toFixed(2):"inf"):"—"}</td>`+
    `<td class="num ${ex>=0?"pos":"neg"}">${cl.length?((ex>=0?"+":"")+ex.toFixed(2)+"R"):"—"}</td>`+
    `<td class="num muted">${dep.toFixed(0)}%</td><td class="num muted">$${fm(risk)}</td></tr>`;
  }).join("");
  $("perbook").innerHTML=`<table><thead><tr><th>book</th><th class="num">equity</th><th class="num">ret</th>`+
    `<th class="num">unreal</th><th class="num">realized</th><th class="num">open/closed</th><th class="num">win</th>`+
    `<th class="num">PF</th><th class="num">exp R</th><th class="num">deployed</th><th class="num">risk@stop</th></tr></thead>`+
    `<tbody>${rows}</tbody></table>`;
}

/* ---------- overview: forward book is the headline, no cross-book sums ---------- */
function liveBooks(){
  return Object.entries(SNAP.books).filter(([k])=>k!=="spot");
}
function renderHero(){
  const fwd = SNAP.books.fwd, fut = SNAP.books.fut;
  const e = bookEq(fwd), ef = fut ? bookEq(fut) : null;
  const ret = (e.eq/START-1)*100;
  $("hero-label").textContent = "Live paper · Forward · $"+fm(START)+" start";
  $("hero-eq").textContent = "$" + fm(e.eq);
  $("hero-eq").className = "hero-val " + (ret>=0?"pos":"neg");
  let risk=0;
  for(const p of fwd.open){ const cur=live.px[p.symbol];
    risk += Math.max(0,((cur||p.entry)-p.stop))*p.qty; }
  $("hero-sub").innerHTML =
    `<b class="${ret>=0?"pos":"neg"}">${pc(ret)}</b> since start · <b>${fwd.open.length}</b> open · `+
    `unrealized <b class="${e.un>=0?"pos":"neg"}">$${fm(e.un)}</b> · `+
    `realized <b class="${e.real>=0?"pos":"neg"}">$${fm(e.real)}</b> · `+
    `risk at stop <b>$${fm(risk)} (${e.eq?(100*risk/e.eq).toFixed(1):"0"}%)</b>`+
    (ef ? `<br><span class="muted">futures 3x book: <b>$${fm(ef.eq)}</b> (${pc((ef.eq/START-1)*100)}) · ${fut.open.length} open</span>` : "");
  $("hero-books").innerHTML =
    `<div class="hcell"><label>cash</label><b>$${fm(fwd.cash)}</b><i>forward</i></div>`+
    `<div class="hcell"><label>closed</label><b>${fwd.closed.length}</b><i>realized $${fm(e.real)}</i></div>`+
    `<div class="hcell"><label>win rate</label><b>${fwd.closed.length?Math.round(100*fwd.closed.filter(c=>c.pnl>0).length/fwd.closed.length)+"%":"—"}</b><i>forward</i></div>`+
    `<div class="hcell"><label>deployed</label><b>${e.eq?Math.round(100*(e.eq-fwd.cash)/e.eq):0}%</b><i>of equity</i></div>`;
}

function retStr(eq){ const v=(eq/START-1)*100; return (v>=0?"+":"")+v.toFixed(1)+"%"; }
function renderPositions(){
  const wrap=$("positions"); let h="";
  for(const [k,b] of liveBooks()){
    const e=bookEq(b);
    for(const p of b.open) h+=posCard(k,b,p,e.eq);
  }
  wrap.innerHTML = h || `<div class="empty">Flat — no open positions.</div>`;
}
function posCard(bk,b,p,eq){
  const cur=live.px[p.symbol];
  const u = cur ? (cur/p.entry-1)*100 : 0;
  const up = cur ? (cur-p.entry)*p.qty : 0;
  const cls = up>=0?"pos":"neg";
  const dst = cur ? (p.stop/cur-1)*100 : 0;
  const dtp = cur ? (p.tp/cur-1)*100 : 0;
  const span = Math.abs(p.tp-p.entry)||1;
  const prog = cur ? Math.max(0,Math.min(100,(cur-p.entry)/span*100)) : 0;
  let smark=0;
  if(p.tp!==p.entry) smark=Math.max(0,Math.min(100,(p.stop-p.entry)/(p.tp-p.entry)*100));
  return `<div class="pos-card${cur?"":" stale"}">
    <div class="pos-top"><span class="pos-sym">${esc(p.symbol)}</span>
      <span class="chip ${b.kind==="fut"?"chip-fut":"chip-spot"}">${esc(b.label)}</span>
      ${p.lev?`<span class="chip">${esc(p.lev)}x</span>`:""}
      <span class="chip">${esc(ago(p.t))} old</span></div>
    <div class="pos-px ${cls}">${cur?"$"+fmg(cur):"entry $"+fmg(p.entry)}
      <span class="pct">${cur?pc(u):"no live price"}</span></div>
    <div class="track" title="progress entry → target"><span class="track-fill ${cls}" style="width:${prog.toFixed(0)}%"></span>
      <span class="track-mark" style="left:${smark.toFixed(0)}%"></span></div>
    <div class="kv-grid">
      <span class="kv">entry <b>${fmg(p.entry)}</b></span>
      <span class="kv">stop <b>${fmg(p.stop)}</b> <i class="neg">(${cur?pc(dst):"—"})</i></span>
      <span class="kv">target <b>${fmg(p.tp)}</b> <i class="pos">(${cur?pc(dtp):"—"})</i></span>
      <span class="kv">size <b>$${fm(p.notional)}</b></span>
    </div>
    <div class="pos-meta" style="display:flex;gap:11px;flex-wrap:wrap;margin-top:9px;padding-top:8px;border-top:1px solid var(--line);font-size:11px;color:var(--dim)">
      <span class="kv">uP&L <b class="${cls}">${up>=0?"+":""}$${fm(up)}</b></span>
      <span class="kv">risk <b>$${fm(p.risk)} (${eq?(100*p.risk/eq).toFixed(1):"0"}%)</b></span>
      ${p.liq?`<span class="kv">liq <b>${fmg(p.liq)}</b></span>`:""}
      <span class="kv">score <b>${p.score}</b></span>
    </div></div>`;
}
function renderClosedMini(){
  const c=allClosed().slice(0,8);
  $("closed-mini").innerHTML = c.length ? c.map(x=>
    `<div style="display:flex;gap:12px;align-items:center;padding:8px 2px;border-bottom:1px solid rgba(255,255,255,.04);font-size:13px">
      <b>${esc(x.symbol)}</b><span class="chip">${esc(x.book)}</span>
      <span class="muted">${fmg(x.entry)} → ${fmg(x.exit)}</span>
      <span class="${x.pnl>=0?"pos":"neg"}" style="margin-left:auto;font-weight:650">${x.pnl>=0?"+":""}$${fm(x.pnl)}</span>
      <span class="chip">${esc(x.reason)}</span></div>`).join("")
    : `<div class="empty">No closed trades yet — exits appear here.</div>`;
}

/* ---------- forward ---------- */
function renderForward(){
  const b=SNAP.books.fwd, bt=SNAP.spot;
  const cl=[...b.closed].sort((x,y)=>String(y.exit_t).localeCompare(String(x.exit_t)));
  let exp="—", wr="—", pf="—";
  if(cl.length){
    const w=cl.filter(c=>c.pnl>0).length, gw=cl.filter(c=>c.pnl>0).reduce((s,c)=>s+c.pnl,0),
          gl=-cl.filter(c=>c.pnl<=0).reduce((s,c)=>s+c.pnl,0);
    const R=cl.map(c=>c.risk?c.pnl/c.risk:0), m=R.reduce((s,v)=>s+v,0)/R.length;
    wr=(100*w/cl.length).toFixed(0)+"%"; pf=gl>0?(gw/gl).toFixed(2):"—"; exp=(m>=0?"+":"")+m.toFixed(2)+"R";
  }
  $("expect").innerHTML =
    `<span class="kv">closed <b>${cl.length}</b></span>`+
    `<span class="kv">win <b>${wr}</b><i>bt ${(100*(bt.win||0)).toFixed(0)}%</i></span>`+
    `<span class="kv">PF <b>${pf}</b><i>bt ${(bt.pf||0).toFixed(2)}</i></span>`+
    `<span class="kv">exp <b>${exp}</b><i>bt +${(bt.exp||0).toFixed(2)}R</i></span>`;
  $("fwd-table").innerHTML = cl.length ?
    `<table><thead><tr><th>symbol</th><th class="num">entry → exit</th><th class="num">R</th><th class="num">pnl $</th><th>exit</th></tr></thead><tbody>`+
    cl.slice(0,40).map(c=>{const R=c.risk?c.pnl/c.risk:0;
      return `<tr><td class="sym">${esc(c.symbol)}</td><td class="num muted">${fmg(c.entry)} → ${fmg(c.exit)}</td>`+
      `<td class="num ${R>0?"pos":"neg"}">${R>=0?"+":""}${R.toFixed(2)}R</td>`+
      `<td class="num ${c.pnl>=0?"pos":"neg"}">${c.pnl>=0?"+":""}$${fm(c.pnl)}</td>`+
      `<td class="muted">${esc(c.reason)}</td></tr>`;}).join("")+`</tbody></table>`
    : `<div class="empty">No closed trades yet — exits appear here on the next paper cycle.</div>`;
  const Rs=cl.map(c=>c.risk?c.pnl/c.risk:0);
  const bins=[[-99,-1,"&lt;-1R"],[-1,0,"-1..0"],[0,1,"0..1"],[1,2,"1..2"],[2,99,">2R"]];
  const mx=Math.max(1,...bins.map(([a,z])=>Rs.filter(r=>r>a&&r<=z).length));
  $("r-hist").innerHTML = Rs.length ? bins.map(([a,z,nm],i)=>{
    const c=Rs.filter(r=>r>a&&r<=z).length;
    return `<div class="rb"><div class="rb-bar ${i>=2?"":"neg"}" style="height:${Math.round(8+c/mx*60)}px"></div><span>${nm}</span><b>${c}</b></div>`;
  }).join("") : `<div class="empty">no closed trades</div>`;
  const sn=b.snaps||[];
  $("fwd-snaps").innerHTML = sn.length ?
    `<table><thead><tr><th>time</th><th class="num">cash</th></tr></thead><tbody>`+
    sn.slice(-30).map(([t,v])=>`<tr><td class="muted">${esc(String(t).slice(0,16))}</td><td class="num">$${fm(v)}</td></tr>`).join("")+
    `</tbody></table>` : `<div class="empty">No snapshots.</div>`;
  draw("ch-fwd", sn.map(x=>x[1]), "#a78bfa", null);
  const fills=[];
  for(const [k,bb] of Object.entries(SNAP.books)) for(const t of bb.fills) fills.push({...t,book:bb.label});
  fills.sort((a,c)=>String(c.t).localeCompare(String(a.t)));
  $("fills").innerHTML = fills.length ?
    `<table><thead><tr><th>time</th><th>symbol</th><th>book</th><th>side</th><th class="num">qty</th><th class="num">price</th><th class="num">fee</th></tr></thead><tbody>`+
    fills.slice(0,60).map(t=>`<tr><td class="muted">${esc(String(t.t).slice(0,16))}</td><td class="sym">${esc(t.symbol)}</td>`+
      `<td><span class="chip">${esc(t.book)}</span></td><td class="muted">${esc(t.side)}</td>`+
      `<td class="num muted">${fmg(t.qty)}</td><td class="num">${fmg(t.price)}</td><td class="num neg">$${t.fee.toFixed(2)}</td></tr>`).join("")+
    `</tbody></table>` : `<div class="empty">No fills yet.</div>`;
}

/* ---------- backtest ---------- */
function statRow(el, items){
  el.innerHTML = items.map(([l,v,s,cls])=>
    `<div class="stat" style="background:var(--panel);padding:11px 12px"><label>${l}</label><b class="${cls||""}">${v}</b>${s?`<i>${s}</i>`:""}</div>`).join("");
}
function renderBacktest(){
  const b=SNAP.spot;
  $("bt-file").textContent=b.file||"no backtest loaded";
  statRow($("bt-stats"),[
    ["trades",b.n,""],["win",(100*b.win).toFixed(0)+"%",""],
    ["PF",b.pf.toFixed(2),""],["exp R","+"+b.exp.toFixed(2)+"R",""],
    ["avg hold",b.avg_bars+" bars",""],["symbols",b.rows.length,""]]);
  draw("ch-bt", b.cum, "#2dd4bf", START);
  const rs=[...b.rows].sort((x,y)=>y.ret-x.ret);
  $("bt-per").textContent=`per-symbol (${rs.length}) · best ${rs.length?rs[0].s+" +"+rs[0].ret+"%":"—"} · worst ${rs.length?rs[rs.length-1].s+" "+rs[rs.length-1].ret+"%":"—"}`;
  $("bt-syms").innerHTML=`<table><thead><tr><th>symbol</th><th class="num">return</th><th class="num">trades</th><th class="num">win</th><th class="num">PF</th><th class="num">max DD</th></tr></thead><tbody>`+
    rs.map(r=>`<tr><td class="sym">${esc(r.s)}</td><td class="num ${r.ret>=0?"pos":"neg"}">${r.ret>=0?"+":""}${r.ret}%</td>`+
      `<td class="num muted">${r.n}</td><td class="num muted">${r.win}%</td><td class="num">${r.pf===null?"inf":r.pf}</td>`+
      `<td class="num neg">${r.dd}%</td></tr>`).join("")+`</tbody></table>`;
  const tot=Object.values(b.reasons||{}).reduce((s,v)=>s+v,0)||1;
  const chips=Object.entries(b.reasons||{}).sort((a,c)=>c[1]-a[1]).slice(0,6)
    .map(([k,v])=>`<span class="chip">${esc(k)} <b>${Math.round(100*v/tot)}%</b></span>`).join("");
  $("bt-method").innerHTML=`<div style="display:flex;gap:7px;flex-wrap:wrap;margin-bottom:9px">${chips}</div>
    <p class="note">causal 4H replay · daily-trend + BTC regime aligned by bar time (no lookahead) · 0.10% fees + slippage · expectancy +${b.exp.toFixed(2)}R over ${b.n} trades</p>`;
  const f=SNAP.fut;
  $("btf-file").textContent=f.file||"no futures backtest";
  statRow($("btf-stats"),[
    ["trades",f.n,""],["win",(100*f.win).toFixed(0)+"%",""],["PF",f.pf.toFixed(2),""],
    ["exp R","+"+f.exp.toFixed(2)+"R",""],["liquidations",f.liq,"3x isolated"]]);
  const fr=[...f.rows].sort((x,y)=>y.ret-x.ret);
  $("btf-rows").innerHTML=`<div class="tbl-scroll"><table><thead><tr><th>symbol</th><th class="num">return</th><th class="num">trades</th><th class="num">win</th><th class="num">PF</th><th class="num">max DD</th><th class="num">liq</th></tr></thead><tbody>`+
    fr.map(r=>`<tr><td class="sym">${esc(r.s)}</td><td class="num ${r.ret>=0?"pos":"neg"}">${r.ret>=0?"+":""}${r.ret}%</td>`+
      `<td class="num muted">${r.n}</td><td class="num muted">${r.win}%</td><td class="num">${r.pf===null?"inf":r.pf}</td>`+
      `<td class="num neg">${r.dd}%</td><td class="num">${r.liq||0}</td></tr>`).join("")+`</tbody></table></div>`;
}

/* ---------- monte carlo ---------- */
function renderMC(){
  const cards=[];
  for(const [title,m] of [["Spot backtest",SNAP.mc],["Futures 3x",SNAP.mc_fut]]){
    if(!m) continue;
    cards.push(`<div class="mc-card"><div class="mc-head"><span>${title}</span>
      <span class="chip">${m.sims} sims · risk ${m.risk}%</span></div><div class="mc-grid">
      <div><label>final median</label><b>$${fm(m.med)}</b><i>p5 $${fm(m.p5)} · p95 $${fm(m.p95)}</i></div>
      <div><label>max DD median</label><b class="neg">${m.dd_med}%</b><i>p95 ${m.dd_p95}%</i></div>
      <div><label>profit prob</label><b class="pos">${m.pprofit}%</b><i>ruin ${m.ruin}%</i></div>
      <div><label>walk-forward</label><b>${m.wf1} → ${m.wf2}</b><i>PF half 1 → 2</i></div>
      </div></div>`);
  }
  $("mc-row").innerHTML=cards.join("")||`<div class="empty">No Monte Carlo runs yet.</div>`;
}

/* ---------- canvas ---------- */
function draw(id,a,color,start){
  const c=document.getElementById(id); if(!c) return;
  const x=c.getContext("2d");
  const W=c.width=Math.max(300,c.offsetWidth*2), H=c.height=370;
  x.clearRect(0,0,W,H);
  if(!a.length){x.fillStyle="#525c70";x.font="22px sans-serif";x.fillText("not enough data yet",20,H/2);return;}
  if(start!=null) a=[start].concat(a);
  const mn=Math.min.apply(0,a), mx=Math.max.apply(0,a);
  const rg=(mx-mn)||1;
  x.strokeStyle="rgba(255,255,255,.05)";x.lineWidth=1;
  for(let g=0;g<5;g++){const y=H*g/4;x.beginPath();x.moveTo(0,y);x.lineTo(W,y);x.stroke();}
  if(start!=null){const y0=H-((start-mn)/rg)*(H-28)-14;
    x.strokeStyle="rgba(255,255,255,.16)";x.setLineDash([7,7]);x.beginPath();x.moveTo(0,y0);x.lineTo(W,y0);x.stroke();x.setLineDash([]);}
  x.strokeStyle=color;x.lineWidth=2.8;x.beginPath();let py=0;
  a.forEach((v,i)=>{const px=a.length>1?i/(a.length-1)*W:W/2;py=H-((v-mn)/rg)*(H-28)-14;i?x.lineTo(px,py):x.moveTo(px,py);});
  x.stroke();x.fillStyle=color;x.beginPath();x.arc(W-3,py,3.5,0,7);x.fill();
}

/* ---------- boot ---------- */
renderNav(); renderHero(); renderBooks(); renderPositions(); renderClosedMini();
renderForward(); renderBacktest(); renderMC();
draw("ch-bt", SNAP.spot.cum, "#2dd4bf", START);
draw("ch-fwd", (SNAP.books.fwd.snaps||[]).map(s=>s[1]), "#a78bfa", null);
// Baked prices already render above; show their age immediately so the page
// is never blank on that front, then try a live refresh on top.
if(live.ok) setPill(true, "snapshot · "+ago(new Date(live.at).toISOString())+" old");
if(!location.protocol.startsWith("http")){
  const w=document.querySelector(".wrap");
  const d=document.createElement("div");
  d.innerHTML=`<div class="panel" style="border-color:rgba(251,191,36,.45);margin-bottom:18px">`+
    `<b style="color:var(--amber)">Static copy — frozen at ${esc(SNAP.meta.built)}.</b> `+
    `<span class="muted">Open <b>http://127.0.0.1:8000</b> for the live tracking page `+
    `(auto-rebuilds + fresh prices).</span></div>`;
  w.insertBefore(d, w.children[1]);
} else {
  // served page: soft auto-reload every 3 min (hash preserves the tab)
  setTimeout(()=>location.reload(), 180000);
}
refreshPrices();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
