#!/usr/bin/env python3
"""V6.2 frozen validation lab.

Validates frozen V6.1 rules on an older pre-discovery period.  Benchmark history
comes first from the official Nifty Indices historical-data endpoint so the
validation is not limited by recent ETF inception dates.  ETF/Yahoo data is only
a fallback.
"""
import json, os, sys, time
from collections import defaultdict
from datetime import datetime, timezone
import numpy as np, pandas as pd, yfinance as yf, requests

HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
sys.path.insert(0,HERE); sys.path.insert(0,ROOT)
import stock_registry as registry
import v6_features

BATCH=35
PERIOD='10y'
SAMPLE_EVERY=5
HORIZONS=[20,40,60]
COOLDOWN=40
DISCOVERY_START=pd.Timestamp('2023-04-18')
MID_FALLBACK=['MID150BEES.NS','NETFMID150.NS']
SMALL_FALLBACK=['HDFCSML250.NS','MOSMALL250.NS']

FROZEN_RULES={
  'R1_p60_80_t1_7_2_5_above_high_bull_strong': {'p_lo':0.60,'p_hi':0.80,'t_lo':1.70,'t_hi':2.50,'loc_lo':0.0,'loc_hi':5.0,'market':'bull','sector':'strong'},
  'R2_p80_100_t1_7_2_5_above_high_bull_strong': {'p_lo':0.80,'p_hi':1.01,'t_lo':1.70,'t_hi':2.50,'loc_lo':0.0,'loc_hi':5.0,'market':'bull','sector':'strong'},
  'R3_p60_80_t2_5plus_below_high_bull_strong': {'p_lo':0.60,'p_hi':0.80,'t_lo':2.50,'t_hi':1e99,'loc_lo':-5.0,'loc_hi':0.0,'market':'bull','sector':'strong'},
  'R4_p80_100_t1_3_1_7_below_high_mixed_strong': {'p_lo':0.80,'p_hi':1.01,'t_lo':1.30,'t_hi':1.70,'loc_lo':-5.0,'loc_hi':0.0,'market':'mixed','sector':'strong'},
  'R5_family_p60plus_t1_7_2_5_near_breakout_bull_strong': {'p_lo':0.60,'p_hi':1.01,'t_lo':1.70,'t_hi':2.50,'loc_lo':-5.0,'loc_hi':5.0,'market':'bull','sector':'strong'},
}

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
            except Exception: pass
    return out

def choose(cands):
    d=fetch(cands); v=[(t,x) for t,x in d.items() if len(x)>=300] or list(d.items())
    return max(v,key=lambda q:len(q[1])) if v else (None,None)

def official_index_history(name,start='01-Jan-2016',end='31-Mar-2023'):
    """Fetch official Nifty price-index OHLC history. Returns Yahoo-like frame."""
    url='https://www.niftyindices.com/Backpage.aspx/getHistoricaldatatabletoString'
    headers={'User-Agent':'Mozilla/5.0','Accept':'application/json, text/javascript, */*; q=0.01','Content-Type':'application/json; charset=UTF-8','Origin':'https://www.niftyindices.com','Referer':'https://www.niftyindices.com/reports/historical-data','X-Requested-With':'XMLHttpRequest'}
    inner={'name':name,'indexName':name,'startDate':start,'endDate':end}
    payload={'cinfo':json.dumps(inner,separators=(',',':'))}
    s=requests.Session()
    try:
        s.get('https://www.niftyindices.com/reports/historical-data',headers=headers,timeout=10)
    except Exception:
        pass
    last=None
    for attempt in range(3):
        try:
            r=s.post(url,headers=headers,json=payload,timeout=30); r.raise_for_status(); obj=r.json()
            raw=obj.get('d','[]'); data=json.loads(raw) if isinstance(raw,str) else raw
            if not data: raise RuntimeError(f'official endpoint returned no rows for {name}')
            rows=[]
            for x in data:
                dt=pd.to_datetime(x.get('HistoricalDate'),dayfirst=True,errors='coerce')
                if pd.isna(dt): continue
                def num(k):
                    v=str(x.get(k,'')).replace(',','').replace('-','').strip()
                    return float(v) if v else np.nan
                rows.append((dt,num('OPEN'),num('HIGH'),num('LOW'),num('CLOSE')))
            if not rows: raise RuntimeError(f'official endpoint parsed no rows for {name}')
            d=pd.DataFrame(rows,columns=['Date','Open','High','Low','Close']).dropna(subset=['Close']).drop_duplicates('Date').sort_values('Date').set_index('Date')
            d['Volume']=0.0
            print(f'official benchmark {name}: {d.index.min().date()} -> {d.index.max().date()} ({len(d)} rows)',flush=True)
            return d
        except Exception as e:
            last=e; time.sleep(2*(attempt+1))
    raise RuntimeError(f'official Nifty history failed for {name}: {last}')

