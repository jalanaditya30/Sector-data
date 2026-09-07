#!/usr/bin/env python3
"""Early Momentum Radar — find stocks transitioning from ignored to accumulated/momentum.

This is a DISCOVERY screen, not a buy signal. It deliberately combines independent
families of evidence instead of hiding everything inside one opaque indicator:
  price acceleration, turnover acceleration, OBV accumulation, market relative
  strength, sector relative strength, trend efficiency, and proximity to breakout.

The default universe is ../trend/universe.txt, currently the repo's full tradable
NSE company universe. Identity and sector/industry metadata come from ../stocks.csv.
"""
import json, math, os, sys
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import stock_registry as registry  # noqa: E402

FETCH = "6mo"
BENCHMARK = "^NSEI"
BATCH = 40
MIN_HISTORY = 66
RECENT = 5
BASE = 20
LIQ_LOOKBACK = 20
MIN_TURNOVER_CR = 0.25   # intentionally permissive: micro/small caps matter here
MAX_STALE_FRAC = 0.35
EVENT_DAY = 15.0
WINSOR_PCT = 6.0


def fetch(tickers):
    out = {}
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i+BATCH]
        print(f"fetching {i+1}-{i+len(chunk)} of {len(tickers)}", flush=True)
        raw = yf.download(chunk, period=FETCH, interval="1d", group_by="ticker",
                          auto_adjust=True, progress=False, threads=True)
        if raw is None or raw.empty: continue
        for t in chunk:
            try:
                sub = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                sub = sub.dropna(subset=["Close"])
                if not sub.empty: out[t] = sub
            except (KeyError, TypeError): pass
    return out


def ret(c, n):
    return (float(c[-1] / c[-(n+1)] - 1) * 100) if len(c) >= n+1 else None


def efficiency(c, n=20):
    if len(c) < n+1: return None
    x = np.log(c[-(n+1):])
    steps = np.diff(x)
    cap = math.log(1 + WINSOR_PCT/100)
    steps = np.clip(steps, -cap, cap)
    distance = float(np.abs(steps).sum())
    return abs(float(steps.sum())) / distance if distance else 0.0


def obv_metrics(close, volume):
    if len(close) < 61 or len(volume) < 61: return None, None, None
    direction = np.sign(np.diff(close))
    obv = np.concatenate([[0.0], np.cumsum(direction * volume[1:])])
    # Normalised slope: 20d OBV change divided by typical 20d share volume.
    denom = float(np.median(volume[-20:])) * 20
    slope20 = float((obv[-1] - obv[-21]) / denom) if denom > 0 else 0.0
    obv_high = bool(obv[-1] >= np.max(obv[-60:]))
    price_high = bool(close[-1] >= np.max(close[-60:]))
    divergence = bool(obv_high and not price_high)
    return slope20, obv_high, divergence


def turnover_metrics(df):
    tv = (df["Close"].astype(float) * df["Volume"].astype(float) / 1e7).dropna()
    if len(tv) < RECENT + BASE: return None, None
    recent = float(tv.iloc[-RECENT:].median())
    baseline = float(tv.iloc[-(RECENT+BASE):-RECENT].median())
    ratio = recent / baseline if baseline > 0 else None
    liquid = float(tv.iloc[-LIQ_LOOKBACK:].median())
    return ratio, liquid


def analyse(symbol, df, meta, bench):
    if df is None or len(df) < MIN_HISTORY or "Volume" not in df: return None
    close = df["Close"].to_numpy(float); volume = df["Volume"].fillna(0).to_numpy(float)
    if np.any(close[-61:] <= 0): return None
    r5, r10, r20, r60 = (ret(close,n) for n in (5,10,20,60))
    # Comparable dailyised momentum. Positive accel means the newest leg is faster.
    accel = (r5/5) - (r20/20)
    accel_mid = (r10/10) - (r60/60)
    rvol, turn = turnover_metrics(df)
    obv_slope, obv_high, obv_div = obv_metrics(close, volume)
    eff = efficiency(close, 20)
    high60 = float(np.max(close[-61:-1]))
    from_high = (float(close[-1])/high60 - 1)*100 if high60 > 0 else None
    near_breakout = bool(from_high is not None and -5 <= from_high <= 2)
    daily20 = (close[-20:] / close[-21:-1] - 1)*100
    stale = float((np.abs(daily20) < .001).mean())
    biggest = float(np.max(np.abs(daily20)))
    display = meta.get("nse") or meta.get("bse") or meta.get("isin") or symbol.split(".")[0]
    bench20 = bench.get(20); market_rs = r20-bench20 if bench20 is not None else None
    return {
      "isin":meta.get("isin"),"symbol":display,"yahoo":symbol,
      "exchange":"BSE" if symbol.endswith(".BO") else "NSE",
      "name":meta.get("name") or display,
      "sector":meta.get("industry_group") or "Unclassified",
      "last":round(float(close[-1]),2),
      "r5":round(r5,2),"r10":round(r10,2),"r20":round(r20,2),"r60":round(r60,2),
      "accel":round(accel,3),"accel_mid":round(accel_mid,3),
      "rvol":round(rvol,2) if rvol is not None else None,
      "turnover_cr":round(turn,2) if turn is not None else None,
      "obv_slope":round(obv_slope,3) if obv_slope is not None else None,
      "obv_high":obv_high,"obv_div":obv_div,
      "eff20":round(eff,3) if eff is not None else None,
      "market_rs":round(market_rs,2) if market_rs is not None else None,
      "from_high60":round(from_high,2) if from_high is not None else None,
      "near_breakout":near_breakout,"event":bool(biggest>EVENT_DAY),
      "thin":bool(turn is not None and turn<MIN_TURNOVER_CR),"stale":bool(stale>MAX_STALE_FRAC)
    }


