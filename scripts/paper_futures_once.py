"""Futures paper cycle (3x default, isolated, paper only).
Usage: python scripts/paper_futures_once.py --max-new 1 --lev 3
Writes to data/futures_paper.db. NO live orders. High risk: leverage kills.
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from datetime import datetime, timezone
from src import futures_universe, binance_client, strategy, paper_futures as pf
from src import indicators as ind, config

def bars_held(pos):
    """Real 4H bars elapsed since entry (entry_time is UTC ISO).
    Falls back to 30 (below the 60-bar time stop) if unparseable."""
    try:
        et = datetime.fromisoformat(str(pos.get("entry_time")))
        if et.tzinfo is None:
            et = et.replace(tzinfo=timezone.utc)
        return int((datetime.now(timezone.utc) - et).total_seconds() // 14400)
    except Exception:
        return 30

def heater(sym):
    try:
        return binance_client.futures_klines(sym, "4h", 120)
    except Exception:
        return binance_client.klines(sym, "4h", 120)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-new", type=int, default=1)
    ap.add_argument("--lev", type=int, default=None)
    ap.add_argument("--limit", type=int, default=25)
    a = ap.parse_args()
    lev = max(1, min(a.lev or config.FUTURES_DEFAULT_LEV, config.FUTURES_MAX_LEV))
    print(f"FUTURES paper (isolated {lev}x, paper only). High risk.")
    print("== exits ==")
    for pos in pf.open_positions():
        sym = pos["symbol"]
        try:
            d4 = heater(sym)
        except Exception as e:
            print(f"  {sym}: skip ({e})")
            continue
        px = float(d4["close"].iloc[-1])
        lo = float(d4["low"].iloc[-1])
        atr = float(ind.atr(d4).iloc[-1])
        if lo <= pos["liq_price"]:
            pnl = pf.close_position(pos, pos["liq_price"], "liquidation")
            print(f"  LIQUIDATED {sym} pnl=${pnl:+.2f}")
            continue
        new_stop = max(pos["stop"], d4["close"].tail(20).max() - atr * 3.0) if atr > 0 else pos["stop"]
        if new_stop > pos["stop"]:
            pf.update_trailing(pos["id"], new_stop)
            pos["stop"] = new_stop
        reason = None
        if px <= pos["stop"]: reason = "stop/trail"
        elif px >= pos["take_profit"]: reason = "take-profit"
        else:
            sig, msg = strategy.exit_signal(d4, pos["entry_price"], bars_held(pos))
            if sig: reason = msg
        if reason:
            pnl = pf.close_position(pos, px, reason)
            print(f"  CLOSED {sym} @{px:.6g} pnl=${pnl:+.2f} ({reason})")
        else:
            print(f"  HOLD {sym} @{px:.6g} stop={pos['stop']:.6g} liq={pos['liq_price']:.6g}")
    s = pf.summary()
    print(f"equity=${s['equity']:.2f} open={s['n_open']} closed={s['n_closed']} liqs={s['liquidations']}")
    if s["n_open"] >= config.FUTURES_MAX_POSITIONS:
        print("max positions reached.")
        return
    print("\n== entries ==")
    uni = futures_universe.build_futures_universe()
    btc = binance_client.klines("BTCUSDT", "1d", 300)
    cands = []
    for u in uni[:60]:
        sym = u["symbol"]
        if sym in config.BLOCKED_SYMBOLS or sym not in config.SMART_SYMBOLS:
            continue
        if any(p["symbol"] == sym for p in pf.open_positions()):
            continue
        try:
            try:
                d4 = binance_client.futures_klines(sym, "4h", 300)
            except Exception:
                d4 = binance_client.klines(sym, "4h", 300)
            try:
                dd = binance_client.klines(sym, "1d", 120)
            except Exception:
                dd = binance_client.futures_klines(sym, "1d", 120)
            if len(d4) < 210: continue
            score, f, rs = strategy.score_setup(d4, dd, btc)
            ok, msg = strategy.passes_filters(score, f)
            if ok:
                cands.append((score, sym, float(d4["close"].iloc[-1]), f))
                print(f"  PASS {sym} score={score:.0f} ATR%={f['atr_pct']:.2f}")
        except Exception as e:
            print(f"  {sym}: skip ({e})")
    cands.sort(reverse=True)
    eq = pf.equity_now()
    opened = 0
    for score, sym, px, f in cands[:a.limit]:
        if opened >= a.max_new or pf.summary()["n_open"] >= config.FUTURES_MAX_POSITIONS:
            break
        sz = pf.size_futures(eq, px, f["atr"], lev, config.FUTURES_RISK_PER_TRADE)
        if not sz:
            print(f"  skip {sym}: leverage/liq guard")
            continue
        fill = px * (1 + config.FUTURES_SLIPPAGE)
        pf.open_position(sym, fill, sz["qty"], sz["margin"], sz["stop"], sz["tp"], sz["liq"],
                         score, f["atr"], sz["risk_usd"], lev)
        print(f"  OPENED {sym} {lev}x qty={sz['qty']:.4f} margin=${sz['margin']:.0f} stop={sz['stop']:.6g} liq={sz['liq']:.6g}")
        opened += 1
    if not cands:
        print("no PASS setups.")

if __name__ == "__main__":
    main()
