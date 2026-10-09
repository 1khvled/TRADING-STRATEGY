"""Futures universe backtest (3x default, causal, funding+fees+liquidation).
Usage:
  python scripts/backtest_futures_universe.py --limit 20 --lev 3 --total4h 1200
Saves data/futures_backtest_<ts>.csv/.json. PAPER ONLY - no live orders.
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from datetime import datetime, timezone
from src import futures_universe, binance_client, backtest_futures

def safe(s):
    return s.encode("ascii", "ignore").decode()

def run(symbols, total4h, per_eq, lev, risk):
    dd_days = max(320, total4h * 4 // 24 + 60)
    btc = binance_client.klines_paged("BTCUSDT", "1d", dd_days)
    rows, all_trades = [], []
    use_fut_klines = True
    try:
        binance_client.futures_get("/fapi/v1/ping")
    except Exception:
        use_fut_klines = False
        print("fapi klines blocked - using spot klines as proxy (costs still futures-style)")
    for k, sym in enumerate(symbols):
        try:
            if use_fut_klines:
                try:
                    d4 = binance_client.futures_klines_paged(sym, "4h", total4h)
                except Exception:
                    d4 = binance_client.klines_paged(sym, "4h", total4h)
            else:
                d4 = binance_client.klines_paged(sym, "4h", total4h)
            try:
                dd = binance_client.klines_paged(sym, "1d", dd_days)
            except Exception:
                try:
                    dd = binance_client.futures_klines_paged(sym, "1d", dd_days)
                except Exception:
                    print(safe(f"[{k+1}/{len(symbols)}] {sym}: skip no daily history"))
                    continue
            if len(d4) < 400 or len(dd) < 210:
                print(safe(f"[{k+1}/{len(symbols)}] {sym}: skip short history {len(d4)}/{len(dd)}"))
                continue
            res = backtest_futures.backtest_futures(d4, dd, btc, equity=per_eq,
                                                    risk_pct=risk, leverage=lev)
            print(safe(f"[{k+1}/{len(symbols)}] {sym}: ret={res['return_pct']:+.1f}% n={res['n_trades']} "
                  f"win={res['win_rate']:.0%} PF={res['profit_factor']:.2f} "
                  f"dd={res['max_drawdown_pct']:.1f}% liq={res['liquidations']}"))
            rows.append({"symbol": sym, "return_pct": res["return_pct"], "n_trades": res["n_trades"],
                         "win_rate": res["win_rate"], "profit_factor": res["profit_factor"],
                         "max_dd": res["max_drawdown_pct"], "liquidations": res["liquidations"],
                         "avg_bars": res["avg_bars"], "equity": res["equity"]})
            for t in res["trades"]:
                all_trades.append({"symbol": sym, **t})
        except Exception as e:
            print(safe(f"[{k+1}/{len(symbols)}] {sym}: ERROR {e}"))
    return rows, all_trades

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--lev", type=int, default=3)
    ap.add_argument("--risk", type=float, default=0.01)
    ap.add_argument("--per-symbol-equity", type=float, default=10000.0)
    ap.add_argument("--total4h", type=int, default=1200)
    ap.add_argument("--symbols", type=str, default=None)
    a = ap.parse_args()
    if a.symbols:
        syms = [s.strip().upper() for s in a.symbols.split(",")]
    else:
        syms = [u["symbol"] for u in futures_universe.build_futures_universe()[:a.limit]]
    print(safe(f"futures backtest {len(syms)} syms lev={a.lev}x risk={a.risk:.1%} (~{a.total4h*4/24:.0f}d)"))
    rows, trades = run(syms, a.total4h, a.per_symbol_equity, a.lev, a.risk)
    os.makedirs("data", exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    import csv
    cp = f"data/futures_backtest_{ts}.csv"
    with open(cp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "return_pct", "n_trades", "win_rate",
                                          "profit_factor", "max_dd", "liquidations", "avg_bars", "equity"])
        w.writeheader(); w.writerows(rows)
    json.dump({"rows": rows, "trades": trades, "leverage": a.lev, "risk": a.risk},
              open(f"data/futures_backtest_{ts}.json", "w", encoding="utf-8"), indent=1)
    n = sum(r["n_trades"] for r in rows)
    liq = sum(r["liquidations"] for r in rows)
    if n:
        pf_num = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        pf_den = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
        print(safe(f"== FUT SUMMARY ({len(rows)} syms, {n} trades, {liq} liqs) lev={a.lev}x =="))
        print(safe(f"avg ret/sym: {sum(r['return_pct'] for r in rows)/len(rows):+.1f}% PF: {pf_num/pf_den if pf_den else 0:.2f}"))
        print(safe(f"saved {cp}"))
        if liq > 0:
            print(f"WARNING: {liq} liquidations at {a.lev}x - cut leverage or widen ATR filter.")
    else:
        print("no trades.")

if __name__ == "__main__":
    main()
