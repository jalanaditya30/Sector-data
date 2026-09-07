#!/usr/bin/env python3
"""Evaluate what happens after Early Momentum signals.

Reads compact daily snapshots from early/history/*.json. For each stock/stage, only
the FIRST recorded signal is used, which avoids counting the same multi-day setup
again and again. Forward returns are measured after 5/10/20/40 trading sessions
and compared with Nifty over the same dates.

This is prospective measurement infrastructure. With a new archive, long-horizon
sample counts will initially be zero and fill naturally over time.
"""
import glob, json, os
from collections import defaultdict
import numpy as np
import pandas as pd
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
HORIZONS = [5, 10, 20, 40]
STAGES = ["Discovery", "Confirming"]
BATCH = 40


def load_first_signals():
    first = {}
    for path in sorted(glob.glob(os.path.join(HERE, "history", "*.json"))):
        with open(path) as f:
            snap = json.load(f)
        day = snap["date"]
        for s in snap.get("signals", []):
            stage = s.get("stage")
            if stage not in STAGES:
                continue
            identity = s.get("isin") or s.get("requested_yahoo") or s.get("yahoo")
            key = (identity, stage)
            if key not in first:
                first[key] = {**s, "signal_date": day}
    return list(first.values())


def download(tickers):
    out = {}
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i+BATCH]
        raw = yf.download(chunk, period="2y", interval="1d", group_by="ticker",
                          auto_adjust=True, progress=False, threads=True)
        if raw is None or raw.empty:
            continue
        for t in chunk:
            try:
                sub = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                sub = sub.dropna(subset=["Close"])
                if not sub.empty:
                    out[t] = sub
            except (KeyError, TypeError):
                pass
    return out


def close_on_or_after(df, day):
    idx = pd.DatetimeIndex(df.index)
    target = pd.Timestamp(day)
    pos = idx.searchsorted(target, side="left")
    return int(pos) if pos < len(df) else None


def evaluate(signals, frames, bench):
    results = []
    for s in signals:
        t = s.get("yahoo") or s.get("requested_yahoo")
        df = frames.get(t)
        if df is None or bench is None:
            continue
        p = close_on_or_after(df, s["signal_date"])
        bp = close_on_or_after(bench, s["signal_date"])
        if p is None or bp is None:
            continue
        closes = df["Close"].to_numpy(float)
        bc = bench["Close"].to_numpy(float)
        row = {k:s.get(k) for k in ("isin","symbol","name","sector","stage","radar_score","signal_date","yahoo")}
        row["entry"] = round(float(closes[p]), 2)
        for h in HORIZONS:
            if p+h < len(closes) and bp+h < len(bc):
                stock = (closes[p+h]/closes[p]-1)*100
                nifty = (bc[bp+h]/bc[bp]-1)*100
                row[f"ret{h}"] = round(float(stock), 2)
                row[f"nifty{h}"] = round(float(nifty), 2)
                row[f"alpha{h}"] = round(float(stock-nifty), 2)
            else:
                row[f"ret{h}"] = row[f"nifty{h}"] = row[f"alpha{h}"] = None
        results.append(row)
    return results


def summary(results):
    out = {}
    for stage in STAGES:
        rr = [r for r in results if r["stage"] == stage]
        out[stage] = {}
        for h in HORIZONS:
            vals = [r[f"ret{h}"] for r in rr if r[f"ret{h}"] is not None]
            alphas = [r[f"alpha{h}"] for r in rr if r[f"alpha{h}"] is not None]
            out[stage][str(h)] = {
                "n": len(vals),
                "median_return": round(float(np.median(vals)), 2) if vals else None,
                "mean_return": round(float(np.mean(vals)), 2) if vals else None,
                "win_rate": round(sum(v > 0 for v in vals)/len(vals)*100, 1) if vals else None,
                "median_alpha": round(float(np.median(alphas)), 2) if alphas else None,
                "beat_nifty_rate": round(sum(v > 0 for v in alphas)/len(alphas)*100, 1) if alphas else None,
            }
    return out


def main():
    signals = load_first_signals()
    tickers = sorted({s.get("yahoo") or s.get("requested_yahoo") for s in signals if s.get("yahoo") or s.get("requested_yahoo")})
    frames = download(tickers) if tickers else {}
    bench = download(["^NSEI"]).get("^NSEI")
    results = evaluate(signals, frames, bench)
    payload = {
        "method": "first signal per stock per stage; close-on/after signal date; forward trading-session returns",
        "horizons": HORIZONS,
        "first_signals": len(signals),
        "evaluated": len(results),
        "summary": summary(results),
        "signals": results,
    }
    with open(os.path.join(HERE, "backtest.json"), "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"backtest.json — {len(signals)} first signals, {len(results)} evaluated")
    for st in STAGES:
        print(st, payload["summary"][st])


if __name__ == "__main__":
    main()
