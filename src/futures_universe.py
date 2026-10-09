"""Futures universe: mid/low-tier USDT-M perps. Excludes BTC/ETH/XRP."""
from . import config, binance_client

def build_futures_universe(min_quote_vol: float | None = None,
                           rank_lo: int | None = None,
                           rank_hi: int | None = None) -> list[dict]:
    min_quote_vol = min_quote_vol if min_quote_vol is not None else config.MIN_QUOTE_VOL_24H
    rank_lo = rank_lo if rank_lo is not None else config.UNIVERSE_RANK_LO
    rank_hi = rank_hi if rank_hi is not None else config.UNIVERSE_RANK_HI
    import re as _re
    try:
        info = binance_client.futures_exchange_info()
        perps = set()
        for s in info.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            if s.get("contractType") != "PERPETUAL":
                continue
            if s.get("quoteAsset") != "USDT":
                continue
            sym = s.get("symbol", "")
            if not _re.fullmatch(r"[A-Z0-9]+USDT", sym or ""):
                continue
            base = s.get("baseAsset", sym.replace("USDT", ""))
            if base in config.ALWAYS_EXCLUDE_BASES or base in config.STABLE_BASES:
                continue
            perps.add(sym)
        tickers = binance_client.futures_ticker_24h()
        rows = []
        for t in tickers:
            sym = t.get("symbol", "")
            if sym not in perps:
                continue
            try:
                qv = float(t.get("quoteVolume", 0))
            except (TypeError, ValueError):
                continue
            if qv < min_quote_vol:
                continue
            rows.append({"symbol": sym, "quote_volume": qv,
                         "price": float(t.get("lastPrice", 0)),
                         "price_change_pct": float(t.get("priceChangePercent", 0))})
        rows.sort(key=lambda r: r["quote_volume"], reverse=True)
        return rows[max(0, rank_lo):min(len(rows), rank_hi)]
    except Exception as e:
        # fallback: spot proxy (perps track spot; costs handled in backtest)
        print(f"fapi unavailable ({e}), falling back to spot proxy universe")
        from . import universe as spot_uni
        return spot_uni.build_universe(min_quote_vol, rank_lo, rank_hi)
