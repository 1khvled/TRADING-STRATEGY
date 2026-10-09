"""Paper broker for USDT-M swing longs (isolated margin). SQLite. NO live orders."""
import sqlite3, os
from datetime import datetime, timezone
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT, leverage INTEGER,
  entry_price REAL, qty REAL, notional REAL, margin REAL,
  stop REAL, take_profit REAL, liq_price REAL,
  entry_time TEXT, entry_score REAL, entry_atr REAL, risk_usd REAL,
  status TEXT DEFAULT 'OPEN', exit_price REAL, exit_time TEXT, exit_reason TEXT, pnl REAL, fund_acc REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT, side TEXT, qty REAL, price REAL, fee REAL, time TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS equity(
  time TEXT PRIMARY KEY, equity REAL
);
"""

def _db(db=None):
    db = db or config.FUTURES_DB
    os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
    c = sqlite3.connect(db, timeout=30)
    c.executescript(SCHEMA)
    return c, db

def equity_now(db=None, start=None):
    start = start if start is not None else config.PAPER_START_EQUITY
    c, _ = _db(db)
    try:
        r = c.execute("SELECT equity FROM equity ORDER BY time DESC LIMIT 1").fetchone()
        return float(r[0]) if r else float(start)
    finally:
        c.close()

def open_positions(db=None):
    c, _ = _db(db)
    try:
        desc = [d[0] for d in c.execute("SELECT * FROM positions LIMIT 1").description]
        rows = c.execute("SELECT * FROM positions WHERE status='OPEN'").fetchall()
        return [dict(zip(desc, r)) for r in rows]
    finally:
        c.close()

def size_futures(equity, entry, atr, leverage, risk_pct, stop_mult=2.5):
    risk_usd = equity * risk_pct
    stop_dist = atr * stop_mult
    if stop_dist <= 0 or entry <= 0:
        return None
    qty = risk_usd / stop_dist
    notional = qty * entry
    margin = notional / leverage
    if margin > equity * 0.30:
        qty = (equity * 0.30 * leverage) / entry
        notional = qty * entry; margin = notional / leverage
    liq_dist = 1.0 / leverage - config.FUTURES_MMR
    if stop_dist / entry >= liq_dist * 0.95 or notional < 25:
        return None
    fee = config.FUTURES_FEE_RATE + config.FUTURES_SLIPPAGE
    if margin + notional * fee > equity * 0.95:
        return None
    return {"qty": qty, "notional": notional, "margin": margin,
            "stop": entry - stop_dist, "tp": entry + atr * 4.0,
            "liq": entry * (1 - liq_dist), "risk_usd": stop_dist * qty}

def open_position(symbol, entry_price, qty, margin, stop, tp, liq, score, atr, risk_usd, leverage, db=None):
    fee = entry_price * qty * (config.FUTURES_FEE_RATE + config.FUTURES_SLIPPAGE)
    eq = equity_now(db)
    cost = margin + fee
    if cost > eq * 0.95:
        raise RuntimeError(f"insufficient futures paper equity for {symbol}")
    c, _ = _db(db)
    try:
        now = datetime.now(timezone.utc).isoformat()
        c.execute("""INSERT INTO positions(symbol,leverage,entry_price,qty,notional,margin,stop,take_profit,
                     liq_price,entry_time,entry_score,entry_atr,risk_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (symbol, leverage, entry_price, qty, entry_price * qty, margin, stop, tp, liq, now, score, atr, risk_usd))
        c.execute("INSERT INTO trades(symbol,side,qty,price,fee,time,note) VALUES(?,?,?,?,?,?,?)",
                  (symbol, "BUY", qty, entry_price, fee, now, f"fut paper entry lev={leverage}x score={score:.0f}"))
        c.execute("INSERT OR REPLACE INTO equity(time,equity) VALUES(?,?)", (now, eq - cost))
        c.commit()
    finally:
        c.close()

def close_position(pos, exit_price, reason, db=None):
    c, _ = _db(db)
    try:
        fee_rate = config.FUTURES_FEE_RATE + config.FUTURES_SLIPPAGE
        eq = equity_now(db)
        now = datetime.now(timezone.utc).isoformat()
        if reason == "liquidation":
            # margin already removed; record full loss incl fees+funding est
            open_fee = pos["notional"] * fee_rate
            pnl = -(pos["margin"] + open_fee + (pos["fund_acc"] or 0))
            c.execute("UPDATE positions SET status='CLOSED',exit_price=?,exit_time=?,exit_reason=?,pnl=? WHERE id=?",
                      (exit_price, now, reason, pnl, pos["id"]))
            c.execute("INSERT INTO trades(symbol,side,qty,price,fee,time,note) VALUES(?,?,?,?,?,?,?)",
                      (pos["symbol"], "SELL", pos["qty"], exit_price, 0, now, reason))
            c.commit()
            return pnl
        close_fee = exit_price * pos["qty"] * fee_rate
        gross = (exit_price - pos["entry_price"]) * pos["qty"]
        fund = pos.get("fund_acc") or 0
        # pnl net; free collateral += margin + gross - close_fee - funding_delta(not yet charged)
        c.execute("UPDATE positions SET status='CLOSED',exit_price=?,exit_time=?,exit_reason=?,pnl=? WHERE id=?",
                  (exit_price, now, reason, gross - fund - pos["notional"] * fee_rate - close_fee, pos["id"]))
        c.execute("INSERT INTO trades(symbol,side,qty,price,fee,time,note) VALUES(?,?,?,?,?,?,?)",
                  (pos["symbol"], "SELL", pos["qty"], exit_price, close_fee, now, reason))
        # accrue funding since entry: 8h slots elapsed
        c.execute("INSERT OR REPLACE INTO equity(time,equity) VALUES(?,?)",
                  (now, eq + pos["margin"] + gross - close_fee - fund))
        c.commit()
        return gross - fund - pos["notional"] * fee_rate - close_fee
    finally:
        c.close()

def accrue_funding(pos_id, amount, db=None):
    c, _ = _db(db)
    try:
        c.execute("UPDATE positions SET fund_acc = fund_acc + ? WHERE id=?", (amount, pos_id))
        now = datetime.now(timezone.utc).isoformat()
        eq = equity_now(db)
        c.execute("INSERT OR REPLACE INTO equity(time,equity) VALUES(?,?)", (now, eq - amount))
        c.commit()
    finally:
        c.close()

def update_trailing(pos_id, new_stop, db=None):
    c, _ = _db(db)
    try:
        c.execute("UPDATE positions SET stop=? WHERE id=?", (new_stop, pos_id))
        c.commit()
    finally:
        c.close()

def summary(db=None):
    c, _ = _db(db)
    try:
        try:
            desc = [d[0] for d in c.execute("SELECT * FROM positions LIMIT 1").description]
            allp = [dict(zip(desc, r)) for r in c.execute("SELECT * FROM positions").fetchall()]
        except Exception:
            allp = []
        openp = [p for p in allp if p["status"] == "OPEN"]
        closed = [p for p in allp if p["status"] == "CLOSED"]
        wins = sum(1 for p in closed if (p["pnl"] or 0) > 0)
        liqs = sum(1 for p in closed if p.get("exit_reason") == "liquidation")
        return {"equity": equity_now(db), "n_open": len(openp), "n_closed": len(closed),
                "win_rate": (wins / len(closed) if closed else 0),
                "realized_pnl": sum((p["pnl"] or 0) for p in closed),
                "liquidations": liqs, "open": openp, "closed": closed}
    finally:
        c.close()
