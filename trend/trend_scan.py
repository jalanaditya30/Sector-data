#!/usr/bin/env python3
"""
trend_scan.py — ranks NSE stocks by trend quality across three lookback windows.

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

Windows are 15 / 10 / 5 sessions. The 15-session window sets the trend, supplies
the consistency figure and is the sort key; 10 flags a trend that is cooling; 5 flags
a turn.

Why the 5-session window is not rankable: at n=5 every consistency measure collapses
(pure noise reads ~0.48, a real trend ~0.90). It is emitted as a direction/turn
signal only, never as a sort key.

Measured on driftless random walks, the share of pure noise clearing the
consistency gate falls sharply with window length: 53% at n=5, 30% at n=10,
18% at n=15. That is why 15 is the ranking window.

Identity: every scanned row is keyed on its ISIN, read from the repo-root
registry `stocks.csv`. The Yahoo symbol is only how the price is fetched — it can
be renamed (MACROTECH -> LODHA) or absent (BSE-only listings fetch as <code>.BO),
and neither event should change which company a row refers to.

Data source: Yahoo Finance via yfinance (server-side, no CORS). EOD.
Output     : trend.json
"""

import json
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

# stocks.csv and its loader live at the repo root; this script runs from trend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stock_registry as registry  # noqa: E402

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

WINDOWS = [15, 10, 5]      # sessions; first is the default ranking window
RANK_WINDOW = 15           # sets the trend, the consistency shown, and the sort
MID_WINDOW = 10            # confirmation only — flags a trend that is cooling
SHORT_WINDOW = 5           # signal only, never a sort key
# need max(WINDOWS)+1 closes for the fit and RVOL_RECENT+RVOL_BASE for volume
FETCH_DAYS = "3mo"
RVOL_RECENT = 5            # sessions in the "now" leg of relative volume
RVOL_BASE = 20             # sessions in the baseline leg
TURNOVER_LOOKBACK = 20     # sessions for the median turnover liquidity floor
BENCHMARK = "^NSEI"
WINSOR_PCT = 6.0           # daily cap applied before fitting (display stays raw)
GAP_FLAG_PCT = 15.0        # single session beyond this = event, not trend
MIN_TURNOVER_CR = 1.0      # median 20d turnover floor, Rs crore
MAX_STALE_FRAC = 0.30      # share of unchanged closes above which it is untradeable
TREND_MIN_SCORE = 10.0     # tiny floor, only to confirm a direction exists
# Consistency gate. Was 0.45, calibrated when the ranking window was 30 sessions.
# At n=15 that let ~18% of driftless random walks through — about 364 false trend
# badges across the 1,989-name universe. 0.75 cuts that to ~1.7% (~34 names) at the
# cost of calling some genuine-but-untidy trends "choppy", which is the right trade
# for a screen — and it matters more now the scan covers every listed company.
TREND_MIN_EFF = 0.75
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


def turnover_series(df: pd.DataFrame):
    """Daily traded value in Rs crore, over the WHOLE frame passed in.

    Turnover (price x volume), not share count: comparable across stocks and
    unaffected by a share price that moved during the window.
    """
    if "Volume" not in df:
        return None
    tv = (df["Close"] * df["Volume"]).dropna()
    return (tv / 1e7) if len(tv) else None


def relative_volume(turnover):
    """Is the move being backed by participation?

        rvol = median turnover of the last RVOL_RECENT sessions
             / median turnover of the RVOL_BASE sessions before those

    Above ~1.5 means money is arriving as the trend runs; below ~0.8 means the
    move is happening on fading interest, which is the weaker setup. Medians
    (not means) so one block deal cannot set the level.
    """
    if turnover is None or len(turnover) < RVOL_RECENT + RVOL_BASE:
        return None
    recent = float(turnover.iloc[-RVOL_RECENT:].median())
    base = float(turnover.iloc[-(RVOL_RECENT + RVOL_BASE):-RVOL_RECENT].median())
    if base <= 0:
        return None
    return recent / base


