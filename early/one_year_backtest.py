#!/usr/bin/env python3
"""One-year walk-forward model laboratory for Early Momentum Radar.

Runs the current live V2 and the stricter V3 candidate on exactly the same historical
sessions, with no future bars used in signal construction. The year is split in half:
first half = development, second half = validation. We also report feature attribution,
score buckets and daily signal counts so model changes can be evidence-driven.

Important limitation: today's repository universe is used, so survivorship bias remains.
"""
import json, os, sys, math
from collections import defaultdict
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import yfinance as yf

HERE=os.path.dirname(os.path.abspath(__file__))
ROOT=os.path.dirname(HERE)
sys.path.insert(0,HERE); sys.path.insert(0,ROOT)
import early_scan as es
import model_v3
import stock_registry as registry

BATCH=35
PERIOD="18mo"
HORIZONS=[5,10,20,40]
V2_STAGES=["Discovery","Confirming","Watch","Momentum","Late / Event"]
V3_STAGES=["Discovery","Confirming","Watch","Late / Event"]
TEST_DAYS=252
MIN_GAP=20


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


def stats(rr,h):
    vals=[x[f"ret{h}"] for x in rr if x.get(f"ret{h}") is not None]
    al=[x[f"alpha{h}"] for x in rr if x.get(f"alpha{h}") is not None]
    if not vals:
        return {"n":0,"median_return":None,"mean_return":None,"win_rate":None,
                "median_alpha":None,"mean_alpha":None,"beat_nifty_rate":None}
    return {
        "n":len(vals),
        "median_return":round(float(np.median(vals)),2),
        "mean_return":round(float(np.mean(vals)),2),
        "win_rate":round(sum(v>0 for v in vals)/len(vals)*100,1),
        "median_alpha":round(float(np.median(al)),2),
        "mean_alpha":round(float(np.mean(al)),2),
        "beat_nifty_rate":round(sum(v>0 for v in al)/len(al)*100,1),
    }


def summarize(signals, stages):
    out={}
    for st in stages:
        rr=[x for x in signals if x["stage"]==st]
        out[st]={str(h):stats(rr,h) for h in HORIZONS}
    return out


def actionable_summary(signals):
    rr=[x for x in signals if x["stage"] in ("Discovery","Confirming")]
    return {str(h):stats(rr,h) for h in HORIZONS}


def feature_attribution(signals, field):
    names=sorted({v for r in signals for v in (r.get(field) or [])})
    out={}
    for name in names:
        rr=[r for r in signals if name in (r.get(field) or [])]
        s=stats(rr,20)
        if s["n"]>=20: out[name]=s
    return dict(sorted(out.items(), key=lambda kv: (-(kv[1]["median_alpha"] or -999),-kv[1]["n"])))


def score_buckets(signals):
    buckets={"<4":[],"4-6":[],"6-8":[],"8+":[]}
    for r in signals:
        s=r.get("score")
        if s is None: continue
        if s<4: k="<4"
        elif s<6: k="4-6"
        elif s<8: k="6-8"
        else: k="8+"
        buckets[k].append(r)
    return {k:stats(v,20) for k,v in buckets.items()}


def daily_counts(signals, days):
    by=defaultdict(int)
    for r in signals:
        if r["stage"] in ("Discovery","Confirming"):
            by[r["signal_date"]]+=1
    vals=[by.get(str(pd.Timestamp(d).date()),0) for d in days]
    return {"mean":round(float(np.mean(vals)),1),"median":round(float(np.median(vals)),1),
            "p90":round(float(np.percentile(vals,90)),1),"max":int(max(vals) if vals else 0)}


def split_summary(signals, cutoff, stages):
    dev=[r for r in signals if r["signal_date"]<=cutoff]
    val=[r for r in signals if r["signal_date"]>cutoff]
    return {
        "development":{"n":len(dev),"summary":summarize(dev,stages),"actionable":actionable_summary(dev)},
        "validation":{"n":len(val),"summary":summarize(val,stages),"actionable":actionable_summary(val)},
    }


def model_decision(v2,v3):
    a=v2["validation"]["actionable"]["20"]
    b=v3["validation"]["actionable"]["20"]
    if not a["n"] or not b["n"]:
        return {"promote_v3":False,"reason":"insufficient validation observations"}
    improve_alpha=(b["median_alpha"] or -999)>(a["median_alpha"] or -999)
    improve_beat=(b["beat_nifty_rate"] or 0)>(a["beat_nifty_rate"] or 0)
    improve_win=(b["win_rate"] or 0)>(a["win_rate"] or 0)
    # We require improvement on alpha and at least one hit-rate metric. This is a
    # validation gate, not an optimizer.
    promote=bool(improve_alpha and (improve_beat or improve_win))
    return {"promote_v3":promote,"v2_validation20":a,"v3_validation20":b,
            "criteria":{"median_alpha_better":improve_alpha,"beat_nifty_rate_better":improve_beat,
                        "win_rate_better":improve_win},
            "reason":"promote only if unseen validation improves alpha and at least one hit-rate metric"}


