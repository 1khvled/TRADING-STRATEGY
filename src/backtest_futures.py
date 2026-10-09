"""Leveraged long-only swing backtest (USDT-M perps, isolated margin).
Same entries as spot. Differences: leverage, futures fees, funding drag,
liquidation kill-switch, margin-aware sizing.
CAUTION: liquidation makes tails fat. Paper only.
"""
import numpy as _np
import pandas as pd
from . import indicators as ind, config

def backtest_futures(df4h: pd.DataFrame, dfd: pd.DataFrame, btc_daily: pd.DataFrame,
                     equity=10000.0, risk_pct=0.01, leverage=3,
                     stop_mult=2.5, tp_mult=4.0) -> dict:
    leverage = max(1, min(int(leverage), config.FUTURES_MAX_LEV))
    fee = config.FUTURES_FEE_RATE + config.FUTURES_SLIPPAGE
    df4h = df4h.copy().sort_values("open_time").reset_index(drop=True)
    dfd = dfd.copy().sort_values("open_time").reset_index(drop=True)
    btc = btc_daily.copy().sort_values("open_time").reset_index(drop=True)

    c = df4h["close"]; e50 = ind.ema(c, 50); e200 = ind.ema(c, 200)
    rsi = ind.rsi(c); adx = ind.adx(df4h); atr = ind.atr(df4h)
    dh = ind.donchian_high(df4h["high"], 30); vz = ind.volume_zscore(df4h["volume"])
    d_close = dfd["close"]; d_ema50 = ind.ema(d_close, 50)
    b_close = btc["close"]; b_sma200 = ind.sma(b_close, 200)
    d_idx = _np.searchsorted(dfd["open_time"].values, df4h["open_time"].values, side="right") - 1
    b_idx = _np.searchsorted(btc["open_time"].values, df4h["open_time"].values, side="right") - 1

    trades, eq, peak, max_dd = [], equity, equity, 0.0
    pos, liqs = None, 0
    bars_held_funding = 0

    for i in range(210, len(df4h)):
        px = float(c.iloc[i]); a = float(atr.iloc[i])
        lo, hi = float(df4h["low"].iloc[i]), float(df4h["high"].iloc[i])
        if not (_np.isfinite(px) and _np.isfinite(a)) or a <= 0 or px <= 0:
            continue
        di, bi = int(d_idx[i]), int(b_idx[i])
        if di < 55 or bi < 205:
            continue
        regime = bool(b_close.iloc[bi] > b_sma200.iloc[bi])
        daily_ok = bool(d_close.iloc[di] > d_ema50.iloc[di])

        if pos is None:
            if not regime:
                continue
            sc = 0
            if daily_ok: sc += 20
            if e50.iloc[i] > e200.iloc[i] and px > e50.iloc[i]: sc += 20
            if px >= dh.iloc[i]: sc += 25
            if _np.isfinite(vz.iloc[i]) and vz.iloc[i] > 1.5: sc += 15
            if 55 <= (rsi.iloc[i] or 0) <= 75: sc += 10
            if (adx.iloc[i] or 0) > 20: sc += 10
            atrp = a / px * 100
            if sc >= 60 and 1.5 <= atrp <= 12.0:
                risk_usd = eq * risk_pct
                stop_dist = a * stop_mult
                qty = risk_usd / stop_dist
                notional = qty * px
                margin = notional / leverage
                # caps: margin <= 30% equity, notional <= lev*50% equity
                if margin > eq * 0.30:
                    qty = (eq * 0.30 * leverage) / px
                    notional = qty * px; margin = notional / leverage
                if notional > eq * leverage * 0.5:
                    qty = (eq * leverage * 0.5) / px
                    notional = qty * px; margin = notional / leverage
                if notional < 25 or margin > eq * 0.95:
                    continue
                # liquidation distance check: skip if stop is beyond liq (stop would never trigger)
                liq_dist_pct = (1.0 / leverage - config.FUTURES_MMR)
                stop_pct = stop_dist / px
                if stop_pct >= liq_dist_pct * 0.95:
                    continue  # leverage too high for this volatility
                open_fee = notional * fee
                eq -= (margin + open_fee)
                liq_price = px * (1 - liq_dist_pct)
                pos = {"entry": px, "qty": qty, "notional": notional, "margin": margin,
                       "stop": px - stop_dist, "tp": px + a * tp_mult, "peak": px,
                       "liq": liq_price, "i": i, "risk_usd": stop_dist * qty,
                       "fund_acc": 0.0, "entry_time": str(df4h["open_time"].iloc[i])}
        else:
            # funding every 2x4h bars (~8h)
            if (i - pos["i"]) % 2 == 0:
                f = pos["notional"] * config.FUNDING_PER_8H
                pos["fund_acc"] += f
                eq -= f
            # liquidation on intrabar low (conservative: use low).
            # margin + open fee + funding so far are all sunk.
            if lo <= pos["liq"]:
                liqs += 1
                open_fee = pos["notional"] * fee
                loss = pos["margin"] + open_fee + pos["fund_acc"]
                trades.append({"entry": pos["entry"], "exit": pos["liq"], "pnl": -loss,
                               "ret": -loss / pos["notional"], "R": -loss / pos["risk_usd"],
                               "reason": "liquidation", "bars": i - pos["i"], "lev": leverage,
                               "risk_usd": pos["risk_usd"], "locked": pos["notional"],
                               "entry_time": pos["entry_time"],
                               "exit_time": str(df4h["open_time"].iloc[i])})
                pos = None
                peak = max(peak, eq); max_dd = min(max_dd, eq / peak - 1 if peak else 0)
                continue
            pos["peak"] = max(pos["peak"], px)
            pos["stop"] = max(pos["stop"], pos["peak"] - a * 3.0)
            reason = None
            if px <= pos["stop"]: reason = "stop/trail"
            elif px >= pos["tp"]: reason = "take-profit"
            elif rsi.iloc[i] > 80: reason = "rsi-exhaust"
            elif px < e50.iloc[i]: reason = "ema50-break"
            elif i - pos["i"] >= 60: reason = "time-stop"
            if reason:
                # eq currently excludes locked margin and has funding/open_fee sunk.
                # On close: free collateral += margin + gross - close_fee.
                close_fee = pos["qty"] * px * fee
                gross = (px - pos["entry"]) * pos["qty"]
                open_fee = pos["notional"] * fee
                pnl = gross - pos["fund_acc"] - open_fee - close_fee
                eq += pos["margin"] + gross - close_fee
                R = pnl / pos["risk_usd"] if pos["risk_usd"] else 0.0
                trades.append({"entry": pos["entry"], "exit": px, "pnl": pnl,
                               "ret": pnl / pos["notional"], "R": float(R),
                               "reason": reason, "bars": i - pos["i"], "lev": leverage,
                               "risk_usd": pos["risk_usd"], "locked": pos["notional"],
                               "entry_time": pos["entry_time"],
                               "exit_time": str(df4h["open_time"].iloc[i])})
                pos = None
            peak = max(peak, eq); max_dd = min(max_dd, eq / peak - 1 if peak else 0)
    if pos is not None:
        px = float(c.iloc[-1])
        gross = (px - pos["entry"]) * pos["qty"]
        eq += pos["margin"] + gross - pos["qty"] * px * fee
        # margin back + gross minus close fee (open fee + funding already sunk)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    gw = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return {"equity": eq, "return_pct": (eq / equity - 1) * 100, "n_trades": len(trades),
            "win_rate": (wins / len(trades) if trades else 0),
            "profit_factor": (gw / gl if gl > 0 else float("inf") if gw > 0 else 0),
            "max_drawdown_pct": max_dd * 100, "liquidations": liqs,
            "avg_bars": float(_np.mean([t["bars"] for t in trades])) if trades else 0,
            "trades": trades}
