#!/usr/bin/env python3
"""Three-year walk-forward model lab for Early Momentum Radar.

Primary objective: beat Nifty Midcap 150 and/or Nifty Smallcap 250, NOT Nifty 50.
Benchmarks use liquid ETF proxies because Yahoo Finance does not expose reliable
multi-year history for both underlying indices through yfinance:
  Midcap 150 -> MID150BEES.NS
  Smallcap 250 -> HDFCSML250.NS (fallback MOSMALL250.NS)

V4 was designed after examining the most recent ~1 year. Therefore the OLDER two
years are treated as the cleanest fresh holdout in this experiment.
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
import model_v4
import stock_registry as registry

BATCH=35
PERIOD="5y"
HORIZONS=[5,10,20,40]
TEST_DAYS=756
MIN_GAP=20
V2_STAGES=["Discovery","Confirming","Watch","Momentum","Late / Event"]
V4_STAGES=["Discovery","Confirming","Watch","Late / Event"]
MID_CANDIDATES=["MID150BEES.NS"]
SMALL_CANDIDATES=["HDFCSML250.NS","MOSMALL250.NS"]


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


def choose_proxy(candidates):
    data=fetch(candidates)
    valid=[(t,d) for t,d in data.items() if len(d)>=500]
    if not valid:
        valid=list(data.items())
    if not valid: return None,None
    return max(valid,key=lambda x:len(x[1]))


def ret_from(df,pos,n):
    if pos<n: return None
    c=df["Close"].to_numpy(float)
    return float((c[pos]/c[pos-n]-1)*100)


def forward_ret(df,pos,h):
    c=df["Close"].to_numpy(float)
    if pos+h>=len(c): return None
    return float((c[pos+h]/c[pos]-1)*100)


def align_pos(df,day):
    return pd.DatetimeIndex(df.index).searchsorted(day,side="right")-1


def sector_context(rows):
    groups=defaultdict(list)
    for r in rows:
        if r.get("sector"): groups[r["sector"]].append(r)
    out={}
    for sec,rr in groups.items():
        vals=[x.get("r20") for x in rr if x.get("r20") is not None]
        if not vals: continue
        out[sec]={
            "median20":float(np.median(vals)),
            "breadth20":sum(v>0 for v in vals)/len(vals),
            "n":len(vals),
        }
    return out


def evaluate_forward(df,pos,mid,midpos,small,smallpos):
    out={}
    for h in HORIZONS:
        r=forward_ret(df,pos,h); mr=forward_ret(mid,midpos,h); sr=forward_ret(small,smallpos,h)
        out[f"ret{h}"]=None if r is None else round(r,2)
        out[f"mid{h}"]=None if mr is None else round(mr,2)
        out[f"small{h}"]=None if sr is None else round(sr,2)
        if r is None or mr is None or sr is None:
            out[f"alpha_mid{h}"]=out[f"alpha_small{h}"]=out[f"alpha_hard{h}"]=None
        else:
            out[f"alpha_mid{h}"]=round(r-mr,2)
            out[f"alpha_small{h}"]=round(r-sr,2)
            out[f"alpha_hard{h}"]=round(r-max(mr,sr),2)
    return out


def stats(rr,h):
    rows=[x for x in rr if x.get(f"ret{h}") is not None and x.get(f"alpha_mid{h}") is not None and x.get(f"alpha_small{h}") is not None]
    if not rows:
        return {"n":0}
    vals=[x[f"ret{h}"] for x in rows]
    am=[x[f"alpha_mid{h}"] for x in rows]; ass=[x[f"alpha_small{h}"] for x in rows]
    ah=[x[f"alpha_hard{h}"] for x in rows]
    return {
        "n":len(rows),
        "median_return":round(float(np.median(vals)),2),
        "mean_return":round(float(np.mean(vals)),2),
        "win_rate":round(sum(v>0 for v in vals)/len(vals)*100,1),
        "median_alpha_mid150":round(float(np.median(am)),2),
        "median_alpha_small250":round(float(np.median(ass)),2),
        "median_alpha_harder_benchmark":round(float(np.median(ah)),2),
        "beat_mid150_rate":round(sum(v>0 for v in am)/len(am)*100,1),
        "beat_small250_rate":round(sum(v>0 for v in ass)/len(ass)*100,1),
        "beat_either_rate":round(sum((a>0 or b>0) for a,b in zip(am,ass))/len(rows)*100,1),
        "beat_both_rate":round(sum((a>0 and b>0) for a,b in zip(am,ass))/len(rows)*100,1),
    }


def summarize(signals,stages):
    return {st:{str(h):stats([x for x in signals if x["stage"]==st],h) for h in HORIZONS} for st in stages}


def actionable(signals):
    rr=[x for x in signals if x["stage"] in ("Discovery","Confirming")]
    return {str(h):stats(rr,h) for h in HORIZONS}


def daily_counts(signals,days):
    by=defaultdict(int)
    for r in signals:
        if r["stage"] in ("Discovery","Confirming"): by[r["signal_date"]]+=1
    vals=[by.get(str(pd.Timestamp(d).date()),0) for d in days]
    return {"mean":round(float(np.mean(vals)),1),"median":round(float(np.median(vals)),1),"p90":round(float(np.percentile(vals,90)),1),"max":int(max(vals) if vals else 0)}


def period_summary(signals,start,end,stages):
    rr=[r for r in signals if start<=r["signal_date"]<=end]
    return {"n":len(rr),"summary":summarize(rr,stages),"actionable":actionable(rr)}


def decision(v2_hold,v4_hold,v4_daily):
    a=v2_hold["actionable"]["40"]; b=v4_hold["actionable"]["40"]
    if not a.get("n") or not b.get("n"):
        return {"promote_v4":False,"reason":"insufficient fresh-holdout observations"}
    alpha_positive=(b.get("median_alpha_mid150",-999)>0 or b.get("median_alpha_small250",-999)>0)
    either_good=b.get("beat_either_rate",0)>=55
    better_either=b.get("beat_either_rate",0)>a.get("beat_either_rate",0)
    shortlist_ok=v4_daily.get("median",999)<=35
    promote=bool(alpha_positive and either_good and better_either and shortlist_ok)
    return {
        "promote_v4":promote,
        "v2_fresh_holdout_40":a,"v4_fresh_holdout_40":b,
        "criteria":{"positive_median_alpha_vs_at_least_one_target":alpha_positive,"beat_either_at_least_55pct":either_good,"beat_either_better_than_v2":better_either,"median_daily_shortlist_le_35":shortlist_ok},
        "reason":"V4 must improve on the untouched older holdout and remain practically reviewable"
    }


def main():
    universe=registry.read_universe(os.path.join(ROOT,"trend","universe.txt"))
    rows_reg=registry.load(); meta=registry.by_yahoo(rows_reg); fb=es.fallback_map(rows_reg)
    nifty=fetch([es.BENCHMARK]).get(es.BENCHMARK)
    mid_sym,mid=choose_proxy(MID_CANDIDATES); small_sym,small=choose_proxy(SMALL_CANDIDATES)
    if nifty is None or mid is None or small is None: raise RuntimeError("benchmark history unavailable")

    frames=fetch(universe)
    missing=[t for t in universe if t not in frames]
    req={t:fb[t] for t in missing if t in fb}; alts=fetch(list(req.values())) if req else {}
    source={t:t for t in universe}
    for t,a in req.items():
        if a in alts: frames[t]=alts[a]; source[t]=a

    # Use dates available in all three benchmarks and leave 40 forward sessions.
    common=pd.DatetimeIndex(nifty.index).intersection(pd.DatetimeIndex(mid.index)).intersection(pd.DatetimeIndex(small.index))
    common=common[common>=max(pd.Timestamp(nifty.index[66]),pd.Timestamp(mid.index[66]),pd.Timestamp(small.index[66]))]
    test_dates=list(common[-(TEST_DAYS+40):-40])
    if len(test_dates)<500: raise RuntimeError(f"insufficient common history: {len(test_dates)} sessions")
    if len(test_dates)>TEST_DAYS: test_dates=test_dates[-TEST_DAYS:]

    # Last ~252 sessions overlap the period already inspected during V2/V3 development.
    known_start=str(pd.Timestamp(test_dates[-252]).date()) if len(test_dates)>=252 else str(pd.Timestamp(test_dates[-1]).date())
    fresh_start=str(pd.Timestamp(test_dates[0]).date()); fresh_end=str(pd.Timestamp(test_dates[-253]).date()) if len(test_dates)>252 else fresh_start
    end=str(pd.Timestamp(test_dates[-1]).date())

    sig={"v2":[],"v4":[]}; last_seen={"v2":{},"v4":{}}
    for di,day in enumerate(test_dates,1):
        if di%40==1: print(f"walk-forward {di}/{len(test_dates)} {day.date()}",flush=True)
        npos=align_pos(nifty,day); mpos=align_pos(mid,day); spos=align_pos(small,day)
        if min(npos,mpos,spos)<65: continue
        nb={n:ret_from(nifty,npos,n) for n in (5,10,20,60)}
        mid20=ret_from(mid,mpos,20) or 0; small20=ret_from(small,spos,20) or 0

        rows=[]; posmap={}
        for t in universe:
            df=frames.get(t)
            if df is None: continue
            p=align_pos(df,day)
            if p<65: continue
            r=es.analyse(t,df.iloc[:p+1],meta.get(t,{}),nb,source_symbol=source.get(t,t))
            if r is not None: rows.append(r); posmap[t]=p
        es.add_sector_and_signals(rows)
        breadth20=(sum((r.get("r20") or -999)>0 for r in rows)/len(rows)) if rows else 0
        sec=sector_context(rows)

        for r in rows:
            t=r["requested_yahoo"]; df=frames.get(t); p=posmap.get(t)
            if df is None or p is None: continue
            ident=r.get("isin") or t
            fwd=evaluate_forward(df,p,mid,mpos,small,spos)
            common_rec={k:r.get(k) for k in ("isin","symbol","name","sector","r5","r10","r20","r60","accel","accel_mid","rvol","turnover_cr","obv_slope","obv_high","obv_div","eff20","from_high60","event","stale")}
            common_rec.update({"signal_date":str(pd.Timestamp(day).date()),"yahoo":source.get(t,t),"entry":round(float(df["Close"].iloc[p]),2),"mid20":round(mid20,2),"small20":round(small20,2),"breadth20":round(breadth20,4)})
            common_rec.update(fwd)

            st=r["stage"]
            if st in V2_STAGES:
                key=(ident,st)
                if key not in last_seen["v2"] or di-last_seen["v2"][key]>=MIN_GAP:
                    last_seen["v2"][key]=di
                    sig["v2"].append({**common_rec,"model":"v2","stage":st,"score":r.get("radar_score"),"evidence":r.get("flags") or []})

            sctx=sec.get(r.get("sector"),{"median20":0,"breadth20":0,"n":0})
            ctx={"breadth20":breadth20,"mid20":mid20,"small20":small20,"sector_median20":sctx["median20"],"sector_breadth20":sctx["breadth20"],"target_rs20":None if r.get("r20") is None else r["r20"]-max(mid20,small20)}
            st4,score4,ev4=model_v4.classify(r,ctx)
            if st4 in V4_STAGES:
                key=(ident,st4)
                if key not in last_seen["v4"] or di-last_seen["v4"][key]>=MIN_GAP:
                    last_seen["v4"][key]=di
                    sig["v4"].append({**common_rec,"model":"v4","stage":st4,"score":score4,"evidence":ev4,"sector_median20":round(sctx["median20"],2),"sector_breadth20":round(sctx["breadth20"],4),"target_rs20":round(ctx["target_rs20"],2) if ctx["target_rs20"] is not None else None})

    days_fresh=[d for d in test_dates if fresh_start<=str(pd.Timestamp(d).date())<=fresh_end]
    v2_hold=period_summary(sig["v2"],fresh_start,fresh_end,V2_STAGES)
    v4_hold=period_summary(sig["v4"],fresh_start,fresh_end,V4_STAGES)
    v4_daily=daily_counts(sig["v4"],days_fresh)
    payload={
      "generated":datetime.now(timezone.utc).isoformat(timespec="seconds"),
      "method":"3y daily walk-forward; V2 baseline vs regime-aware V4; older ~2y = fresh holdout; 20-session cooldown; no future data in signal construction",
      "objective":"Beat Nifty Midcap 150 and/or Nifty Smallcap 250; Nifty 50 is diagnostic only, not the target benchmark.",
      "benchmark_proxies":{"midcap150":mid_sym,"smallcap250":small_sym,"note":"ETF price proxies; alpha is reported separately vs both. alpha_hard uses the better-performing benchmark each horizon."},
      "limitation":"Current repository universe creates survivorship bias; ETF tracking error, Yahoo history/corporate actions, slippage and transaction costs are not fully modelled.",
      "start":fresh_start,"end":end,"fresh_holdout_end":fresh_end,"known_period_start":known_start,"trading_days":len(test_dates),"requested":len(universe),"history_resolved":len(frames),
      "models":{"v2":{"signals":len(sig["v2"]),"full":summarize(sig["v2"],V2_STAGES),"fresh_holdout":v2_hold},"v4":{"signals":len(sig["v4"]),"full":summarize(sig["v4"],V4_STAGES),"fresh_holdout":v4_hold}},
      "daily_actionable":{"v2":daily_counts(sig["v2"],days_fresh),"v4":v4_daily},
      "decision":decision(v2_hold,v4_hold,v4_daily),
      "examples_v4":sorted([x for x in sig["v4"] if x["stage"] in ("Discovery","Confirming")],key=lambda x:-(x.get("score") or 0))[:150]
    }
    with open(os.path.join(HERE,"three_year_backtest.json"),"w") as f: json.dump(payload,f,separators=(",",":"))
    print("three_year_backtest.json written")
    print("benchmarks",mid_sym,small_sym)
    print("fresh holdout",fresh_start,"to",fresh_end)
    print("V2 fresh 40D",v2_hold["actionable"]["40"])
    print("V4 fresh 40D",v4_hold["actionable"]["40"])
    print("V4 daily",v4_daily)
    print("DECISION",payload["decision"])

if __name__=="__main__": main()