def main():
    universe=registry.read_universe(os.path.join(ROOT,"trend","universe.txt"))
    rows_reg=registry.load(); meta=registry.by_yahoo(rows_reg); fb=es.fallback_map(rows_reg)
    bench=fetch([es.BENCHMARK]).get(es.BENCHMARK)
    if bench is None: raise RuntimeError("Nifty history unavailable")
    frames=fetch(universe)

    missing=[t for t in universe if t not in frames]
    req={t:fb[t] for t in missing if t in fb}
    alts=fetch(list(req.values())) if req else {}
    source={t:t for t in universe}
    for t,a in req.items():
        if a in alts:
            frames[t]=alts[a]; source[t]=a

    bidx=pd.DatetimeIndex(bench.index)
    eligible=bidx[66:]
    test_dates=list(eligible[-(TEST_DAYS+40):-40]) if len(eligible)>40 else []
    if not test_dates: raise RuntimeError("not enough benchmark history")
    cutoff=str(pd.Timestamp(test_dates[len(test_dates)//2-1]).date())

    last_seen={"v2":{},"v3":{}}
    sig={"v2":[],"v3":[]}
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
            r=es.analyse(t,df.iloc[:p+1],meta.get(t,{}),bret,source_symbol=source.get(t,t))
            if r is not None:
                rows.append(r); posmap[t]=p
        es.add_sector_and_signals(rows)

        for r in rows:
            t=r["requested_yahoo"]; df=frames.get(t); p=posmap.get(t)
            if df is None or p is None: continue
            ident=r.get("isin") or r.get("requested_yahoo") or r.get("yahoo")
            fwd=evaluate_forward(df,bench,p,bpos)
            common={k:r.get(k) for k in ("isin","symbol","name","sector","r5","r10","r20","r60","accel","accel_mid","rvol","turnover_cr","obv_slope","obv_high","obv_div","market_rs","sector_rs","eff20","from_high60","event","stale")}
            common.update({"signal_date":str(pd.Timestamp(day).date()),"yahoo":source.get(t,t),"entry":round(float(df["Close"].iloc[p]),2)})
            common.update(fwd)

            # Baseline V2
            st=r["stage"]
            if st in V2_STAGES:
                key=(ident,st)
                if key not in last_seen["v2"] or di-last_seen["v2"][key]>=MIN_GAP:
                    last_seen["v2"][key]=di
                    rec={**common,"model":"v2","stage":st,"score":r.get("radar_score"),"evidence":r.get("flags") or []}
                    sig["v2"].append(rec)

            # Candidate V3
            st3,score3,reasons3=model_v3.classify(r)
            if st3 in V3_STAGES:
                key=(ident,st3)
                if key not in last_seen["v3"] or di-last_seen["v3"][key]>=MIN_GAP:
                    last_seen["v3"][key]=di
                    rec={**common,"model":"v3","stage":st3,"score":score3,"evidence":reasons3}
                    sig["v3"].append(rec)

    v2split=split_summary(sig["v2"],cutoff,V2_STAGES)
    v3split=split_summary(sig["v3"],cutoff,V3_STAGES)
    diagnostics={
        "v2":{"feature20":feature_attribution(sig["v2"],"evidence"),"score20":score_buckets(sig["v2"]),"daily_actionable":daily_counts(sig["v2"],test_dates)},
        "v3":{"feature20":feature_attribution(sig["v3"],"evidence"),"score20":score_buckets(sig["v3"]),"daily_actionable":daily_counts(sig["v3"],test_dates)},
    }
    decision=model_decision(v2split,v3split)

    payload={
        "generated":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method":"1y daily walk-forward; v2 baseline vs strict v3 candidate; first half development / second half validation; 20-session cooldown; no future data in signals",
        "limitation":"Current repository universe creates survivorship bias; Yahoo history/corporate actions and real execution costs are not fully modelled.",
        "start":str(pd.Timestamp(test_dates[0]).date()),"end":str(pd.Timestamp(test_dates[-1]).date()),
        "cutoff_development_end":cutoff,"trading_days":len(test_dates),"requested":len(universe),"history_resolved":len(frames),
        "models":{
            "v2":{"signals":len(sig["v2"]),"full":summarize(sig["v2"],V2_STAGES),"split":v2split},
            "v3":{"signals":len(sig["v3"]),"full":summarize(sig["v3"],V3_STAGES),"split":v3split},
        },
        "diagnostics":diagnostics,"decision":decision,
        "examples_v3":sorted([x for x in sig["v3"] if x["stage"] in ("Discovery","Confirming")],key=lambda x:-(x.get("score") or 0))[:100]
    }
    with open(os.path.join(HERE,"one_year_backtest.json"),"w") as f: json.dump(payload,f,separators=(",",":"))

    print(f"one_year_backtest.json: v2={len(sig['v2'])} signals, v3={len(sig['v3'])} signals, {len(test_dates)} days")
    print("cutoff",cutoff)
    print("V2 validation actionable20",v2split["validation"]["actionable"]["20"])
    print("V3 validation actionable20",v3split["validation"]["actionable"]["20"])
    print("V2 daily actionable",diagnostics["v2"]["daily_actionable"])
    print("V3 daily actionable",diagnostics["v3"]["daily_actionable"])
    print("DECISION",decision)

if __name__=="__main__": main()
