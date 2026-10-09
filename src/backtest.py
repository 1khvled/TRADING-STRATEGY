"""Causal backtest on 4H klines with the same rules (fees included).
No lookahead: daily trend + BTC regime are aligned by open_time (merge_asof).
Execution at bar close + fees/slippage.
"""
import pandas as pd
import numpy as np
from . import indicators as ind, config

def backtest(df4h: pd.DataFrame, dfd: pd.DataFrame, btc_daily: pd.DataFrame,
             equity=10000.0, risk_pct=0.01) -> dict:
    df4h = df4h.copy().sort_values("open_time").reset_index(drop=True)
    dfd = dfd.copy().sort_values("open_time").reset_index(drop=True)
    btc = btc_daily.copy().sort_values("open_time").reset_index(drop=True)

    c = df4h["close"]
    e50 = ind.ema(c, 50); e200 = ind.ema(c, 200)
    rsi = ind.rsi(c); adx = ind.adx(df4h); atr = ind.atr(df4h)
    dh = ind.donchian_high(df4h["high"], 30)
    vz = ind.volume_zscore(df4h["volume"])

    d_close = dfd["close"]
    d_ema50 = ind.ema(d_close, 50)
    b_close = btc["close"]
    b_sma200 = ind.sma(b_close, 200)

    # align daily/BTC to each 4h bar: last daily/btc bar with open_time <= 4h open_time
    d_times = dfd["open_time"].values
    b_times = btc["open_time"].values
    h_times = df4h["open_time"].values
    import numpy as _np
    d_idx = _np.searchsorted(d_times, h_times, side="right") - 1
    b_idx = _np.searchsorted(b_times, h_times, side="right") - 1

    trades = []
    equity_curve = []
    pos = None
    eq = equity
    peak = equity
    max_dd = 0.0

    for i in range(210, len(df4h)):
        px = float(c.iloc[i])
        a = float(atr.iloc[i])
        if not _np.isfinite(a) or a <= 0 or not _np.isfinite(px) or px <= 0:
            equity_curve.append(eq + (pos["locked"] if pos else 0))
            continue
        di = int(d_idx[i]); bi = int(b_idx[i])
        if di < 55 or bi < 205:
            equity_curve.append(eq + (pos["locked"] if pos else 0))
            continue
        daily_ok = bool(d_close.iloc[di] > d_ema50.iloc[di])
        regime = bool(b_close.iloc[bi] > b_sma200.iloc[bi])

        if pos is None:
            if not regime:
                equity_curve.append(eq)
                continue
            sc = 0
            if daily_ok: sc += 20
            if e50.iloc[i] > e200.iloc[i] and px > e50.iloc[i]: sc += 20
            if px >= dh.iloc[i]: sc += 25
            vzv = vz.iloc[i]
            if _np.isfinite(vzv) and vzv > 1.5: sc += 15
            rv = rsi.iloc[i]
            if _np.isfinite(rv) and 55 <= rv <= 75: sc += 10
            av = adx.iloc[i]
            if _np.isfinite(av) and av > 20: sc += 10
            atrp = a / px * 100
            if sc >= 60 and 1.5 <= atrp <= 12.0:
                risk_usd = eq * risk_pct
                qty = risk_usd / (a * 2.5)
                qty = min(qty, (eq * 0.20) / px)
                stop = px - a * 2.5
                tp = px + a * 4.0
                risk_usd_actual = (px - stop) * qty
                entry_cost = qty * px * (1 + config.FEE_RATE + config.SLIPPAGE_RATE)
                if entry_cost > eq * 0.25 or qty * px < 25:
                    equity_curve.append(eq)
                    continue
                eq -= entry_cost
                pos = {"entry": px, "qty": qty, "stop": stop, "tp": tp,
                       "peak": px, "i": i, "locked": qty * px,
                       "risk_usd": risk_usd_actual,
                       "entry_time": str(df4h["open_time"].iloc[i])}
            equity_curve.append(eq + (pos["locked"] if pos else 0))
        else:
            pos["peak"] = max(pos["peak"], px)
            trail = pos["peak"] - a * 3.0
            pos["stop"] = max(pos["stop"], trail)
            reason = None
            if px <= pos["stop"]: reason = "stop/trail"
            elif px >= pos["tp"]: reason = "take-profit"
            elif rsi.iloc[i] > 80: reason = "rsi-exhaust"
            elif px < e50.iloc[i]: reason = "ema50-break"
            elif i - pos["i"] >= 60: reason = "time-stop"
            if reason:
                proceeds = px * pos["qty"] * (1 - config.FEE_RATE - config.SLIPPAGE_RATE)
                pnl = proceeds - pos["locked"]
                r_mult = (pnl / pos["risk_usd"]) if pos.get("risk_usd", 0) else 0.0
                eq += pos["locked"] + pnl
                trades.append({"entry": pos["entry"], "exit": px, "pnl": pnl,
                               "ret": pnl / pos["locked"], "reason": reason,
                               "bars": i - pos["i"], "R": float(r_mult),
                               "risk_usd": float(pos.get("risk_usd", 0.0)),
                               "locked": float(pos["locked"]),
                               "entry_time": pos["entry_time"],
                               "exit_time": str(df4h["open_time"].iloc[i])})
                pos = None
            equity_curve.append(eq + (pos["locked"] if pos else 0))
            peak = max(peak, eq + (pos["locked"] if pos else 0))
            dd = (eq + (pos["locked"] if pos else 0)) / peak - 1 if peak else 0
            max_dd = min(max_dd, dd)
    if pos is not None:  # mark to market
        px = float(c.iloc[-1])
        proceeds = px * pos["qty"] * (1 - config.FEE_RATE - config.SLIPPAGE_RATE)
        pnl = proceeds - pos["locked"]
        eq += pos["locked"] + pnl
    wins = sum(1 for t in trades if t["pnl"] > 0)
    gross_win = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    rets = [t["ret"] for t in trades]
    return {"equity": eq, "return_pct": (eq / equity - 1) * 100,
            "n_trades": len(trades),
            "win_rate": (wins / len(trades) if trades else 0),
            "profit_factor": (gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0),
            "expectancy_r": (float(_np.mean(rets)) * 100 if rets else 0.0),
            "max_drawdown_pct": max_dd * 100,
            "avg_bars": (float(_np.mean([t["bars"] for t in trades])) if trades else 0),
            "trades": trades, "equity_curve": equity_curve}
