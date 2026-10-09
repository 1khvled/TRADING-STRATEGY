"""One paper-trade cycle: manage exits, then open top new setups.
Usage: python scripts/paper_trade_once.py [--max-new 2] [--limit 25]
Safe: writes to data/paper.db only. No real orders.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import argparse
from datetime import datetime, timezone
from src import universe, binance_client, strategy, risk, paper, config
from src import indicators as ind

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

def manage_exits():
    closed = []
    for pos in paper.open_positions():
        sym = pos["symbol"]
        try:
            d4 = binance_client.klines(sym, "4h", 120)
        except Exception as e:
            print(f"  exit-check {sym}: skip ({e})")
            continue
        px = float(d4["close"].iloc[-1])
        atr = float(ind.atr(d4).iloc[-1])
        # trailing update
        highs = d4["close"].tail(20).max()
        new_stop = max(pos["stop"], highs - atr * 3.0) if atr > 0 else pos["stop"]
        if new_stop > pos["stop"]:
            paper.update_trailing(pos["id"], new_stop)
            pos["stop"] = new_stop
        reason = None
        if px <= pos["stop"]:
            reason = f"stop/trail @{pos['stop']:.6g}"
        elif px >= pos["take_profit"]:
            reason = f"take-profit @{pos['take_profit']:.6g}"
        else:
            bh = bars_held(pos)
            sig, msg = strategy.exit_signal(d4, pos["entry_price"], bh)
            if sig:
                reason = msg
        if reason:
            pnl = paper.close_position(pos, px, reason)
            closed.append((sym, pnl, reason))
            print(f"  CLOSED {sym} @{px:.6g} pnl=${pnl:+.2f} ({reason})")
        else:
            print(f"  HOLD {sym} @{px:.6g} stop={pos['stop']:.6g} tp={pos['take_profit']:.6g}")
    return closed

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-new", type=int, default=2)
    ap.add_argument("--limit", type=int, default=25)
    a = ap.parse_args()

    print("== exits ==")
    manage_exits()
    s = paper.summary()
    print(f"equity=${s['equity']:.2f} open={s['n_open']} closed={s['n_closed']} "
          f"win={s['win_rate']:.0%} realized=${s['realized_pnl']:+.2f}")

    if s["n_open"] >= 5:
        print("max positions reached, no new entries.")
        return
    print("\n== entries ==")
    uni = universe.build_universe()
    print(f"universe: {len(uni)}")
    btc = binance_client.klines("BTCUSDT", "1d", 300)
    cands = []
    for u in uni[:60]:  # scan top-60 of mid/low slice for speed
        sym = u["symbol"]
        if sym in config.BLOCKED_SYMBOLS or sym not in config.SMART_SYMBOLS:
            continue
        if any(p["symbol"] == sym for p in paper.open_positions()):
            continue
        try:
            d4 = binance_client.klines(sym, "4h", 300)
            dd = binance_client.klines(sym, "1d", 120)
            if len(d4) < 210:
                continue
            score, f, reasons = strategy.score_setup(d4, dd, btc)
            ok, msg = strategy.passes_filters(score, f)
            if ok:
                cands.append((score, sym, float(d4["close"].iloc[-1]), f))
                print(f"  PASS {sym} score={score:.0f} px={f['price']:.6g} ATR%={f['atr_pct']:.2f} {','.join(reasons)}")
        except Exception as e:
            print(f"  {sym}: skip ({e})")
    cands.sort(reverse=True)
    eq = paper.equity_now()
    opened = 0
    for score, sym, px, f in cands[:a.limit]:
        if opened >= a.max_new or paper.summary()["n_open"] >= 5:
            break
        pos = risk.size_position(eq, px, f["atr"])
        if pos["qty"] <= 0 or pos["notional"] < 25:
            print(f"  skip {sym}: size too small (${pos['notional']:.2f})")
            continue
        # fill with slippage
        fill = px * (1 + 0.0005)
        paper.open_position(sym, fill, pos["qty"], pos["stop"], score, f["atr"])
        print(f"  OPENED {sym} qty={pos['qty']:.4f} fill={fill:.6g} stop={pos['stop']:.6g} risk=${pos['risk_usd']:.2f}")
        opened += 1
    if not cands:
        print("no PASS setups this cycle.")
    print("\n", paper.summary())

if __name__ == "__main__":
    main()
