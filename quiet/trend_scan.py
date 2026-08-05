#!/usr/bin/env python3
"""
trend_scan.py — finds stocks that grind quietly upward.

The goal: a big 10-15% pop is already all over Twitter. What gets missed is the stock
that adds 0.8% a day, most days, for six weeks. This finds those.

Two numbers do all the work, both of which you can check by eye on a chart:

  up_days      how many of the last 30 sessions closed higher than the day before
  biggest_day  the largest single-day move in the window

A stock up 18% where no day moved more than 2.5% climbed in thirty small steps.
A stock up 18% where one day moved 15% had one event and 29 days of nothing.
Same return. Only the first is a trend.

No regressions, no log scales, no statistics. Just counting.

Data: Yahoo Finance via yfinance. EOD.
Output: trend.json
"""

import json, sys
from datetime import datetime, timezone
import numpy as np, pandas as pd, yfinance as yf

DAYS = 30                # sessions in the window
FETCH = "4mo"
QUIET_DAY = 5.0          # a single move above this is an "event", not a grind
STRONG_UP = 20           # up-days out of DAYS needed to call it a grind
MIN_TURNOVER_CR = 1.0
BATCH = 40

UNIVERSE = ["IONEXCHANG.NS","WABAG.NS","JASH.NS","KSB.NS","KIRLOSBROS.NS",
 "HITACHIENERGY.NS","SIEMENS.NS","ABB.NS","CGPOWER.NS","TRANSFORMERS.NS",
 "SANSERA.NS","DYNAMATECH.NS","NRBBEARING.NS","SCHAEFFLER.NS","THYROCARE.NS",
 "LALPATHLAB.NS","METROPOLIS.NS","SOLARA.NS","CAPRIGLOB.NS","NAUKRI.NS",
 "RELIANCE.NS","HDFCBANK.NS","TCS.NS","SUNPHARMA.NS","LT.NS"]


def verdict(up_days, total, biggest, days):
    """Plain-English label. Deliberately only four outcomes."""
    if biggest > QUIET_DAY:
        return "one big day"           # an event, not a grind — you already heard about it
    if up_days >= STRONG_UP and total > 0:
        return "quiet climb"          # the thing we are hunting
    if (days - up_days) >= STRONG_UP and total < 0:
        return "quiet slide"
    return "no pattern"


def analyse(symbol, df):
    if df is None or df.empty:
        return None
    df = df.dropna(subset=["Close"])
    if len(df) < DAYS + 1:
        return None

    w = df.iloc[-(DAYS + 1):]
    closes = w["Close"].to_numpy(float)
    daily = (closes[1:] / closes[:-1] - 1) * 100
    total = (closes[-1] / closes[0] - 1) * 100

    up_days = int((daily > 0).sum())
    biggest = float(np.abs(daily).max())
    typical = float(np.median(np.abs(daily)))

    # longest run of consecutive up days — the most literal reading of "continuously up"
    best = run = 0
    for v in daily:
        run = run + 1 if v > 0 else 0
        best = max(best, run)

    # how much of the whole move came from the single largest day
    biggest_share = min(100.0, abs(biggest / total) * 100) if abs(total) > 0.01 else 100.0

    turnover = None
    if "Volume" in w:
        tv = (w["Close"] * w["Volume"]).dropna().tail(20)
        if len(tv):
            turnover = float(tv.median()) / 1e7

    return {
        "symbol": symbol.replace(".NS", ""),
        "up_days": up_days,
        "days": DAYS,
        "total": round(total, 1),
        "typical": round(typical, 2),
        "biggest": round(biggest, 1),
        "biggest_share": round(biggest_share),
        "streak": best,
        "recent_up": int((daily[-5:] > 0).sum()),
        "verdict": verdict(up_days, total, biggest, DAYS),
        "turnover_cr": round(turnover, 1) if turnover is not None else None,
        "thin": bool(turnover is not None and turnover < MIN_TURNOVER_CR),
        "last": round(float(closes[-1]), 2),
        "daily": [round(float(v), 2) for v in daily],
        "dates": [d.strftime("%Y-%m-%d") for d in w.index[1:]],
    }


def fetch(tickers):
    out = {}
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i+BATCH]
        print(f"  {i+1}-{i+len(chunk)} of {len(tickers)}", flush=True)
        raw = yf.download(chunk, period=FETCH, interval="1d", group_by="ticker",
                          auto_adjust=True, progress=False, threads=True)
        if raw is None or raw.empty:
            continue
        for t in chunk:
            try:
                sub = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                if not sub.dropna(subset=["Close"]).empty:
                    out[t] = sub
            except KeyError:
                pass
    return out


def main():
    universe = UNIVERSE
    if len(sys.argv) > 1:
        universe = [l.strip() for l in open(sys.argv[1])
                    if l.strip() and not l.lstrip().startswith("#")]
    print(f"{len(universe)} symbols, {DAYS}-session window")

    frames = fetch(universe)
    rows, failed = [], []
    for t in universe:
        r = analyse(t, frames.get(t))
        (rows.append(r) if r else failed.append(t))

    # sort: quiet climbs first, then by up-day count, then by size of move
    order = {"quiet climb": 0, "no pattern": 1, "one big day": 2, "quiet slide": 3}
    rows.sort(key=lambda r: (order[r["verdict"]], -r["up_days"], -r["total"]))

    json.dump({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "days": DAYS, "requested": len(universe), "resolved": len(rows),
               "failed": failed, "rows": rows},
              open("trend.json", "w"), separators=(",", ":"))

    print(f"\n{len(rows)} scored, {len(failed)} unresolved")
    if failed:
        print("unresolved: " + ", ".join(failed))
    print(f"\n{'symbol':<15}{'up days':>9}{'30d %':>8}{'typical':>9}{'biggest':>9}{'streak':>8}  verdict")
    for r in rows:
        print(f"{r['symbol']:<15}{str(r['up_days'])+'/'+str(DAYS):>9}{r['total']:>8.1f}"
              f"{r['typical']:>9.2f}{r['biggest']:>9.1f}{r['streak']:>8}  {r['verdict']}"
              f"{'  THIN' if r['thin'] else ''}")


if __name__ == "__main__":
    main()
