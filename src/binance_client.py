"""Minimal Binance REST client (public + optional signed). No SDK needed."""
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from . import config

_session = requests.Session()
_session.headers.update({"User-Agent": "alt-swing/1.0"})

def _url(path: str) -> str:
    return config.BINANCE_BASE_URL + path

def public_get(path: str, params: dict | None = None, timeout=20):
    for attempt in range(4):
        try:
            r = _session.get(_url(path), params=params or {}, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            if 400 <= r.status_code < 500 and r.status_code != 429:
                r.raise_for_status()  # don't retry client errors (e.g. bad symbol)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code == 429 or (code is not None and code >= 500):
                time.sleep(1.0 * (attempt + 1))
                continue
            raise
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(1.0 * (attempt + 1))

def klines(symbol: str, interval: str, limit: int = 300):
    data = public_get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    # [openTime, o,h,l,c, v, closeTime, quoteV, trades, takerBase, takerQuote, ignore]
    import pandas as pd
    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]
    df = pd.DataFrame(data, columns=cols)
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = __import__("pandas").to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = __import__("pandas").to_datetime(df["close_time"], unit="ms", utc=True)
    return df

def klines_paged(symbol: str, interval: str, total: int = 1500, end_ms: int | None = None):
    """Fetch >1000 klines via pagination (Binance caps at 1000/call). Returns oldest->newest."""
    import pandas as pd
    out = []
    end = end_ms
    remaining = total
    while remaining > 0:
        lim = min(1000, remaining)
        params = {"symbol": symbol, "interval": interval, "limit": lim}
        if end is not None:
            params["endTime"] = end
        batch = public_get("/api/v3/klines", params)
        if not batch:
            break
        out = batch + out
        remaining -= len(batch)
        end = batch[0][0] - 1  # paginate backwards
        if len(batch) < lim:
            break
        if len(out) >= total:
            break
    out = out[-total:]
    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]
    df = pd.DataFrame(out, columns=cols)
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df

def exchange_info():
    return public_get("/api/v3/exchangeInfo")

def ticker_24h(symbol: str | None = None):
    p = {"symbol": symbol} if symbol else {}
    return public_get("/api/v3/ticker/24hr", p)

def signed_request(method: str, path: str, params: dict | None = None):
    """Only for future live trading. Requires keys."""
    if not config.BINANCE_API_KEY or not config.BINANCE_API_SECRET:
        raise RuntimeError("BINANCE_API_KEY/SECRET not set")
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    qs = urlencode(params)
    sig = hmac.new(config.BINANCE_API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    headers = {"X-MBX-APIKEY": config.BINANCE_API_KEY}
    r = _session.request(method, _url(path) + "?" + qs + "&signature=" + sig, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()

# ---------- Futures (fapi host, geo-block resistant) ----------
def _furl(path: str) -> str:
    return config.BINANCE_FUTURES_URL + path

def futures_get(path: str, params: dict | None = None, timeout=20):
    for attempt in range(4):
        try:
            r = _session.get(_furl(path), params=params or {}, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            if 400 <= r.status_code < 500 and r.status_code != 429:
                r.raise_for_status()
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code == 429 or (code is not None and code >= 500):
                time.sleep(1.0 * (attempt + 1))
                continue
            raise
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(1.0 * (attempt + 1))

def futures_klines(symbol: str, interval: str, limit: int = 500):
    import pandas as pd
    data = futures_get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]
    df = pd.DataFrame(data, columns=cols)
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df

def futures_klines_paged(symbol: str, interval: str, total: int = 1200):
    import pandas as pd
    out, end, remaining = [], None, total
    while remaining > 0:
        lim = min(1000, remaining)
        p = {"symbol": symbol, "interval": interval, "limit": lim}
        if end is not None:
            p["endTime"] = end
        batch = futures_get("/fapi/v1/klines", p)
        if not batch:
            break
        out = batch + out
        remaining -= len(batch)
        end = batch[0][0] - 1
        if len(batch) < lim or len(out) >= total:
            break
    out = out[-total:]
    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]
    df = pd.DataFrame(out, columns=cols)
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

def futures_exchange_info():
    return futures_get("/fapi/v1/exchangeInfo")

def futures_ticker_24h():
    return futures_get("/fapi/v1/ticker/24hr")
