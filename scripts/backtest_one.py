"""Quick backtest of the current rules on one symbol.
Usage: python scripts/backtest_one.py SYMBOL [--tf 4h]
Ex: python scripts/backtest_one.py SOLUSDT
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from src import binance_client, backtest

sym = sys.argv[1] if len(sys.argv) > 1 else "SOLUSDT"
d4 = binance_client.klines(sym, "4h", 1000)
dd = binance_client.klines(sym, "1d", 400)
btc = binance_client.klines("BTCUSDT", "1d", 300)
res = backtest.backtest(d4, dd, btc)
print(f"{sym}: eq=${res['equity']:.0f} ret={res['return_pct']:+.1f}% n={res['n_trades']} win={res['win_rate']:.0%}")
for t in res["trades"][-10:]:
    print(f"  {t['entry']:.4g}->{t['exit']:.4g} {t['ret']:+.1%} ({t['reason']},{t['bars']}bars)")