def benchmark_frame(index_name,fallbacks):
    try:
        return index_name,official_index_history(index_name)
    except Exception as e:
        print('WARNING',e,'-- using Yahoo proxy fallback',flush=True)
        sym,d=choose(fallbacks)
        if d is None: raise RuntimeError(f'benchmark unavailable: {index_name}')
        print(f'fallback benchmark {sym}: {d.index.min().date()} -> {d.index.max().date()} ({len(d)} rows)',flush=True)
        return sym,d

def pos(d,day): return pd.DatetimeIndex(d.index).searchsorted(day,side='right')-1
def pastret(d,p,n):
    if p<n:return None
    c=d['Close'].to_numpy(float); return float((c[p]/c[p-n]-1)*100)
def fwd(d,p,n):
    c=d['Close'].to_numpy(float); return None if p+n>=len(c) else float((c[p+n]/c[p]-1)*100)
def excursion(d,p,n):
    c=d['Close'].to_numpy(float)
    if p+n>=len(c): return None,None
    path=c[p+1:p+n+1]/c[p]-1; return float(np.max(path)*100),float(np.min(path)*100)
def outcome(d,p,mid,mp,sm,sp):
    o={}
    for h in HORIZONS:
        r=fwd(d,p,h); mr=fwd(mid,mp,h); sr=fwd(sm,sp,h); mx,dd=excursion(d,p,h)
        o[f'ret{h}']=r; o[f'alpha_mid{h}']=None if r is None or mr is None else r-mr; o[f'alpha_small{h}']=None if r is None or sr is None else r-sr; o[f'max{h}']=mx; o[f'dd{h}']=dd
    return o

def stats(rows,h):
    q=[r for r in rows if r.get(f'ret{h}') is not None and r.get(f'alpha_mid{h}') is not None and r.get(f'alpha_small{h}') is not None]
    if not q:return {'n':0}
    rr=np.array([r[f'ret{h}'] for r in q]); am=np.array([r[f'alpha_mid{h}'] for r in q]); ass=np.array([r[f'alpha_small{h}'] for r in q]); mx=np.array([r[f'max{h}'] for r in q]); dd=np.array([r[f'dd{h}'] for r in q]); z=lambda x:round(float(x),2)
    return {'n':len(q),'median_return':z(np.median(rr)),'mean_return':z(np.mean(rr)),'win_rate':round(float((rr>0).mean()*100),1),'median_alpha_mid150':z(np.median(am)),'median_alpha_small250':z(np.median(ass)),'beat_either_rate':round(float(((am>0)|(ass>0)).mean()*100),1),'beat_both_rate':round(float(((am>0)&(ass>0)).mean()*100),1),'median_max_rise':z(np.median(mx)),'median_max_drawdown':z(np.median(dd)),'hit_plus10_rate':round(float((mx>=10).mean()*100),1),'hit_plus20_rate':round(float((mx>=20).mean()*100),1),'hit_plus30_rate':round(float((mx>=30).mean()*100),1)}

def market_regime(m20,m60,s20,s60):
    if any(x is None for x in (m20,m60,s20,s60)):return 'unknown'
    if m20>0 and m60>0 and s20>0 and s60>0:return 'bull'
    if m20<0 and s20<0 and (m60<0 or s60<0):return 'bear'
    return 'mixed'
def sector_regime(median20,breadth):
    if median20 is None:return 'unknown'
    if median20>2 and breadth>=0.55:return 'strong'
    if median20<-2 and breadth<0.45:return 'weak'
    return 'neutral'
def matches(rule,r):
    f=r['f']; return (rule['p_lo']<=f['turnover_persistence']<rule['p_hi'] and rule['t_lo']<=f['turnover_change']<rule['t_hi'] and rule['loc_lo']<=f['from_high120']<=rule['loc_hi'] and r['market_regime']==rule['market'] and r['sector_regime']==rule['sector'])
def cooldown_rows(rows):
    kept=[]; last={}
    for r in rows:
        idx=r['sample_idx']; t=r['ticker']
        if t in last and idx-last[t]<COOLDOWN:continue
        kept.append(r); last[t]=idx
    return kept

