#!/usr/bin/env python3
"""V6 research lab: anomaly / accumulation detection.

Methodological change from V2-V5:
- no hand-written composite score
- compare each stock with its own historical behaviour
- test raw features and pre-declared feature combinations
- measure benchmark-relative alpha AND big-winner discovery
- use non-overlapping-ish weekly sampling to reduce repeated observations

Primary benchmarks: Nifty Midcap 150 and Nifty Smallcap 250 ETF proxies.
"""
import json, os, sys, math
from collections import defaultdict
from datetime import datetime, timezone
import numpy as np, pandas as pd, yfinance as yf
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
sys.path.insert(0,HERE); sys.path.insert(0,ROOT)
import stock_registry as registry
import v6_features

BATCH=35; PERIOD='5y'; TEST_DAYS=756; SAMPLE_EVERY=5
HORIZONS=[10,20,40,60]
MID=['MID150BEES.NS']; SMALL=['HDFCSML250.NS','MOSMALL250.NS']

FEATURES=['turnover_change','turnover_persistence','up_down_turnover','participation_price_ratio','atr_compression','range_compression','accumulation_days','rs_accel10','rs_accel20','from_high120']

# Pre-declared, interpretable patterns. These are not optimized weights.
def pattern(name,f):
    try:
        if name=='quiet_participation': return f['quiet_participation'] and f['turnover_cr']>=0.5
        if name=='absorption': return f['turnover_change']>=1.35 and f['participation_price_ratio']>=0.25 and -3<=f['r20']<=12 and f['event20']<12 and f['turnover_cr']>=0.5
        if name=='persistent_accumulation': return f['turnover_persistence']>=0.7 and f['accumulation_days']>=0.5 and f['up_down_turnover']>=1.2 and f['event20']<12 and f['turnover_cr']>=0.5
        if name=='compression_plus_money': return f['compression'] and f['turnover_change']>=1.25 and f['turnover_persistence']>=0.5 and f['r20']<15 and f['turnover_cr']>=0.5
        if name=='rs_transition': return f['rs_accel10'] is not None and f['rs_accel20'] is not None and f['rs_accel10']>1 and f['rs_accel20']>1 and f['r20']<15 and f['event20']<12 and f['turnover_cr']>=0.5
        if name=='full_accumulation_setup': return f['quiet_participation'] and f['compression'] and f['near_structure'] and f['accumulation_days']>=0.5 and f['rs_accel20'] is not None and f['rs_accel20']>0 and f['turnover_cr']>=0.5
    except (KeyError,TypeError): return False
    return False
PATTERNS=['quiet_participation','absorption','persistent_accumulation','compression_plus_money','rs_transition','full_accumulation_setup']

def fetch(ts):
    out={}
    for i in range(0,len(ts),BATCH):
        ch=ts[i:i+BATCH]; print(f'fetch {i+1}-{i+len(ch)}/{len(ts)}',flush=True)
        z=yf.download(ch,period=PERIOD,interval='1d',group_by='ticker',auto_adjust=True,progress=False,threads=True)
        if z is None or z.empty: continue
        for t in ch:
            try:
                d=z[t] if isinstance(z.columns,pd.MultiIndex) else z
                d=d.dropna(subset=['Close'])
                if not d.empty: out[t]=d
            except: pass
    return out

def choose(cands):
    d=fetch(cands); v=[(t,x) for t,x in d.items() if len(x)>=600] or list(d.items())
    return max(v,key=lambda q:len(q[1])) if v else (None,None)
def pos(d,day): return pd.DatetimeIndex(d.index).searchsorted(day,side='right')-1
def pastret(d,p,n):
    if p<n:return None
    c=d['Close'].to_numpy(float); return float((c[p]/c[p-n]-1)*100)
def fwd(d,p,n):
    c=d['Close'].to_numpy(float); return None if p+n>=len(c) else float((c[p+n]/c[p]-1)*100)
def excursion(d,p,n):
    c=d['Close'].to_numpy(float)
    if p+n>=len(c): return None,None
    path=c[p+1:p+n+1]/c[p]-1
    return float(np.max(path)*100),float(np.min(path)*100)

def outcome(d,p,mid,mp,sm,sp):
    o={}
    for h in HORIZONS:
        r=fwd(d,p,h); mr=fwd(mid,mp,h); sr=fwd(sm,sp,h); mx,dd=excursion(d,p,h)
        o[f'ret{h}']=r; o[f'alpha_mid{h}']=None if r is None or mr is None else r-mr; o[f'alpha_small{h}']=None if r is None or sr is None else r-sr
        o[f'max{h}']=mx; o[f'dd{h}']=dd
    return o

def stats(rows,h):
    q=[r for r in rows if r.get(f'ret{h}') is not None and r.get(f'alpha_mid{h}') is not None and r.get(f'alpha_small{h}') is not None]
    if not q:return {'n':0}
    rr=np.array([r[f'ret{h}'] for r in q]); am=np.array([r[f'alpha_mid{h}'] for r in q]); ass=np.array([r[f'alpha_small{h}'] for r in q]); mx=np.array([r[f'max{h}'] for r in q]); dd=np.array([r[f'dd{h}'] for r in q])
    z=lambda x:round(float(x),2)
    return {'n':len(q),'median_return':z(np.median(rr)),'mean_return':z(np.mean(rr)),'win_rate':round(float((rr>0).mean()*100),1),'median_alpha_mid150':z(np.median(am)),'median_alpha_small250':z(np.median(ass)),'beat_either_rate':round(float(((am>0)|(ass>0)).mean()*100),1),'beat_both_rate':round(float(((am>0)&(ass>0)).mean()*100),1),'median_max_rise':z(np.median(mx)),'median_max_drawdown':z(np.median(dd)),'hit_plus10_rate':round(float((mx>=10).mean()*100),1),'hit_plus20_rate':round(float((mx>=20).mean()*100),1),'hit_plus30_rate':round(float((mx>=30).mean()*100),1)}

