"""Edge: Alt Rotation Breakout (swing 2-10 days, 4H entries, daily regime).

Score 0-100:
  daily close > EMA50 ............ +20
  4H EMA50 > EMA200 (uptrend) .... +20
  4H Donchian(30) breakout ....... +25
  volume z-score > 1.5 ........... +15
  RSI(14) in [55,75] ............. +10
  ADX(14) > 20 ................... +10
  RS vs BTC positive (bonus) ..... +10 (cap 100)

Filters (hard veto):
  BTC daily close > SMA200 (else risk-off, no new longs)
  ATR% in [1.5%, 12%] on 4H
  score >= 60
"""
import pandas as pd
from . import indicators as ind

ENTRY_SCORE_MIN = 60

def add_features(df4h: pd.DataFrame, dfd: pd.DataFrame, btc_daily: pd.DataFrame) -> dict:
    last = df4h.iloc[-1]
    feats = {}
    c = df4h["close"]
    feats["ema50_4h"] = ind.ema(c, 50).iloc[-1]
    feats["ema200_4h"] = ind.ema(c, 200).iloc[-1]
    feats["rsi"] = ind.rsi(c).iloc[-1]
    feats["adx"] = ind.adx(df4h).iloc[-1]
    feats["atr"] = ind.atr(df4h).iloc[-1]
    feats["atr_pct"] = feats["atr"] / last["close"] * 100
    feats["don_high30"] = ind.donchian_high(df4h["high"], 30).iloc[-1]
    feats["vol_z"] = ind.volume_zscore(df4h["volume"]).iloc[-1]
    feats["price"] = float(last["close"])

    dc = dfd["close"]
    feats["daily_ema50"] = ind.ema(dc, 50).iloc[-1]
    feats["daily_close"] = float(dfd["close"].iloc[-1])
    feats["above_daily_ema50"] = feats["daily_close"] > feats["daily_ema50"]

    b = btc_daily["close"]
    feats["btc_regime_ok"] = bool(b.iloc[-1] > ind.sma(b, 200).iloc[-1])
    return feats

def score_setup(df4h: pd.DataFrame, dfd: pd.DataFrame, btc_daily: pd.DataFrame,
                alt_btc_roc: float = 0.0) -> tuple[float, dict, dict]:
    f = add_features(df4h, dfd, btc_daily)
    score = 0
    reasons = {}
    if f["above_daily_ema50"]:
        score += 20; reasons["daily_trend"] = True
    if f["ema50_4h"] > f["ema200_4h"] and f["price"] > f["ema50_4h"]:
        score += 20; reasons["4h_uptrend"] = True
    if f["price"] >= (f["don_high30"] or 0):
        score += 25; reasons["breakout"] = True
    if (f["vol_z"] or 0) > 1.5:
        score += 15; reasons["vol_expand"] = True
    if 55 <= (f["rsi"] or 0) <= 75:
        score += 10; reasons["rsi_zone"] = True
    if (f["adx"] or 0) > 20:
        score += 10; reasons["adx_trend"] = True
    if alt_btc_roc > 0:
        score = min(100, score + 10); reasons["rel_strength_btc"] = True
    return min(100, score), f, reasons

def passes_filters(score: float, f: dict) -> tuple[bool, str]:
    if not f.get("btc_regime_ok"):
        return False, "BTC below SMA200 (risk-off)"
    if score < ENTRY_SCORE_MIN:
        return False, f"score {score:.0f} < {ENTRY_SCORE_MIN}"
    a = f.get("atr_pct", 0) or 0
    if not (1.5 <= a <= 12.0):
        return False, f"ATR% {a:.2f} outside [1.5,12]"
    return True, "ok"

def exit_signal(df4h: pd.DataFrame, entry_price: float, bars_held: int) -> tuple[bool, str]:
    """ATR trailing / stop / TP / RSI exhaustion / time stop evaluated by paper broker too;
    this is the indicator-based part (RSI + trend break + time)."""
    c = df4h["close"]
    rsi = ind.rsi(c).iloc[-1]
    e50 = ind.ema(c, 50).iloc[-1]
    px = float(df4h["close"].iloc[-1])
    if rsi > 80:
        return True, "RSI exhaustion >80"
    if px < e50:
        return True, "close < 4H EMA50"
    if bars_held >= 60:  # 60x4h = 10 days
        return True, "time stop 10d"
    return False, ""
