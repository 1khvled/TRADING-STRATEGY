"""Universe: mid/low-tier USDT spot alts. Excludes BTC/ETH/XRP."""
from . import config, binance_client

def build_universe(min_quote_vol: float | None = None,
                   rank_lo: int | None = None,
                   rank_hi: int | None = None) -> list[dict]:
    min_quote_vol = min_quote_vol if min_quote_vol is not None else config.MIN_QUOTE_VOL_24H
    rank_lo = rank_lo if rank_lo is not None else config.UNIVERSE_RANK_LO
    rank_hi = rank_hi if rank_hi is not None else config.UNIVERSE_RANK_HI

    info = binance_client.exchange_info()
    spot_usdt = set()
    for s in info["symbols"]:
        if s["status"] != "TRADING" or not s.get("isSpotTradingAllowed", True):
            continue
        if "SPOT" not in str(s.get("permissionSets", "SPOT")) and s.get("permissionSets"):
            # permissionSets is list-of-lists; require SPOT in first set if present
            try:
                if "SPOT" not in s["permissionSets"][0]:
                    continue
            except Exception:
                pass
        if s["quoteAsset"] != "USDT":
            continue
        sym = s["symbol"]
        import re as _re
        if not _re.fullmatch(r"[A-Z0-9]+USDT", sym or ""):
            continue
        if sym.endswith(config.LEVERAGED_SUFFIXES):
            continue
        base = s["baseAsset"]
        if base in config.ALWAYS_EXCLUDE_BASES or base in config.STABLE_BASES:
            continue
        spot_usdt.add(sym)

    tickers = binance_client.ticker_24h()
    rows = []
    for t in tickers:
        sym = t["symbol"]
        if sym not in spot_usdt:
            continue
        try:
            qv = float(t["quoteVolume"])
            px = float(t["lastPrice"])
        except (KeyError, ValueError):
            continue
        if qv < min_quote_vol or px <= 0:
            continue
        rows.append({"symbol": sym, "quote_volume": qv, "price": px,
                     "price_change_pct": float(t.get("priceChangePercent", 0))})

    rows.sort(key=lambda r: r["quote_volume"], reverse=True)
    # rank_lo..rank_hi = mid/low tier (skip mega-caps at top)
    lo = max(0, rank_lo)
    hi = min(len(rows), rank_hi)
    return rows[lo:hi]
