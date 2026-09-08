#!/usr/bin/env python3
"""V6.1 research lab.

Narrow follow-up to V6. No production score.
Tests the interaction of sustained participation, turnover expansion, price
location, and market/sector regime. Reports full cells, candidate cells,
forward alpha, big-winner rates, folds, and signal frequency.
"""
import json, os, sys
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
        o[f'ret{h}']=r; o[f'alpha_mid{h}']=None if r is None or mr is None else r-mr
        o[f'alpha_small{h}']=None if r is None or sr is None else r-sr; o[f'max{h}']=mx; o[f'dd{h}']=dd
    return o

def stats(rows,h):
    q=[r for r in rows if r.get(f'ret{h}') is not None and r.get(f'alpha_mid{h}') is not None and r.get(f'alpha_small{h}') is not None]
    if not q:return {'n':0}
    rr=np.array([r[f'ret{h}'] for r in q]); am=np.array([r[f'alpha_mid{h}'] for r in q]); ass=np.array([r[f'alpha_small{h}'] for r in q]); mx=np.array([r[f'max{h}'] for r in q]); dd=np.array([r[f'dd{h}'] for r in q])
    z=lambda x:round(float(x),2)
    return {'n':len(q),'median_return':z(np.median(rr)),'mean_return':z(np.mean(rr)),'win_rate':round(float((rr>0).mean()*100),1),'median_alpha_mid150':z(np.median(am)),'median_alpha_small250':z(np.median(ass)),'beat_either_rate':round(float(((am>0)|(ass>0)).mean()*100),1),'beat_both_rate':round(float(((am>0)&(ass>0)).mean()*100),1),'median_max_rise':z(np.median(mx)),'median_max_drawdown':z(np.median(dd)),'hit_plus10_rate':round(float((mx>=10).mean()*100),1),'hit_plus20_rate':round(float((mx>=20).mean()*100),1),'hit_plus30_rate':round(float((mx>=30).mean()*100),1)}

def pbin(x):
    if x<0.4:return '<40%'
    if x<0.6:return '40-60%'
    if x<0.8:return '60-80%'
    return '80-100%'
def tbin(x):
    if x<1:return '<1.0x'
    if x<1.3:return '1.0-1.3x'
    if x<1.7:return '1.3-1.7x'
    if x<2.5:return '1.7-2.5x'
    return '>=2.5x'
def lbin(x):
    if x<-20:return '<-20%'
    if x<-10:return '-20 to -10%'
    if x<-5:return '-10 to -5%'
    if x<=0:return '-5 to 0%'
    if x<=5:return '0 to +5%'
    return '>+5%'
def market_regime(m20,m60,s20,s60):
    vals=[x for x in (m20,m60,s20,s60) if x is not None]
    if len(vals)<4:return 'unknown'
    if m20>0 and m60>0 and s20>0 and s60>0:return 'bull'
    if m20<0 and s20<0 and (m60<0 or s60<0):return 'bear'
    return 'mixed'
def sector_regime(median20,breadth):
    if median20 is None:return 'unknown'
    if median20>2 and breadth>=0.55:return 'strong'
    if median20<-2 and breadth<0.45:return 'weak'
    return 'neutral'

