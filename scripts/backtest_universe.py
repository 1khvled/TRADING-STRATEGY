"""Universe backtest (causal, fees included).
Usage:
  python scripts/backtest_universe.py --limit 25 --per-symbol-equity 10000
  python scripts/backtest_universe.py --symbols SOLUSDT,AVAXUSDT --total4h 1500
Saves CSV+JSON to data/.
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from datetime import datetime, timezone
from src import universe, binance_client, backtest

def safe(s: str) -> str:
    return s.encode("ascii", "ignore").decode()

def run(symbols, total4h, per_eq):
    dd_days = max(320, total4h * 4 // 24 + 60)
    btc = binance_client.klines_paged("BTCUSDT", "1d", dd_days)
    rows = []
    all_trades = []
    for k, sym in enumerate(symbols):
        try:
            d4 = binance_client.klines_paged(sym, "4h", total4h)
            dd = binance_client.klines_paged(sym, "1d", dd_days)
            if len(d4) < 400 or len(dd) < 210:
                print(safe(f"[{k+1}/{len(symbols)}] {sym}: skip short history {len(d4)}/{len(dd)}"))
                continue
            res = backtest.backtest(d4, dd, btc, equity=per_eq)
            print(safe(f"[{k+1}/{len(symbols)}] {sym}: ret={res['return_pct']:+.1f}% n={res['n_trades']} "
                  f"win={res['win_rate']:.0%} PF={res['profit_factor']:.2f} dd={res['max_drawdown_pct']:.1f}%"))
            rows.append({"symbol": sym, "return_pct": res["return_pct"], "n_trades": res["n_trades"],
                         "win_rate": res["win_rate"], "profit_factor": res["profit_factor"],
                         "max_dd": res["max_drawdown_pct"], "avg_bars": res["avg_bars"],
                         "equity": res["equity"]})
            for t in res["trades"]:
                all_trades.append({"symbol": sym, **t})
        except Exception as e:
            print(safe(f"[{k+1}/{len(symbols)}] {sym}: ERROR {e}"))
    return rows, all_trades

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--per-symbol-equity", type=float, default=10000.0)
    ap.add_argument("--total4h", type=int, default=1200)
    ap.add_argument("--symbols", type=str, default=None)
    a = ap.parse_args()

    if a.symbols:
        syms = [s.strip().upper() for s in a.symbols.split(",")]
    else:
        uni = universe.build_universe()
        syms = [u["symbol"] for u in uni[:a.limit]]
    print(safe(f"backtesting {len(syms)} symbols, {a.total4h}x4h each (~{a.total4h*4/24:.0f}d)"))
    rows, trades = run(syms, a.total4h, a.per_symbol_equity)

    os.makedirs("data", exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    import csv
    csv_path = f"data/backtest_{ts}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "return_pct", "n_trades", "win_rate",
                                          "profit_factor", "max_dd", "avg_bars", "equity"])
        w.writeheader(); w.writerows(rows)
    with open(f"data/backtest_{ts}.json", "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "trades": trades}, f, indent=1)

    n = sum(r["n_trades"] for r in rows)
    if n:
        tot_ret = sum(r["return_pct"] for r in rows) / len(rows)
        wr = sum(r["win_rate"] * r["n_trades"] for r in rows) / n
        pf_num = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        pf_den = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
        pf = pf_num / pf_den if pf_den else 0
        print(safe(f"== SUMMARY ({len(rows)} syms, {n} trades) =="))
        print(safe(f"avg return/sym: {tot_ret:+.1f}% | pooled win: {wr:.0%} | pooled PF: {pf:.2f}"))
        print(safe(f"median trades/sym: {sorted(r['n_trades'] for r in rows)[len(rows)//2]}"))
        print(safe(f"saved {csv_path}"))
        if tot_ret < 0 or pf < 1.1:
            print("VERDICT: no edge yet - do NOT go live. Tune or stay paper.")
        elif n < 30:
            print("VERDICT: sample too small (<30 trades) - keep backtesting + start forward paper.")
        else:
            print("VERDICT: passes smoke test - proceed to FORWARD paper test.")
    else:
        print("no trades - strategy too selective on this window.")

if __name__ == "__main__":
    main()
