"""Monte Carlo rigor on closed trades.
Usage:
  python scripts/monte_carlo.py --input data/backtest_20261003_1421.json --sims 5000 --risk 0.01
  python scripts/monte_carlo.py --input data/futures_backtest_*.json --sims 5000 --risk 0.01 --futures
Reads trades[{pnl,R,exit_time}], runs bootstrap + permutation + walk-forward + risk sweep.
Saves data/montecarlo_<ts>.json. Paper only.
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import glob
import numpy as np
from src import montecarlo as mc

def safe(s):
    return s.encode("ascii", "ignore").decode()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=str, default=None)
    ap.add_argument("--sims", type=int, default=5000)
    ap.add_argument("--risk", type=float, default=0.01)
    ap.add_argument("--start", type=float, default=10000.0)
    ap.add_argument("--futures", action="store_true")
    a = ap.parse_args()

    path = a.input
    if not path:
        cands = sorted(glob.glob("data/*backtest_*.json"))
        if not cands:
            print("no backtest json found. Run backtest_universe first.")
            sys.exit(1)
        path = cands[-1]
    d = json.load(open(path, encoding="utf-8"))
    trades = d.get("trades", [])
    trades = [t for t in trades if isinstance(t.get("pnl"), (int, float))]
    if len(trades) < 20:
        print(f"only {len(trades)} trades - Monte Carlo unreliable (<20). Get more history first.")
    # ensure R present (older files lack it) -> approximate R from ret via 2.5*ATR unknown; fallback: R = sign*1
    missing = sum(1 for t in trades if not t.get("R"))
    if missing:
        print(f"WARNING: {missing}/{len(trades)} trades lack R-multiple; approximating R from pnl sign/size via ret/0.04.")
        for t in trades:
            if not t.get("R"):
                t["R"] = float(t.get("ret", 0) / 0.04)  # ~4% stop proxy, disclosed approximation
    R = np.array([t["R"] for t in trades], dtype=float)

    base = mc.base_stats(trades, a.risk, a.start)
    finals, dds, ruins, profits = mc.bootstrap_R(R, a.sims, a.risk, a.start)
    pf, pdf = mc.permutation_R(R, min(2000, a.sims), a.risk, a.start)
    feq = a.start * float(np.mean([1 for _ in [0]] or [0]))  # placeholder no-op
    bs = mc.summarize(finals)
    ds = mc.summarize(dds * 100)
    out = {
        "input": path, "n_trades": len(trades), "risk_pct": a.risk, "sims": a.sims,
        "base": base,
        "bootstrap": {"final_equity": bs, "max_dd_pct": ds,
                      "prob_profit": float(np.mean(profits)),
                      "prob_ruin_30dd": float(np.mean(ruins))},
        "permutation": {"final_equity": mc.summarize(pf)},
        "risk_sweep": {},
    }
    for r in (0.005, 0.01, 0.02):
        f2, d2, ru2, pr2 = mc.bootstrap_R(R, 2000, r, a.start, seed=99)
        out["risk_sweep"][str(r)] = {
            "median_equity": float(np.median(f2)),
            "p5_equity": float(np.percentile(f2, 5)),
            "prob_ruin_30dd": float(np.mean(ru2)),
            "prob_profit": float(np.mean(pr2))}
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    tag = "futures_" if a.futures else ""
    op = f"data/montecarlo_{tag}{ts}.json"
    json.dump(out, open(op, "w", encoding="utf-8"), indent=1)

    print(safe(f"input: {path} | n={len(trades)}"))
    print(safe(f"base: win={base['win_rate']:.0%} PF={base['profit_factor']:.2f} expR={base['expectancy_R']:+.2f}R sharpe/tr={base['sharpe_per_trade']:+.2f}"))
    wf = base.get("walkforward", {})
    if wf:
        print(safe(f"walk-forward: 1H n={wf['n1']} PF={wf['pf1']:.2f} expR={wf['expR1']:+.2f} | 2H n={wf['n2']} PF={wf['pf2']:.2f} expR={wf['expR2']:+.2f}"))
        if wf["pf2"] < 1.0:
            print("WARNING: second-half edge decayed (PF2<1). Treat live with skepticism.")
    print(safe(f"MC bootstrap x{a.sims} @risk={a.risk:.1%}: median eq=${bs['median']:,.0f} "
          f"p5=${bs['p5']:,.0f} p95=${bs['p95']:,.0f}"))
    print(safe(f"  prob_profit={float(np.mean(profits)):.0%} prob_30pct_ruin={float(np.mean(ruins)):.1%} "
          f"median_DD={np.median(dds)*100:.1f}% p95_DD={np.percentile(dds,5)*100:.1f}%"))
    for r, s in out["risk_sweep"].items():
        print(safe(f"  risk {float(r):.1%}: median=${s['median_equity']:,.0f} p5=${s['p5_equity']:,.0f} "
              f"ruin={s['prob_ruin_30dd']:.1%} profit={s['prob_profit']:.0%}"))
    if out["bootstrap"]["prob_ruin_30dd"] > 0.05:
        print("VERDICT: tail risk too high (>5% ruin) at this risk - halve size or stay spot.")
    elif base["profit_factor"] < 1.2 or base["expectancy_R"] <= 0:
        print("VERDICT: no robust edge - do NOT size up.")
    elif wf and wf.get("pf2", 0) < 1.0:
        print("VERDICT: edge not stable OOS - forward-paper only, min size.")
    else:
        print("VERDICT: survives MC smoke test - forward-paper at same risk, re-check monthly.")
    print(safe(f"saved {op}"))
    print("Limits: trade-bootstrap ignores overlap/clustering; futures liq clustering understated.")

if __name__ == "__main__":
    main()
