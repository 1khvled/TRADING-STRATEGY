"""Position sizing: fixed fractional risk with ATR stop."""
from . import config

def size_position(equity: float, entry: float, atr: float,
                  risk_pct: float | None = None,
                  stop_mult: float = 2.5) -> dict:
    risk_pct = risk_pct if risk_pct is not None else config.RISK_PER_TRADE
    risk_usd = equity * risk_pct
    stop_dist = atr * stop_mult
    if stop_dist <= 0 or entry <= 0:
        return {"qty": 0.0, "notional": 0.0, "risk_usd": 0.0, "stop": 0.0}
    qty = risk_usd / stop_dist
    notional = qty * entry
    # Binance ~$5-10 min; use $25 floor for safety, cap single position 20% equity
    max_notional = equity * 0.20
    if notional > max_notional:
        qty = max_notional / entry
        notional = max_notional
    stop = entry - stop_dist
    return {"qty": qty, "notional": notional,
            "risk_usd": min(risk_usd, stop_dist * qty),
            "stop": stop, "stop_mult": stop_mult}