def add_sector_and_signals(rows):
    # Sector benchmark is the MEDIAN stock, so a giant constituent cannot dominate it.
    groups={}
    for r in rows: groups.setdefault(r["sector"],[]).append(r)
    sector_stats={}
    for s, rr in groups.items():
        vals=[x["r20"] for x in rr if x["r20"] is not None]
        rv=[x["rvol"] for x in rr if x["rvol"] is not None]
        sector_stats[s]={"count":len(rr),"ret20":round(float(np.median(vals)),2) if vals else None,
                         "rvol":round(float(np.median(rv)),2) if rv else None,
                         "positive20":round(sum(x["r20"]>0 for x in rr)/len(rr)*100,1) if rr else None}
    for r in rows:
        sr=sector_stats[r["sector"]]["ret20"]
        r["sector_rs"]=round(r["r20"]-sr,2) if sr is not None else None
        flags=[]
        if r["accel"]>0.35 and r["accel_mid"]>0: flags.append("price_accel")
        if r["rvol"] is not None and r["rvol"]>=1.5: flags.append("turnover_accel")
        if r["obv_slope"] is not None and r["obv_slope"]>0.20: flags.append("obv_rising")
        if r["obv_div"]: flags.append("obv_divergence")
        if r["market_rs"] is not None and r["market_rs"]>2: flags.append("beats_market")
        if r["sector_rs"] is not None and r["sector_rs"]>2: flags.append("beats_sector")
        if r["eff20"] is not None and r["eff20"]>=0.45 and r["r20"]>0: flags.append("clean_trend")
        if r["near_breakout"]: flags.append("near_breakout")
        r["flags"]=flags
        # Transparent ranking: confirmations first; small tie-break rewards strength.
        bonus=min(max(r["rvol"] or 0,0),4)*0.15 + min(max(r["market_rs"] or 0,0),10)*0.03
        r["radar_score"]=round(len(flags)+bonus,2)
        if r["event"] or r["stale"]: stage="Review"
        elif len(flags)>=6: stage="Strong emerging"
        elif len(flags)>=4: stage="Emerging"
        elif len(flags)>=2: stage="Watch"
        else: stage="No setup"
        r["stage"]=stage
    return sector_stats


def main():
    universe_path = sys.argv[1] if len(sys.argv)>1 else os.path.join(ROOT,"trend","universe.txt")
    universe=registry.read_universe(universe_path)
    rows_reg=registry.load(); meta=registry.by_yahoo(rows_reg)
    print(f"Early Momentum Radar: {len(universe)} symbols")
    bench_df=fetch([BENCHMARK]).get(BENCHMARK); bench={}
    if bench_df is not None:
        bc=bench_df["Close"].dropna().to_numpy(float)
        for n in (5,10,20,60): bench[n]=ret(bc,n)
    frames=fetch(universe)
    rows=[]; failed=[]
    for t in universe:
        r=analyse(t,frames.get(t),meta.get(t,{}),bench)
        (rows.append(r) if r else failed.append(t))
    sectors=add_sector_and_signals(rows)
    rows.sort(key=lambda x:x["radar_score"],reverse=True)
    payload={"generated":datetime.now(timezone.utc).isoformat(timespec="seconds"),
      "method":"independent confirmations; discovery, not buy signal",
      "requested":len(universe),"resolved":len(rows),"failed":failed,
      "benchmark":{str(k):round(v,2) if v is not None else None for k,v in bench.items()},
      "sectors":sectors,"rows":rows}
    with open("early.json","w") as f: json.dump(payload,f,separators=(",",":"))
    print(f"wrote early.json — {len(rows)} resolved, {len(failed)} failed")
    for r in rows[:25]: print(f"{r['symbol']:<16} {r['stage']:<16} {r['radar_score']:>5.2f}  {','.join(r['flags'])}")

if __name__=="__main__": main()
