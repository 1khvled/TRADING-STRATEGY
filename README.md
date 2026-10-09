# Alt Swing — Binance mid/low-tier swing system (paper-first)

Spot swing, 4H entries + daily regime. Universe = USDT spot ranked 30–180 by 24h quote volume,
always excluding BTC/ETH/XRP, stables, and leveraged tokens.

## Edge (Alt Rotation Breakout)
Score 0–100, enter ≥60 only when BTC daily close > SMA200 and 4H ATR% in [1.5,12]:
- daily > EMA50 (+20), 4H EMA50>EMA200 (+20), Donchian-30 breakout (+25),
  volume z>1.5 (+15), RSI 55–75 (+10), ADX>20 (+10), RS vs BTC bonus (+10)
Exits: 2.5×ATR stop, 3×ATR trail, ~4×ATR target, RSI>80, close<EMA50(4H), 10-day time stop.
Risk: 1% per trade, max 5 positions, ≤20% equity per name.

## Setup
    pip install -r requirements.txt
Keys optional for paper (public data only). `.env` already created from your API KEYS.txt.
Never share `.env`.

## Use
    python scripts/scan.py --limit 15          # read-only scan
    python scripts/backtest_one.py SOLUSDT      # rule check on one name
    python scripts/paper_trade_once.py --max-new 2   # FIRST PAPER TRADE (sqlite in data/paper.db)

Run paper cycle 1–2×/day (4H close). Promote to live only after 30+ paper trades + positive expectancy.
Live trading not enabled in this build — paper broker only, by design.
