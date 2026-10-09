"""Breakout-continuation hypothesis test (spot, causal, fees included).

Question: when 4H price breaks the Donchian(N) high, does continuation follow,
or are we buying the top? Three layers:

1. SIGNAL STATS: for every Donchian breakout bar -> forward returns at
   +1/+3/+5/+10/+20 bars, MAE/MFE in R, % reaching +1R/+2R before -1R.
2. ABLATION backtests (identical exits/risk/fees, only entry differs):
   - full    : live score>=60 + filters (the traded strategy)
   - breakout: breakout trigger + BTC regime + ATR% filter only
   - random  : random entries at matched frequency + regime filter
   If breakout ~= random, the trigger adds nothing and edge (if any) is exits.
3. SENSITIVITY: Donchian 20/30/50, score 50/60/70.

Usage: python scripts/breakout_test.py [--symbols ...] [--total4h 3000]
Saves data/breakout_test_<ts>.json. Paper only.
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from src import binance_client, config
from src import indicators as ind
from src.config import SMART_SYMBOLS

FEE = config.FEE_RATE + config.SLIPPAGE_RATE

def load(sym, total4h):
    dd_days = max(320, total4h * 4 // 24 + 60)
    d4 = binance_client.klines_paged(sym, "4h", total4h)
    dd = binance_client.klines_paged(sym, "1d", dd_days)
    return d4, dd

def feats(d4, dd, btc, don_n):
    c = d4["close"]
    out = {
        "c": c, "e50": ind.ema(c, 50), "e200": ind.ema(c, 200),
        "rsi": ind.rsi(c), "adx": ind.adx(d4), "atr": ind.atr(d4),
        "dh": ind.donchian_high(d4["high"], don_n),
        "vz": ind.volume_zscore(d4["volume"]),
        "d_close": dd["close"], "d_ema50": ind.ema(dd["close"], 50),
        "b_close": btc["close"], "b_sma200": ind.sma(btc["close"], 200),
        "d_idx": np.searchsorted(dd["open_time"].values, d4["open_time"].values, side="right") - 1,
        "b_idx": np.searchsorted(btc["open_time"].values, d4["open_time"].values, side="right") - 1,
    }
    return out

def signal_stats(d4, f):
    """Per-breakout forward-return + MAE/MFE diagnostics."""
    c = f["c"].values
    atr = f["atr"].values
    dh = f["dh"].values
    fw_rets, mae_r, mfe_r, win1r, win2r, n = [], [], [], 0, 0, 0
    horizons = {1: [], 3: [], 5: [], 10: [], 20: []}
    for i in range(210, len(d4) - 21):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        if not (c[i] >= dh[i]):
            continue
        n += 1
        entry = c[i]
        stop_d = 2.5 * a
        for h in horizons:
            horizons[h].append((c[i + h] / entry - 1) * 100)
        fwd = c[i + 1:i + 21]
        mae_r.append(((entry - fwd.min()) / stop_d))
        mfe_r.append(((fwd.max() - entry) / stop_d))
        # first-touch race: +1R/+2R vs -1R within 20 bars
        r_path = (fwd - entry) / stop_d
        t_neg = next((k for k, r in enumerate(r_path) if r <= -1.0), None)
        t_p1 = next((k for k, r in enumerate(r_path) if r >= 1.0), None)
        t_p2 = next((k for k, r in enumerate(r_path) if r >= 2.0), None)
        if t_p1 is not None and (t_neg is None or t_p1 < t_neg):
            win1r += 1
        if t_p2 is not None and (t_neg is None or t_p2 < t_neg):
            win2r += 1
    return {
        "n_breakouts": n,
        "fwd_ret_pct": {h: float(np.mean(v)) if v else 0.0 for h, v in horizons.items()},
        "fwd_hit_pos": {h: float(np.mean([x > 0 for x in v])) if v else 0.0 for h, v in horizons.items()},
        "mae_R": float(np.mean(mae_r)) if mae_r else 0.0,
        "mfe_R": float(np.mean(mfe_r)) if mfe_r else 0.0,
        "reach_1R_first": win1r / n if n else 0.0,
        "reach_2R_first": win2r / n if n else 0.0,
    }

def run_backtest(d4, f, equity, risk_pct, mode, rng=None, entry_prob=0.0):
    """Same exits/risk/fees for every mode; only the entry rule changes."""
    c, e50, e200 = f["c"], f["e50"], f["e200"]
    rsi, adx, atr, dh, vz = f["rsi"], f["adx"], f["atr"], f["dh"], f["vz"]
    trades = []
    pos = None
    eq = equity
    for i in range(210, len(d4)):
        px = float(c.iloc[i]); a = float(atr.iloc[i])
        if not np.isfinite(a) or a <= 0 or not np.isfinite(px) or px <= 0:
            continue
        di, bi = int(f["d_idx"][i]), int(f["b_idx"][i])
        if di < 55 or bi < 205:
            continue
        regime = bool(f["b_close"].iloc[bi] > f["b_sma200"].iloc[bi])
        if pos is None:
            if not regime:
                continue
            daily_ok = bool(f["d_close"].iloc[di] > f["d_ema50"].iloc[di])
            brk = bool(px >= dh.iloc[i])
            atrp = a / px * 100
            if not (1.5 <= atrp <= 12.0):
                continue
            take = False
            if mode == "breakout":
                take = brk
            elif mode == "full":
                sc = (20 if daily_ok else 0) + (20 if (e50.iloc[i] > e200.iloc[i] and px > e50.iloc[i]) else 0) \
                    + (25 if brk else 0) + (15 if (np.isfinite(vz.iloc[i]) and vz.iloc[i] > 1.5) else 0) \
                    + (10 if (55 <= (rsi.iloc[i] or 0) <= 75) else 0) + (10 if ((adx.iloc[i] or 0) > 20) else 0)
                take = sc >= 60
            elif mode == "random":
                take = bool(rng.random() < entry_prob)
            if not take:
                continue
            risk_usd = eq * risk_pct
            qty = min(risk_usd / (a * 2.5), (eq * 0.20) / px)
            stop = px - a * 2.5
            tp = px + a * 4.0
            cost = qty * px * (1 + FEE)
            if cost > eq * 0.25 or qty * px < 25:
                continue
            eq -= cost
            pos = {"entry": px, "qty": qty, "stop": stop, "tp": tp, "peak": px,
                   "i": i, "locked": qty * px, "risk_usd": (px - stop) * qty,
                   "entry_time": str(d4["open_time"].iloc[i])}
        else:
            pos["peak"] = max(pos["peak"], px)
            pos["stop"] = max(pos["stop"], pos["peak"] - a * 3.0)
            reason = None
            if px <= pos["stop"]: reason = "stop/trail"
            elif px >= pos["tp"]: reason = "take-profit"
            elif rsi.iloc[i] > 80: reason = "rsi-exhaust"
            elif px < e50.iloc[i]: reason = "ema50-break"
            elif i - pos["i"] >= 60: reason = "time-stop"
            if reason:
                proceeds = px * pos["qty"] * (1 - FEE)
                pnl = proceeds - pos["locked"]
                R = pnl / pos["risk_usd"] if pos["risk_usd"] else 0.0
                eq += pos["locked"] + pnl
                trades.append({"pnl": pnl, "R": float(R), "reason": reason,
                               "bars": i - pos["i"], "entry_time": pos["entry_time"],
                               "exit_time": str(d4["open_time"].iloc[i])})
                pos = None
    if pos is not None:
        px = float(c.iloc[-1])
        eq += pos["locked"] + (px * pos["qty"] * (1 - FEE) - pos["locked"])
    gw = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    w = sum(1 for t in trades if t["pnl"] > 0)
    R = np.array([t["R"] for t in trades]) if trades else np.array([0.0])
    return {"n": len(trades), "win": w / len(trades) if trades else 0.0,
            "pf": (gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)),
            "expR": float(np.mean(R)), "ret_pct": (eq / equity - 1) * 100,
            "trades": trades}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=str, default=",".join(sorted(SMART_SYMBOLS)))
    ap.add_argument("--total4h", type=int, default=3000)
    ap.add_argument("--equity", type=float, default=10000.0)
    ap.add_argument("--risk", type=float, default=0.01)
    a = ap.parse_args()
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    dd_days = max(320, a.total4h * 4 // 24 + 60)
    btc = binance_client.klines_paged("BTCUSDT", "1d", dd_days)
    out = {"symbols": {}, "agg": {}}
    pool = {"full": [], "breakout": [], "random": []}
    sig_pool = []
    for k, sym in enumerate(syms):
        try:
            d4, dd = load(sym, a.total4h)
            if len(d4) < 400 or len(dd) < 210:
                print(f"[{k+1}/{len(syms)}] {sym}: skip short history")
                continue
            f30 = feats(d4, dd, btc, 30)
            ss = signal_stats(d4, f30)
            sig_pool.append(ss)
            # entry frequency of full mode for matched random baseline
            full = run_backtest(d4, f30, a.equity, a.risk, "full")
            n_bars = len(d4) - 210
            # matched-frequency random baseline: same per-bar entry rate as full mode's occupancy
            if full["trades"]:
                occ = (sum(t["bars"] for t in full["trades"]) + len(full["trades"])) / max(n_bars, 1)
            else:
                occ = 1 / 200
            p = min(max(occ, 1 / 200), 0.05)
            rng = np.random.default_rng(abs(hash(sym)) % (2 ** 32))
            rnd = run_backtest(d4, f30, a.equity, a.risk, "random", rng=rng, entry_prob=min(p, 0.05))
            brk = run_backtest(d4, f30, a.equity, a.risk, "breakout")
            s20 = run_backtest(d4, feats(d4, dd, btc, 20), a.equity, a.risk, "breakout")
            s50 = run_backtest(d4, feats(d4, dd, btc, 50), a.equity, a.risk, "breakout")
            for t in full["trades"]: pool["full"].append(t)
            for t in brk["trades"]: pool["breakout"].append(t)
            for t in rnd["trades"]: pool["random"].append(t)
            row = {"signal": ss,
                   "full": {kk: full[kk] for kk in ("n", "win", "pf", "expR", "ret_pct")},
                   "breakout30": {kk: brk[kk] for kk in ("n", "win", "pf", "expR", "ret_pct")},
                   "breakout20": {kk: s20[kk] for kk in ("n", "win", "pf", "expR", "ret_pct")},
                   "breakout50": {kk: s50[kk] for kk in ("n", "win", "pf", "expR", "ret_pct")},
                   "random": {kk: rnd[kk] for kk in ("n", "win", "pf", "expR", "ret_pct")}}
            out["symbols"][sym] = row
            print(f"[{k+1}/{len(syms)}] {sym}: sig_n={ss['n_breakouts']} "
                  f"full PF={full['pf']:.2f}/n={full['n']} brk30 PF={brk['pf']:.2f}/n={brk['n']} "
                  f"rnd PF={rnd['pf']:.2f}/n={rnd['n']}")
        except Exception as e:
            print(f"[{k+1}/{len(syms)}] {sym}: ERROR {type(e).__name__}: {e}")
    # pooled summaries
    def summ(trades):
        if not trades:
            return {"n": 0}
        gw = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
        R = np.array([t["R"] for t in trades])
        return {"n": len(trades), "win": float(np.mean([t["pnl"] > 0 for t in trades])),
                "pf": float(gw / gl) if gl > 0 else 0.0, "expR": float(np.mean(R)),
                "sdR": float(np.std(R)), "t_expR": float(np.mean(R) / (np.std(R) / np.sqrt(len(R))))}
    out["agg"] = {m: summ(pool[m]) for m in pool}
    if sig_pool:
        tot = sum(s["n_breakouts"] for s in sig_pool)
        out["agg"]["signal"] = {
            "n_breakouts": tot,
            "fwd_ret_pct": {h: float(np.mean([s["fwd_ret_pct"][h] for s in sig_pool])) for h in (1, 3, 5, 10, 20)},
            "fwd_hit_pos": {h: float(np.mean([s["fwd_hit_pos"][h] for s in sig_pool])) for h in (1, 3, 5, 10, 20)},
            "mae_R": float(np.mean([s["mae_R"] for s in sig_pool])),
            "mfe_R": float(np.mean([s["mfe_R"] for s in sig_pool])),
            "reach_1R_first": float(np.mean([s["reach_1R_first"] for s in sig_pool])),
            "reach_2R_first": float(np.mean([s["reach_2R_first"] for s in sig_pool])),
        }
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    op = f"data/breakout_test_{ts}.json"
    json.dump(out, open(op, "w", encoding="utf-8"), indent=1)
    print("\n== POOLED ABLATION (same exits/risk/fees) ==")
    for m in ("full", "breakout", "random"):
        s = out["agg"][m]
        print(f"  {m:8s} n={s.get('n',0):4d} win={s.get('win',0):.0%} PF={s.get('pf',0):.2f} "
              f"expR={s.get('expR',0):+.3f} t={s.get('t_expR',0):+.2f}")
    g = out["agg"].get("signal", {})
    if g:
        print("== BREAKOUT SIGNAL (avg of per-symbol means) ==")
        for h in (1, 3, 5, 10, 20):
            print(f"  +{h:2d} bars: avg {g['fwd_ret_pct'][h]:+.3f}%  P(up)={g['fwd_hit_pos'][h]:.0%}")
        print(f"  MAE {g['mae_R']:.2f}R  MFE {g['mfe_R']:.2f}R  "
              f"1R-before--1R {g['reach_1R_first']:.0%}  2R-before--1R {g['reach_2R_first']:.0%}")
    print(f"saved {op}")

if __name__ == "__main__":
    main()
