#!/usr/bin/env python3
"""
trend_scan.py — ranks NSE stocks by trend quality across two lookback windows.

    drift = OLS slope of ln(close) on session number, x252x100  (annualised, log terms)
    eff   = efficiency ratio: |net move| / sum(|daily moves|), 0..1
    score = drift x eff^2

Both drift and eff are computed on a WINSORISED log path (daily moves capped at
WINSOR_PCT). Displayed moves are uncapped.

Why efficiency ratio and not R-squared: fed driftless random walks, R-squared reads
a median of 0.44 and exceeds 0.8 on 14.5% of trials, and that false-positive rate
does NOT decay with window length (spurious regression against time). Efficiency
ratio on the same input reads 0.16 median with 0% above 0.8 at n=30. It also
correctly zeroes an up-then-reverse path, which R-squared fits as a tidy line.

Why the 5-session window is not rankable: at n=5 every consistency measure collapses
(pure noise reads ~0.48, a real trend ~0.90). It is emitted as a direction/turn
signal against the 10-session window, never as a sort key.

Data source: Yahoo Finance via yfinance (server-side, no CORS). EOD.
Output     : trend.json
"""

import json
import math
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

WINDOWS = [10, 5]          # sessions; first is the default ranking window
RANK_WINDOW = 10
SHORT_WINDOW = 5           # signal only, never a sort key
FETCH_DAYS = "2mo"         # only need max(WINDOWS)+1 sessions; keep a holiday buffer
BENCHMARK = "^NSEI"
WINSOR_PCT = 6.0           # daily cap applied before fitting (display stays raw)
GAP_FLAG_PCT = 15.0        # single session beyond this = event, not trend
MIN_TURNOVER_CR = 1.0      # median 20d turnover floor, Rs crore
MAX_STALE_FRAC = 0.30      # share of unchanged closes above which it is untradeable
TREND_MIN_SCORE = 10.0     # tiny floor, only to confirm a direction exists
TREND_MIN_EFF = 0.45
TURN_MIN_SCORE = 25.0      # short-window score needed to call a turn
BATCH = 40

UNIVERSE = [
    "IONEXCHANG.NS", "WABAG.NS", "JASH.NS", "KSB.NS", "KIRLOSBROS.NS",
    "HITACHIENERGY.NS", "SIEMENS.NS", "ABB.NS", "CGPOWER.NS", "TRANSFORMERS.NS",
    "SANSERA.NS", "DYNAMATECH.NS", "NRBBEARING.NS", "SCHAEFFLER.NS",
    "THYROCARE.NS", "LALPATHLAB.NS", "METROPOLIS.NS",
    "SOLARA.NS", "CAPRIGLOB.NS", "NAUKRI.NS",
    "RELIANCE.NS", "HDFCBANK.NS", "TCS.NS", "SUNPHARMA.NS", "LT.NS",
]


# ----------------------------------------------------------------------------
# Core maths
# ----------------------------------------------------------------------------

def winsorise(log_prices: np.ndarray) -> np.ndarray:
    """Rebuild the log path with each daily step capped at +/- WINSOR_PCT."""
    steps = np.diff(log_prices)
    cap = math.log(1 + WINSOR_PCT / 100.0)
    return np.concatenate([[log_prices[0]],
                           log_prices[0] + np.cumsum(np.clip(steps, -cap, cap))])


def window_stats(closes: np.ndarray, n: int):
    """Score one lookback window. `closes` must hold at least n values."""
    if len(closes) < n or np.any(closes <= 0):
        return None
    y = winsorise(np.log(closes[-n:]))
    x = np.arange(n, dtype=float)

    slope = float(np.polyfit(x, y, 1)[0])
    drift = max(-2000.0, min(2000.0, slope * 252 * 100.0))

    distance = float(np.abs(np.diff(y)).sum())
    eff = abs(y[-1] - y[0]) / distance if distance > 0 else 0.0
    eff = max(0.0, min(1.0, eff))

    return {
        "drift": round(drift, 1),
        "eff": round(eff, 3),
        "score": round(drift * eff * eff, 1),
        "ret": round((math.exp(y[-1] - y[0]) - 1) * 100.0, 2),
    }


def max_drawdown_pct(closes: np.ndarray) -> float:
    peak = np.maximum.accumulate(closes)
    return float(((closes / peak - 1.0).min()) * 100.0)


def classify(win: dict) -> str:
    """
    Reduce the windows to one readable state.

    Consistency (eff) is the gate, NOT score magnitude. An earlier version gated on
    |score| > 50 and wrongly called shallow-but-clean movers "choppy" — exactly the
    names a plain percent-change sort already buries, and the reason this tool exists.

    The 10-session window sets the trend and is the sort key; the 5-session window is
    used only for a sign flip with conviction (a "turn"), never as a rank.
    """
    lo = win.get(str(RANK_WINDOW))
    sh = win.get(str(SHORT_WINDOW))
    if lo is None:
        return "no data"

    up = lo["score"] > 0
    if lo["eff"] < TREND_MIN_EFF or abs(lo["score"]) < TREND_MIN_SCORE:
        return "choppy"

    if sh is not None and abs(sh["score"]) > TURN_MIN_SCORE and (sh["score"] > 0) != up:
        return "turning up" if sh["score"] > 0 else "turning down"

    return "trending up" if up else "trending down"