def quantile_report(obs,feature,h=40):
    q=[r for r in obs if r['f'].get(feature) is not None and np.isfinite(r['f'][feature]) and r['o'].get(f'ret{h}') is not None]
    if len(q)<100:return []
    vals=np.array([r['f'][feature] for r in q],float); cuts=np.quantile(vals,np.linspace(0,1,6))
    out=[]
    for i in range(5):
        lo,hi=cuts[i],cuts[i+1]
        bucket=[r['o'] for r in q if (r['f'][feature]>=lo and (r['f'][feature]<hi if i<4 else r['f'][feature]<=hi))]
        s=stats(bucket,h); s.update({'bucket':i+1,'lo':round(float(lo),4),'hi':round(float(hi),4)}) ; out.append(s)
    return out

def spearman(obs,feature,target):
    q=[r for r in obs if r['f'].get(feature) is not None and r['o'].get(target) is not None and np.isfinite(r['f'][feature])]
    if len(q)<100:return None
    a=pd.Series([r['f'][feature] for r in q]).rank().to_numpy(); b=pd.Series([r['o'][target] for r in q]).rank().to_numpy()
    return round(float(np.corrcoef(a,b)[0,1]),4)

def main():
    uni=registry.read_universe(os.path.join(ROOT,'trend','universe.txt'))
    mid_sym,mid=choose(MID); sm_sym,sm=choose(SMALL)
    if mid is None or sm is None: raise RuntimeError('benchmark unavailable')
    frames=fetch(uni)
    common=pd.DatetimeIndex(mid.index).intersection(pd.DatetimeIndex(sm.index))
    dates=list(common[-(TEST_DAYS+60):-60])[-TEST_DAYS:]
    sample_dates=dates[::SAMPLE_EVERY]
    obs=[]
    for di,day in enumerate(sample_dates,1):
        if di%20==1:print(f'research {di}/{len(sample_dates)} {day.date()}',flush=True)
        mp=pos(mid,day); sp=pos(sm,day)
        if min(mp,sp)<145: continue
        bm={n:pastret(mid,mp,n) for n in (10,20,60)}
        for t,d in frames.items():
            p=pos(d,day)
            if p<145: continue
            f=v6_features.features(d.iloc[:p+1],bm)
            if f is None:continue
            # basic investability only; feature research remains broad.
            if f['turnover_cr']<0.25 or f['event20']>20: continue
            obs.append({'date':str(day.date()),'ticker':t,'f':f,'o':outcome(d,p,mid,mp,sm,sp)})
    print('observations',len(obs),flush=True)
    feature_report={}
    for ft in FEATURES:
        feature_report[ft]={'spearman_alpha40_mid':spearman(obs,ft,'alpha_mid40'),'spearman_alpha40_small':spearman(obs,ft,'alpha_small40'),'spearman_max40':spearman(obs,ft,'max40'),'quintiles40':quantile_report(obs,ft,40)}
    pattern_report={}
    for pn in PATTERNS:
        rows=[r['o'] for r in obs if pattern(pn,r['f'])]
        pattern_report[pn]={str(h):stats(rows,h) for h in HORIZONS}
    # Robustness: thirds of time for each predeclared pattern at 40D.
    thirds=np.array_split(sample_dates,3); folds={}
    for i,ds in enumerate(thirds,1):
        a=str(pd.Timestamp(ds[0]).date()); b=str(pd.Timestamp(ds[-1]).date())
        folds[f'fold{i}']={'start':a,'end':b,'patterns':{pn:stats([r['o'] for r in obs if a<=r['date']<=b and pattern(pn,r['f'])],40) for pn in PATTERNS}}
    base={str(h):stats([r['o'] for r in obs],h) for h in HORIZONS}
    payload={'generated':datetime.now(timezone.utc).isoformat(timespec='seconds'),'model':'V6 Accumulation / Behaviour Change Research Lab','objective':'Discover point-in-time behavioural anomalies that precede benchmark-relative outperformance and +10/+20/+30 percent winners; no composite score yet.','benchmarks':{'midcap150':mid_sym,'smallcap250':sm_sym},'trading_days':len(dates),'sample_every_sessions':SAMPLE_EVERY,'sample_dates':len(sample_dates),'history_resolved':len(frames),'observations':len(obs),'method':'Each stock compared with its own prior 60-120 session behaviour; weekly sampling; features fixed before outcomes; 10/20/40/60D forward tests.','limitations':'Current-universe survivorship bias; ETF tracking error; no costs/slippage; OHLCV proxies cannot prove institutional accumulation; Yahoo data quality limitations.','baseline':base,'feature_report':feature_report,'pattern_report':pattern_report,'folds':folds}
    path=os.path.join(HERE,'v6_research.json'); open(path,'w').write(json.dumps(payload,separators=(',',':')))
    print('written',path)
    for pn in PATTERNS: print(pn,'40D',pattern_report[pn]['40'],flush=True)
if __name__=='__main__': main()
