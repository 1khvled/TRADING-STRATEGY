"""Paper broker: SQLite portfolio, fees+slippage, ATR exits. No real orders."""
import sqlite3, os, time, math
from datetime import datetime, timezone
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT, side TEXT DEFAULT 'LONG',
  entry_price REAL, qty REAL, notional REAL,
  stop REAL, take_profit REAL, trail_mult REAL DEFAULT 3.0,
  entry_time TEXT, entry_score REAL, entry_atr REAL,
  status TEXT DEFAULT 'OPEN', exit_price REAL, exit_time TEXT, exit_reason TEXT, pnl REAL
);
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT, side TEXT, qty REAL, price REAL, fee REAL, time TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS equity(
  time TEXT PRIMARY KEY, equity REAL
);
"""

def _conn(db=None):
    db = db or config.PAPER_DB
    os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    return c

def equity_now(db=None, start=None) -> float:
    start = start if start is not None else config.PAPER_START_EQUITY
    c = _conn(db)
    try:
        row = c.execute("SELECT equity FROM equity ORDER BY time DESC LIMIT 1").fetchone()
        if row:
            return float(row[0])
    finally:
        c.close()
    return float(start)

def set_equity(eq: float, db=None):
    c = _conn(db)
    try:
        c.execute("INSERT OR REPLACE INTO equity(time,equity) VALUES(?,?)",
                  (datetime.now(timezone.utc).isoformat(), eq))
        c.commit()
    finally:
        c.close()

def open_positions(db=None):
    c = _conn(db)
    try:
        cols = [d[0] for d in c.execute("SELECT * FROM positions LIMIT 1").description] if True else []
        rows = c.execute("SELECT * FROM positions WHERE status='OPEN'").fetchall()
        desc = [d[0] for d in c.execute("SELECT * FROM positions LIMIT 1").description]
        return [dict(zip(desc, r)) for r in rows]
    finally:
        c.close()

def open_position(symbol, entry_price, qty, stop, score, atr, db=None):
    fee = entry_price * qty * config.FEE_RATE
    slip = entry_price * qty * config.SLIPPAGE_RATE
    cost = entry_price * qty + fee + slip
    eq = equity_now(db)
    if cost > eq * 0.25:
        raise RuntimeError(f"not enough paper equity for {symbol}: need ${cost:.0f}")
    db = db or config.PAPER_DB
    os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
    c = sqlite3.connect(db, timeout=30)
    try:
        c.executescript(SCHEMA)
        now = datetime.now(timezone.utc).isoformat()
        tp = entry_price + (entry_price - stop) * (4.0 / 2.5)  # ~4R target from 2.5R stop
        c.execute("""INSERT INTO positions(symbol,entry_price,qty,notional,stop,take_profit,
                     entry_time,entry_score,entry_atr) VALUES(?,?,?,?,?,?,?,?,?)""",
                  (symbol, entry_price, qty, entry_price * qty, stop, tp, now, score, atr))
        c.execute("INSERT INTO trades(symbol,side,qty,price,fee,time,note) VALUES(?,?,?,?,?,?,?)",
                  (symbol, "BUY", qty, entry_price, fee + slip, now, f"paper entry score={score:.0f}"))
        c.execute("INSERT OR REPLACE INTO equity(time,equity) VALUES(?,?)", (now, eq - cost))
        c.commit()
    finally:
        c.close()

def close_position(pos, exit_price, reason, db=None):
    db = db or config.PAPER_DB
    os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
    eq = equity_now(db)
    c = sqlite3.connect(db, timeout=30)
    try:
        c.executescript(SCHEMA)
        fee = exit_price * pos["qty"] * (config.FEE_RATE + config.SLIPPAGE_RATE)
        proceeds = exit_price * pos["qty"] - fee
        pnl = proceeds - pos["notional"]
        now = datetime.now(timezone.utc).isoformat()
        c.execute("UPDATE positions SET status='CLOSED',exit_price=?,exit_time=?,exit_reason=?,pnl=? WHERE id=?",
                  (exit_price, now, reason, pnl, pos["id"]))
        c.execute("INSERT INTO trades(symbol,side,qty,price,fee,time,note) VALUES(?,?,?,?,?,?,?)",
                  (pos["symbol"], "SELL", pos["qty"], exit_price, fee, now, reason))
        # equity currently excludes locked capital; add back notional + pnl
        c.execute("INSERT OR REPLACE INTO equity(time,equity) VALUES(?,?)", (now, eq + pos["notional"] + pnl))
        c.commit()
        return pnl
    finally:
        c.close()

def update_trailing(pos_id, new_stop, db=None):
    c = _conn(db)
    try:
        c.execute("UPDATE positions SET stop=? WHERE id=?", (new_stop, pos_id))
        c.commit()
    finally:
        c.close()

def summary(db=None):
    c = _conn(db)
    try:
        desc = [d[0] for d in c.execute("SELECT * FROM positions LIMIT 1").description]
        allp = [dict(zip(desc, r)) for r in c.execute("SELECT * FROM positions").fetchall()]
        openp = [p for p in allp if p["status"] == "OPEN"]
        closed = [p for p in allp if p["status"] == "CLOSED"]
        wins = sum(1 for p in closed if (p["pnl"] or 0) > 0)
        pnl = sum((p["pnl"] or 0) for p in closed)
        return {"equity": equity_now(db), "n_open": len(openp), "n_closed": len(closed),
                "win_rate": (wins / len(closed) if closed else 0), "realized_pnl": pnl,
                "open": openp, "closed": closed}
    finally:
        c.close()