def main():
    reg=registry.load(); meta=registry.by_yahoo(reg)
    uni=registry.read_universe(os.path.join(ROOT,'trend','universe.txt'))
    mid_sym,mid=choose(MID); sm_sym,sm=choose(SMALL)
    if mid is None or sm is None: raise RuntimeError('benchmark unavailable')
    frames=fetch(uni)
    common=pd.DatetimeIndex(mid.index).intersection(pd.DatetimeIndex(sm.index))
    dates=list(common[-(TEST_DAYS+60):-60])[-TEST_DAYS:]; sample_dates=dates[::SAMPLE_EVERY]
    obs=[]
    for di,day in enumerate(sample_dates,1):
        if di%20==1: print(f'V6.1 {di}/{len(sample_dates)} {day.date()}',flush=True)
        mp=pos(mid,day); sp=pos(sm,day)
        if min(mp,sp)<145:continue
        bm={n:pastret(mid,mp,n) for n in (10,20,60)}
        mr=market_regime(pastret(mid,mp,20),pastret(mid,mp,60),pastret(sm,sp,20),pastret(sm,sp,60))
        dayrows=[]
        for t,d in frames.items():
            p=pos(d,day)
            if p<145:continue
            f=v6_features.features(d.iloc[:p+1],bm)
            if f is None or f['turnover_cr']<0.25 or f['event20']>20:continue
            sector=(meta.get(t) or {}).get('industry_group') or 'Unknown'
            dayrows.append({'date':str(day.date()),'ticker':t,'sector':sector,'market_regime':mr,'f':f,'o':outcome(d,p,mid,mp,sm,sp)})
        sec=defaultdict(list)
        for r in dayrows:
            if r['f']['r20'] is not None: sec[r['sector']].append(r['f']['r20'])
        secstat={k:(float(np.median(v)),float(np.mean(np.array(v)>0))) for k,v in sec.items() if v}
        for r in dayrows:
            med,br=secstat.get(r['sector'],(None,None)); r['sector_regime']=sector_regime(med,br)
            f=r['f']; r['bins']={'persistence':pbin(f['turnover_persistence']),'turnover':tbin(f['turnover_change']),'location':lbin(f['from_high120'])}
            obs.append(r)
    print('observations',len(obs),flush=True)

    baseline={str(h):stats([r['o'] for r in obs],h) for h in HORIZONS}
    cells=defaultdict(list)
    for r in obs:
        key='|'.join([r['bins']['persistence'],r['bins']['turnover'],r['bins']['location'],r['market_regime'],r['sector_regime']])
        cells[key].append(r['o'])
    cell_report=[]
    for key,rows in cells.items():
        s40=stats(rows,40)
        if s40['n']<80:continue
        parts=key.split('|')
        cell_report.append({'persistence':parts[0],'turnover':parts[1],'location':parts[2],'market_regime':parts[3],'sector_regime':parts[4],'stats40':s40,'stats60':stats(rows,60)})
    cell_report.sort(key=lambda x:(x['stats40'].get('median_alpha_small250',-999),x['stats40'].get('beat_either_rate',0),x['stats40'].get('hit_plus20_rate',0)),reverse=True)

    # Predeclared practical hypotheses, chosen from V6 direction rather than optimized weights.
    def cand(name,r):
        f=r['f']; loc=f['from_high120']; tc=f['turnover_change']; p=f['turnover_persistence']
        if name=='A_persistent_near_high': return p>=0.8 and 1.3<=tc<2.5 and -10<=loc<=2
        if name=='B_moderate_persistent_near_high': return p>=0.6 and 1.3<=tc<2.5 and -10<=loc<=2
        if name=='C_persistent_near_high_good_regime': return p>=0.8 and 1.3<=tc<2.5 and -10<=loc<=2 and r['market_regime']!='bear' and r['sector_regime']!='weak'
        if name=='D_very_near_high_good_regime': return p>=0.7 and 1.2<=tc<2.5 and -5<=loc<=2 and r['market_regime']!='bear' and r['sector_regime']!='weak'
        if name=='E_extreme_turnover_near_high': return p>=0.8 and tc>=2.5 and -10<=loc<=2
        return False
    names=['A_persistent_near_high','B_moderate_persistent_near_high','C_persistent_near_high_good_regime','D_very_near_high_good_regime','E_extreme_turnover_near_high']
    candidates={}
    thirds=np.array_split(sample_dates,3)
    for n in names:
        rs=[r for r in obs if cand(n,r)]
        entry={'all':{str(h):stats([r['o'] for r in rs],h) for h in HORIZONS},'avg_signals_per_sample_day':round(len(rs)/max(1,len(sample_dates)),1),'folds':{}}
        for i,ds in enumerate(thirds,1):
            a=str(pd.Timestamp(ds[0]).date()); b=str(pd.Timestamp(ds[-1]).date()); q=[r['o'] for r in rs if a<=r['date']<=b]
            entry['folds'][f'fold{i}']={'start':a,'end':b,'stats40':stats(q,40),'stats60':stats(q,60)}
        candidates[n]=entry

    payload={'generated':datetime.now(timezone.utc).isoformat(timespec='seconds'),'model':'V6.1 Participation x Location x Regime Research Lab','objective':'Test whether V6 participation signal becomes investable when conditioned on price location and market/sector regime; no live score.','benchmarks':{'midcap150':mid_sym,'smallcap250':sm_sym},'trading_days':len(dates),'sample_dates':len(sample_dates),'history_resolved':len(frames),'observations':len(obs),'method':'Weekly point-in-time observations; stock-own-history participation bins x 120D price location x contemporaneous market and sector regimes; outcomes 10/20/40/60D.','limitations':'Current-universe survivorship bias; current industry mapping; ETF tracking error; no costs/slippage; weekly observations overlap at longer horizons; OHLCV cannot prove institutional accumulation.','baseline':baseline,'candidate_hypotheses':candidates,'top_cells':cell_report[:80]}
    path=os.path.join(HERE,'v6_1_research.json'); open(path,'w').write(json.dumps(payload,separators=(',',':')))
    print('written',path,flush=True)
    for n in names: print(n,candidates[n]['all']['40'],flush=True)
if __name__=='__main__': main()
