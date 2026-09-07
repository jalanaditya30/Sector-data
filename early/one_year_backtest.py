#!/usr/bin/env python3
"""One-year walk-forward backtest for Early Momentum Radar v2.

For every completed Nifty trading session in the last ~1 year, rebuild the radar
using ONLY bars available on that date, including sector-relative strength. Record
the FIRST Discovery/Confirming signal per stock/stage in the test window, then
measure forward 5/10/20/40-session returns and alpha vs Nifty.

Important limitation: the universe comes from today's repository universe, so this
is not survivorship-bias free. It is still useful for validating signal mechanics,
but results must not be treated as a production-quality academic backtest.
"""
import json, os, sys
from collections import defaultdict
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import yfinance as yf

HERE=os.path.dirname(os.path.abspath(__file__))
ROOT=os.path.dirname(HERE)
sys.path.insert(0,HERE); sys.path.insert(0,ROOT)
import early_scan as es
import stock_registry as registry

BATCH=35
PERIOD="18mo"
HORIZONS=[5,10,20,40]
STAGES=["Discovery","Confirming","Watch","Momentum","Late / Event"]
TEST_DAYS=252
MIN_GAP=20  # do not count the same stock/stage repeatedly within one short episode


def fetch(tickers):
    out={}
    for i in range(0,len(tickers),BATCH):
        chunk=tickers[i:i+BATCH]
        print(f"history fetch {i+1}-{i+len(chunk)} / {len(tickers)}",flush=True)
        raw=yf.download(chunk,period=PERIOD,interval="1d",group_by="ticker",auto_adjust=True,progress=False,threads=True)
        if raw is None or raw.empty: continue
        for t in chunk:
            try:
                sub=raw[t] if isinstance(raw.columns,pd.MultiIndex) else raw
                sub=sub.dropna(subset=["Close"])
                if not sub.empty: out[t]=sub
            except (KeyError,TypeError): pass
    return out


def bench_returns(close):
    return {n:es.ret(close,n) for n in (5,10,20,60)}


def evaluate_forward(df, bench, pos, bpos):
    c=df["Close"].to_numpy(float); bc=bench["Close"].to_numpy(float)
    out={}
    for h in HORIZONS:
        if pos+h < len(c) and bpos+h < len(bc):
            r=(c[pos+h]/c[pos]-1)*100; br=(bc[bpos+h]/bc[bpos]-1)*100
            out[f"ret{h}"]=round(float(r),2); out[f"nifty{h}"]=round(float(br),2); out[f"alpha{h}"]=round(float(r-br),2)
        else:
            out[f"ret{h}"]=out[f"nifty{h}"]=out[f"alpha{h}"]=None
    return out


def summarize(signals):
    summary={}
    for st in STAGES:
        rr=[x for x in signals if x["stage"]==st]
        summary[st]={}
        for h in HORIZONS:
            vals=[x[f"ret{h}"] for x in rr if x.get(f"ret{h}") is not None]
            al=[x[f"alpha{h}"] for x in rr if x.get(f"alpha{h}") is not None]
            summary[st][str(h)]={
                "n":len(vals),
                "median_return":round(float(np.median(vals)),2) if vals else None,
                "mean_return":round(float(np.mean(vals)),2) if vals else None,
                "win_rate":round(sum(v>0 for v in vals)/len(vals)*100,1) if vals else None,
                "median_alpha":round(float(np.median(al)),2) if al else None,
                "beat_nifty_rate":round(sum(v>0 for v in al)/len(al)*100,1) if al else None,
            }
    return summary


def main():
    universe=registry.read_universe(os.path.join(ROOT,"trend","universe.txt"))
    rows_reg=registry.load(); meta=registry.by_yahoo(rows_reg); fb=es.fallback_map(rows_reg)
    bench=fetch([es.BENCHMARK]).get(es.BENCHMARK)
    if bench is None: raise RuntimeError("Nifty history unavailable")
    frames=fetch(universe)

    # BSE fallback only for names with no usable NSE frame at all.
    missing=[t for t in universe if t not in frames]
    req={t:fb[t] for t in missing if t in fb}
    alts=fetch(list(req.values())) if req else {}
    source={t:t for t in universe}
    for t,a in req.items():
        if a in alts:
            frames[t]=alts[a]; source[t]=a

    bidx=pd.DatetimeIndex(bench.index)
    # Need 66 sessions of warm-up and 40 future sessions for mature results.
    eligible=bidx[66:]
    test_dates=list(eligible[-(TEST_DAYS+40):-40]) if len(eligible)>40 else []
    if not test_dates: raise RuntimeError("not enough benchmark history")

    last_seen={}
    signals=[]
    for di,day in enumerate(test_dates,1):
        if di%20==1: print(f"walk-forward {di}/{len(test_dates)} {day.date()}",flush=True)
        bpos=bidx.searchsorted(day,side="right")-1
        bclose=bench["Close"].iloc[:bpos+1].to_numpy(float)
        bret=bench_returns(bclose)
        rows=[]; posmap={}
        for t in universe:
            df=frames.get(t)
            if df is None: continue
            idx=pd.DatetimeIndex(df.index)
            p=idx.searchsorted(day,side="right")-1
            if p<65: continue
            cut=df.iloc[:p+1]
            r=es.analyse(t,cut,meta.get(t,{}),bret,source_symbol=source.get(t,t))
            if r is not None:
                rows.append(r); posmap[t]=p
        es.add_sector_and_signals(rows)
        for r in rows:
            st=r["stage"]
            if st not in STAGES: continue
            ident=r.get("isin") or r.get("requested_yahoo") or r.get("yahoo")
            k=(ident,st)
            if k in last_seen and di-last_seen[k] < MIN_GAP: continue
            last_seen[k]=di
            t=r["requested_yahoo"]
            df=frames.get(t); p=posmap.get(t)
            if df is None or p is None: continue
            rec={k:r.get(k) for k in ("isin","symbol","name","sector","stage","radar_score","r5","r20","r60","rvol","turnover_cr","obv_slope","obv_div","market_rs","sector_rs","eff20","from_high60")}
            rec.update({"signal_date":str(pd.Timestamp(day).date()),"yahoo":source.get(t,t),"entry":round(float(df["Close"].iloc[p]),2)})
            rec.update(evaluate_forward(df,bench,p,bpos))
            signals.append(rec)

    summary=summarize(signals)
    # Primary decision table focuses on 20D because objective is early multi-week momentum.
    primary={st:summary[st]["20"] for st in STAGES}
    payload={
        "generated":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method":"1y daily walk-forward; current-universe; 20-session cooldown per stock/stage; no future data in signal construction",
        "limitation":"Current repository universe creates survivorship bias; Yahoo history/corporate actions may contain imperfections.",
        "start":str(pd.Timestamp(test_dates[0]).date()),"end":str(pd.Timestamp(test_dates[-1]).date()),
        "trading_days":len(test_dates),"requested":len(universe),"history_resolved":len(frames),
        "signals":len(signals),"horizons":HORIZONS,"summary":summary,"primary20":primary,
        "examples":sorted(signals,key=lambda x:(x["stage"] not in ("Discovery","Confirming"),-(x.get("radar_score") or 0)))[:100]
    }
    with open(os.path.join(HERE,"one_year_backtest.json"),"w") as f: json.dump(payload,f,separators=(",",":"))
    print(f"one_year_backtest.json: {len(signals)} signals across {len(test_dates)} trading days")
    for st in STAGES: print(st,primary[st])

if __name__=="__main__": main()
