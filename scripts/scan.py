"""Scan mid/low-tier alts, print ranked setups. Usage: python scripts/scan.py [--limit 15]"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import argparse
from src import universe, binance_client, strategy
from src import indicators as ind

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--rank-lo", type=int, default=None)
    ap.add_argument("--rank-hi", type=int, default=None)
    a = ap.parse_args()

    uni = universe.build_universe(rank_lo=a.rank_lo or 30, rank_hi=a.rank_hi or 180) \
        if (a.rank_lo or a.rank_hi) else universe.build_universe()
    print(f"universe: {len(uni)} symbols (mid/low-tier USDT spot, ex-BTC/ETH/XRP)")
    btc = binance_client.klines("BTCUSDT", "1d", 300)
    try:
        btc_rs = binance_client.klines("BTCUSDT", "4h", 50)
    except Exception:
        btc_rs = None

    out = []
    for i, u in enumerate(uni):
        sym = u["symbol"]
        try:
            d4 = binance_client.klines(sym, "4h", 300)
            dd = binance_client.klines(sym, "1d", 120)
            if len(d4) < 210 or len(dd) < 60:
                continue
            alt_btc_roc = 0.0
            # RS vs BTC is a bonus only; skip slow lookup in scan (alt/BTC pairs often don't exist).
            # Set >0 to test manually if needed.
            score, f, reasons = strategy.score_setup(d4, dd, btc, alt_btc_roc)
            ok, msg = strategy.passes_filters(score, f)
            out.append((score, sym, u["price"], u["quote_volume"], f, reasons, ok, msg))
        except Exception as e:
            print(f"  {sym}: skip ({e})")
        if (i + 1) % 30 == 0:
            print(f"  scanned {i+1}/{len(uni)}...")
    out.sort(reverse=True)
    print(f"\nTop {a.limit} (BTC regime ok={bool(btc['close'].iloc[-1] > ind.sma(btc['close'],200).iloc[-1])}):")
    print(f"{'score':>5} {'symbol':<14} {'price':>14} {'qv24h(M)':>9} {'ATR%':>6} {'RSI':>5} {'flag'}")
    for score, sym, px, qv, f, rs, ok, msg in out[:a.limit]:
        flag = "PASS" if ok else f"--- ({msg})"
        print(f"{score:5.0f} {sym:<14} {px:14g} {qv/1e6:9.1f} {f['atr_pct']:6.2f} {f['rsi']:5.1f} {flag} {','.join(rs)}")

if __name__ == "__main__":
    main()