def main():
    reg=registry.load(); meta=registry.by_yahoo(reg); uni=registry.read_universe(os.path.join(ROOT,'trend','universe.txt'))
    mid_sym,mid=benchmark_frame('NIFTY MIDCAP 150',MID_FALLBACK)
    sm_sym,sm=benchmark_frame('NIFTY SMALLCAP 250',SMALL_FALLBACK)
    frames=fetch(uni)
    common=pd.DatetimeIndex(mid.index).intersection(pd.DatetimeIndex(sm.index)).sort_values()
    cutoff_pos=common.searchsorted(DISCOVERY_START,side='left')-1; end_pos=cutoff_pos-max(HORIZONS)
    if end_pos<=145:
        raise RuntimeError(f'insufficient pre-discovery benchmark history after 145D warmup; common={len(common)} first={common.min() if len(common) else None} last={common.max() if len(common) else None}')
    eligible=common[:end_pos+1]; eligible=eligible[eligible>=pd.Timestamp('2017-01-01')]; sample_dates=list(eligible[::SAMPLE_EVERY])
    if len(sample_dates)<80: raise RuntimeError(f'validation window too short: only {len(sample_dates)} sample dates')
    obs=[]
    for di,day in enumerate(sample_dates):
        if di%30==0:print(f'validate {di+1}/{len(sample_dates)} {day.date()}',flush=True)
        mp=pos(mid,day); sp=pos(sm,day)
        if min(mp,sp)<145:continue
        bm={n:pastret(mid,mp,n) for n in (10,20,60)}; mr=market_regime(pastret(mid,mp,20),pastret(mid,mp,60),pastret(sm,sp,20),pastret(sm,sp,60)); dayrows=[]
        for t,d in frames.items():
            p=pos(d,day)
            if p<145:continue
            f=v6_features.features(d.iloc[:p+1],bm)
            if f is None or f['turnover_cr']<0.25 or f['event20']>20:continue
            sector=(meta.get(t) or {}).get('industry_group') or 'Unknown'; dayrows.append({'date':str(day.date()),'sample_idx':di*SAMPLE_EVERY,'ticker':t,'sector':sector,'market_regime':mr,'f':f,'o':outcome(d,p,mid,mp,sm,sp)})
        sec=defaultdict(list)
        for r in dayrows:
            if r['f']['r20'] is not None:sec[r['sector']].append(r['f']['r20'])
        secstat={k:(float(np.median(v)),float(np.mean(np.array(v)>0))) for k,v in sec.items() if v}
        for r in dayrows:
            med,br=secstat.get(r['sector'],(None,None)); r['sector_regime']=sector_regime(med,br); obs.append(r)
    results={}
    for name,rule in FROZEN_RULES.items():
        raw=[r for r in obs if matches(rule,r)]; cd=cooldown_rows(raw)
        e={'rule':rule,'raw_observations':len(raw),'cooldown_observations':len(cd),'avg_signals_per_sample_day_raw':round(len(raw)/max(1,len(sample_dates)),2),'avg_signals_per_sample_day_cooldown':round(len(cd)/max(1,len(sample_dates)),2),'all':{str(h):stats([r['o'] for r in cd],h) for h in HORIZONS},'folds':{}}
        if cd:
            dates=sorted({r['date'] for r in cd}); chunks=np.array_split(dates,3)
            for i,ch in enumerate(chunks,1):
                if len(ch)==0:continue
                a,b=str(ch[0]),str(ch[-1]); q=[r['o'] for r in cd if a<=r['date']<=b]; e['folds'][f'fold{i}']={'start':a,'end':b,'stats40':stats(q,40),'stats60':stats(q,60)}
        results[name]=e
    gates={}
    for name,e in results.items():
        s=e['all']['40']; fold40=[x['stats40'] for x in e['folds'].values() if x.get('stats40',{}).get('n',0)>0]
        g={'enough_n':s.get('n',0)>=150,'positive_alpha_both':s.get('median_alpha_mid150',-999)>0 and s.get('median_alpha_small250',-999)>0,'beat_either_55':s.get('beat_either_rate',0)>=55,'folds_nonnegative_small_alpha':len(fold40)>=2 and sum(x.get('median_alpha_small250',-999)>=0 for x in fold40)>=2}; g['passes_research_gate']=all(g.values()); gates[name]=g
    payload={'generated':datetime.now(timezone.utc).isoformat(timespec='seconds'),'model':'V6.2 Frozen Pre-Discovery Validation','discovery_period_start':str(DISCOVERY_START.date()),'validation_period_start':str(sample_dates[0].date()),'validation_period_end':str(sample_dates[-1].date()),'benchmarks':{'midcap150':mid_sym,'smallcap250':sm_sym},'history_resolved':len(frames),'sample_dates':len(sample_dates),'raw_investable_observations':len(obs),'cooldown_sessions':COOLDOWN,'method':'Frozen V6.1 rules tested only on older pre-discovery data; official Nifty index history preferred; weekly point-in-time scans; all 60D outcomes end before discovery period; 40-session per-ticker cooldown.','limitations':'Current-universe survivorship bias; current industry mapping; no transaction costs/slippage; Yahoo stock history varies by ticker; current listed universe omits historical delistings.','frozen_rules':FROZEN_RULES,'results':results,'research_gates':gates}
    path=os.path.join(HERE,'v6_2_validation.json'); open(path,'w').write(json.dumps(payload,separators=(',',':'))); print('written',path,flush=True)
    for name,e in results.items():print(name,e['all']['40'],gates[name],flush=True)
if __name__=='__main__':main()
