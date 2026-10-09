"""Forward (paper) test driver — post-backtest, live-data paper only.
Usage:
  python scripts/forward_once.py --max-new 2
  PAPER_DB=data/forward.db python scripts/paper_trade_once.py --max-new 2
Runs one forward cycle against data/forward.db (fresh $10k).
Repeat 1-2x/day at 4H close for 2-4 weeks, then compare to backtest.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("PAPER_DB", "data/forward.db")
import runpy
sys.argv = ["paper_trade_once.py"] + sys.argv[1:]
runpy.run_path(os.path.join(os.path.dirname(__file__), "paper_trade_once.py"),
               run_name="__main__")
