"""Rigorous trade-based Monte Carlo + robustness stats.
Methods (all on closed trades only, no lookahead):
 1. Bootstrap with replacement (fixed-fractional compounding): R-multiples resampled,
    equity evolves eq *= (1 + R*risk_pct). Captures order risk + compounding.
 2. Shuffle-without-replacement (permutation): same trades, random order, same compounding.
    Isolates lucky/unlucky sequencing.
 3. Walk-forward split: first-half vs second-half of trades chronologically.
 4. Risk sweep: 0.5% / 1% / 2% per trade to show ruin scaling.
Limitations stated in output: ignores concurrent overlap, intrabar slippage beyond
fees, funding (spot), and regime clustering. Futures MC adds liquidation drag separately.
"""
import numpy as np

def _equity_from_R(R, risk_pct=0.01, start=10000.0):
    eq = start
    curve = [eq]
    peak = eq
    max_dd = 0.0
    for r in R:
        eq = eq * (1.0 + float(r) * risk_pct)
        # bankrupt guard
        if eq <= 0:
            eq = 0.0
            curve.append(eq)
            max_dd = -1.0
            break
        curve.append(eq)
        peak = max(peak, eq)
        dd = eq / peak - 1.0
        max_dd = min(max_dd, dd)
    return np.array(curve), max_dd

def bootstrap_R(R, n_sims=5000, risk_pct=0.01, start=10000.0, seed=7):
    rng = np.random.default_rng(seed)
    R = np.asarray(R, dtype=float)
    n = len(R)
    finals, dds, ruins, profits = [], [], [], []
    for _ in range(n_sims):
        sample = rng.choice(R, size=n, replace=True)
        curve, dd = _equity_from_R(sample, risk_pct, start)
        finals.append(curve[-1])
        dds.append(dd)
        ruins.append(dd <= -0.30)
        profits.append(curve[-1] > start)
    return np.array(finals), np.array(dds), np.array(ruins), np.array(profits)

def permutation_R(R, n_sims=2000, risk_pct=0.01, start=10000.0, seed=11):
    rng = np.random.default_rng(seed)
    R = np.asarray(R, dtype=float)
    finals, dds = [], []
    for _ in range(n_sims):
        sample = rng.permutation(R)
        curve, dd = _equity_from_R(sample, risk_pct, start)
        finals.append(curve[-1])
        dds.append(dd)
    return np.array(finals), np.array(dds)

def summarize(arr):
    arr = np.asarray(arr, dtype=float)
    return {
        "mean": float(np.mean(arr)), "median": float(np.median(arr)),
        "p5": float(np.percentile(arr, 5)), "p25": float(np.percentile(arr, 25)),
        "p75": float(np.percentile(arr, 75)), "p95": float(np.percentile(arr, 95)),
        "min": float(np.min(arr)), "max": float(np.max(arr)),
    }

def base_stats(trades, risk_pct=0.01, start=10000.0):
    R = np.array([t.get("R", 0.0) for t in trades], dtype=float)
    rets = np.array([t.get("ret", 0.0) for t in trades], dtype=float)
    wins = float(np.mean([1 if t.get("pnl", 0) > 0 else 0 for t in trades])) if trades else 0.0
    gw = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)
    expR = float(np.mean(R)) if len(R) else 0.0
    # Sharpe on per-trade R (not annualized; diagnostic only)
    sharpe_tr = float(np.mean(R) / (np.std(R) + 1e-12)) if len(R) else 0.0
    # Walk-forward: chronological split
    ts = sorted(trades, key=lambda t: str(t.get("exit_time", "")))
    h = len(ts) // 2
    def _pf(slice_):
        a = sum(t["pnl"] for t in slice_ if t["pnl"] > 0)
        b = -sum(t["pnl"] for t in slice_ if t["pnl"] <= 0)
        return a / b if b > 0 else 0.0
    wf = {}
    if h > 0:
        wf = {"n1": h, "n2": len(ts) - h,
              "pf1": _pf(ts[:h]), "pf2": _pf(ts[h:]),
              "win1": float(np.mean([t["pnl"] > 0 for t in ts[:h]])),
              "win2": float(np.mean([t["pnl"] > 0 for t in ts[h:]])),
              "expR1": float(np.mean([t.get("R", 0) for t in ts[:h]])),
              "expR2": float(np.mean([t.get("R", 0) for t in ts[h:]]))}
    return {"n": len(trades), "win_rate": wins, "profit_factor": float(pf),
            "expectancy_R": expR, "sharpe_per_trade": sharpe_tr,
            "mean_ret": float(np.mean(rets)) if len(rets) else 0.0,
            "walkforward": wf}