def analyse(symbol: str, df: pd.DataFrame, bench_ret: float | None):
    if df is None or df.empty:
        return None
    df = df.dropna(subset=["Close"])
    longest = max(WINDOWS)
    if len(df) < longest + 1:
        return None

    frame = df.iloc[-(longest + 1):]
    closes = frame["Close"].to_numpy(dtype=float)
    dates = [d.strftime("%Y-%m-%d") for d in frame.index[1:]]

    windows = {}
    for n in WINDOWS:
        st = window_stats(closes[1:], n)
        if st is not None:
            windows[str(n)] = st
    if str(RANK_WINDOW) not in windows:
        return None

    daily = (closes[1:] / closes[:-1] - 1.0) * 100.0
    total = (closes[-1] / closes[0] - 1.0) * 100.0
    stale_frac = float((np.abs(daily) < 0.001).mean())

    turnover_cr = None
    if "Volume" in frame:
        tv = (frame["Close"] * frame["Volume"]).dropna().tail(20)
        if len(tv):
            turnover_cr = float(tv.median()) / 1e7

    up_days = int((daily > 0).sum())
    biggest = float(np.abs(daily).max())

    return {
        "symbol": symbol.replace(".NS", ""),
        "windows": windows,
        "score": windows[str(RANK_WINDOW)]["score"],   # default sort key
        "state": classify(windows),
        "total": round(total, 2),
        "rel": round(total - bench_ret, 2) if bench_ret is not None else None,
        "up_days": up_days,
        "down_days": longest - up_days,
        "max_dd": round(max_drawdown_pct(closes[1:]), 2),
        "biggest_move": round(biggest, 2),
        "gappy": bool(biggest > GAP_FLAG_PCT),
        "turnover_cr": round(turnover_cr, 2) if turnover_cr is not None else None,
        "thin": bool(turnover_cr is not None and turnover_cr < MIN_TURNOVER_CR),
        "stale": bool(stale_frac > MAX_STALE_FRAC),
        "last": round(float(closes[-1]), 2),
        "daily": [round(float(v), 2) for v in daily],
        "dates": dates,
    }


# ----------------------------------------------------------------------------
# Fetch
# ----------------------------------------------------------------------------

def fetch(tickers):
    out = {}
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i + BATCH]
        print(f"  fetching {i + 1}-{i + len(chunk)} of {len(tickers)}", flush=True)
        raw = yf.download(chunk, period=FETCH_DAYS, interval="1d",
                          group_by="ticker", auto_adjust=True,
                          progress=False, threads=True)
        if raw is None or raw.empty:
            continue
        for t in chunk:
            try:
                sub = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                if sub.dropna(subset=["Close"]).empty:
                    continue
                out[t] = sub
            except KeyError:
                continue
    return out


def main():
    universe = UNIVERSE
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as fh:
            universe = [ln.strip() for ln in fh
                        if ln.strip() and not ln.lstrip().startswith("#")]

    print(f"universe: {len(universe)} symbols · windows {WINDOWS} · rank on {RANK_WINDOW}")

    print("benchmark…")
    bench_ret = None
    bench = fetch([BENCHMARK]).get(BENCHMARK)
    if bench is not None and len(bench) >= max(WINDOWS) + 1:
        bc = bench["Close"].dropna().to_numpy(dtype=float)[-(max(WINDOWS) + 1):]
        bench_ret = (bc[-1] / bc[0] - 1.0) * 100.0
        print(f"  Nifty {max(WINDOWS)}d: {bench_ret:+.2f}%")

    print("universe…")
    frames = fetch(universe)

    rows, failed = [], []
    for t in universe:
        r = analyse(t, frames.get(t), bench_ret)
        (rows.append(r) if r else failed.append(t))
    rows.sort(key=lambda r: r["score"], reverse=True)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "windows": WINDOWS,
        "rank_window": RANK_WINDOW,
        "short_window": SHORT_WINDOW,
        "benchmark_return": round(bench_ret, 2) if bench_ret is not None else None,
        "requested": len(universe),
        "resolved": len(rows),
        "failed": failed,
        "rows": rows,
    }
    with open("trend.json", "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))

    print(f"\nwrote trend.json — {len(rows)} scored, {len(failed)} unresolved")
    if failed:
        print("unresolved (fix the ticker or drop it):\n  " + ", ".join(failed))

    print("\n" + f"{'symbol':<15}" + "".join(f"{str(n)+'d':>8}" for n in WINDOWS) +
          f"{'eff':>7}{'state':>15}  flags")
    for r in rows:
        cells = "".join(f"{r['windows'][str(n)]['score']:>8.0f}"
                        if str(n) in r["windows"] else f"{'-':>8}" for n in WINDOWS)
        flags = " ".join(f for f, on in (("GAP", r["gappy"]), ("THIN", r["thin"]),
                                         ("STALE", r["stale"])) if on)
        print(f"{r['symbol']:<15}{cells}{r['windows'][str(RANK_WINDOW)]['eff']:>7.2f}"
              f"{r['state']:>15}  {flags}")


if __name__ == "__main__":
    main()