def classify(win: dict) -> str:
    """
    Reduce the windows to one readable state.

    Consistency (eff) is the gate, NOT score magnitude. An earlier version gated on
    |score| > 50 and wrongly called shallow-but-clean movers "choppy" — exactly the
    names a plain percent-change sort already buries, and the reason this tool exists.

    The 15-session window sets the trend, supplies the consistency shown, and is the
    sort key. The 5-session window can only flag a TURN (a recent flip with
    conviction). The 10-session window sits between them and flags COOLING — the
    trend is intact over 15 sessions but the middle leg has already rolled over.
    """
    lo = win.get(str(RANK_WINDOW))
    mid = win.get(str(MID_WINDOW))
    sh = win.get(str(SHORT_WINDOW))
    if lo is None:
        return "no data"

    up = lo["score"] > 0
    if lo["eff"] < TREND_MIN_EFF or abs(lo["score"]) < TREND_MIN_SCORE:
        return "choppy"

    if sh is not None and abs(sh["score"]) > TURN_MIN_SCORE and (sh["score"] > 0) != up:
        return "turning up" if sh["score"] > 0 else "turning down"

    if mid is not None and abs(mid["score"]) > TURN_MIN_SCORE and (mid["score"] > 0) != up:
        return "cooling up" if up else "cooling down"

    return "trending up" if up else "trending down"


def analyse(symbol: str, df: pd.DataFrame, bench_ret: float | None,
            meta: dict | None = None):
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

    # Volume is measured on the FULL history, not `frame`: the price windows only
    # need 16 closes, but relative volume needs 25 sessions of baseline. Reading
    # it off `frame` silently truncated the median to the window length.
    turnover = turnover_series(df)
    turnover_cr = (float(turnover.iloc[-TURNOVER_LOOKBACK:].median())
                   if turnover is not None and len(turnover) else None)
    rvol = relative_volume(turnover)

    up_days = int((daily > 0).sum())
    biggest = float(np.abs(daily).max())

    meta = meta or {}
    # A BSE-only listing has no NSE symbol — its scrip code is the right short
    # label there, and the exchange field below tells the UI to say so. Only a
    # symbol the registry does not know at all falls through to the raw ticker.
    display = (meta.get("nse") or meta.get("bse")
               or meta.get("isin") or symbol.rsplit(".", 1)[0])

    return {
        "isin": meta.get("isin"),          # the identity — stable across renames
        "symbol": display,
        "yahoo": symbol,                   # only how the price was fetched
        "exchange": "BSE" if symbol.endswith(".BO") else "NSE",
        "name": meta.get("name") or display,
        "industry": meta.get("industry_group") or None,
        "windows": windows,
        "score": windows[str(RANK_WINDOW)]["score"],   # default sort key
        "state": classify(windows),
        "total": round(total, 2),
        "rel": round(total - bench_ret, 2) if bench_ret is not None else None,
        "up_days": up_days,
        "down_days": longest - up_days,
        "biggest_move": round(biggest, 2),
        "gappy": bool(biggest > GAP_FLAG_PCT),
        "turnover_cr": round(turnover_cr, 2) if turnover_cr is not None else None,
        "rvol": round(rvol, 2) if rvol is not None else None,
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
        universe = registry.read_universe(sys.argv[1])

    print(f"universe: {len(universe)} symbols · windows {WINDOWS} · rank on {RANK_WINDOW}")

    # Identity comes from stocks.csv, not from the ticker. A symbol the registry
    # does not know still gets scanned — it just carries no ISIN, and says so.
    try:
        rows_reg = registry.load()
        meta = registry.by_yahoo(rows_reg)
        print(f"  registry: {len(rows_reg)} companies, {len(meta)} with a price symbol")
    except FileNotFoundError:
        meta = {}
        print("  note: stocks.csv not found — rows will carry no ISIN")
    unknown = [t for t in universe if t not in meta]
    if unknown:
        print(f"  {len(unknown)} symbols not in the registry (no ISIN): "
              + ", ".join(unknown[:8]) + ("…" if len(unknown) > 8 else ""))

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
        r = analyse(t, frames.get(t), bench_ret, meta.get(t))
        (rows.append(r) if r else failed.append(t))
    rows.sort(key=lambda r: r["score"], reverse=True)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "identity": "isin",                # rows are keyed on ISIN, not ticker
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
          f"{'eff':>7}{'rvol':>7}{'state':>15}  flags")
    for r in rows:
        cells = "".join(f"{r['windows'][str(n)]['score']:>8.0f}"
                        if str(n) in r["windows"] else f"{'-':>8}" for n in WINDOWS)
        flags = " ".join(f for f, on in (("GAP", r["gappy"]), ("THIN", r["thin"]),
                                         ("STALE", r["stale"])) if on)
        rv = f"{r['rvol']:>7.2f}" if r["rvol"] is not None else f"{'-':>7}"
        print(f"{r['symbol']:<15}{cells}{r['windows'][str(RANK_WINDOW)]['eff']:>7.2f}"
              f"{rv}{r['state']:>15}  {flags}")


if __name__ == "__main__":
    main()
