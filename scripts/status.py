"""One-shot portfolio status printout. Usage: python scripts/status.py"""
import os, sys, sqlite3, glob
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from datetime import datetime, timezone
from src.config import SMART_SYMBOLS, BLOCKED_SYMBOLS

now = datetime.now(timezone.utc)
def parse_t(s):
    try:
        return datetime.fromisoformat(str(s))
    except Exception:
        return None

for db in sorted(glob.glob('data/*.db')):
    if db.endswith('_tmp.db'):
        continue
    c = sqlite3.connect(db)
    try:
        tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if not {'positions', 'trades', 'equity'} & set(tabs):
            continue
        print('=' * 78)
        print(db)
        # equity
        try:
            last = c.execute("SELECT time,equity FROM equity ORDER BY time DESC LIMIT 1").fetchone()
            print(f"  cash(last): {last[1]:>10.2f} at {last[0][:19]}" if last else '  cash: no rows')
        except Exception:
            print('  cash: ?')
        # open
        try:
            is_fut = 'futures' in db
            cols = "id,symbol,{lev}entry_price,stop,take_profit,entry_time,{lev2}qty,notional,status"
            if is_fut:
                sql = ("SELECT id,symbol,leverage,entry_price,stop,take_profit,entry_time,qty,notional,status "
                       "FROM positions WHERE status='OPEN' ORDER BY entry_time")
            else:
                sql = ("SELECT id,symbol,0,entry_price,stop,take_profit,entry_time,qty,notional,status "
                       "FROM positions WHERE status='OPEN' ORDER BY entry_time")
            openrows = c.execute(sql).fetchall()
        except Exception:
            openrows = []
        if openrows:
            print('  OPEN:')
            for r in openrows:
                rid, sym, lev, entry, stop, tp, etts, qty, notional, status = r
                et = parse_t(etts)
                age = '' if not et else f"{(now - et).total_seconds()/3600:.1f}h"
                print(f"    id={rid:>2} {sym:<11} lev={lev}x entry={entry:<12.6g} stop={stop:<12.6g} tp={tp:<12.6g} qty={qty:<10.2f} age={age}")
        else:
            print('  OPEN: none')
        # closed
        try:
            closed = c.execute(
                "SELECT symbol,pnl,exit_reason,exit_time FROM positions WHERE status='CLOSED' "
                "ORDER BY exit_time DESC LIMIT 5").fetchall()
            n = c.execute("SELECT COUNT(*) FROM positions WHERE status='CLOSED'").fetchone()[0]
            print(f'  closed: {n} total; last {len(closed)}:')
            for sym, pnl, reason, et in closed:
                print(f"    {et[:16] if et else '?'} {sym:<11} pnl={pnl:+.2f} ({reason})")
        except Exception as e:
            print('  closed: ?', e)
    finally:
        c.close()

print('=' * 78)
print(f"smart budget allows: {sorted(SMART_SYMBOLS)}")
print(f"blocked: {sorted(BLOCKED_SYMBOLS)}")
print(f"now UTC: {now.isoformat()}")