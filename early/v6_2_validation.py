#!/usr/bin/env python3
"""V6.2 recent-regime frozen validation.

Project rule: model-selection backtests use only the most recent ~3 years.
Older data may be fetched only as feature warm-up; it is not scored.

Frozen V6.1 rules are evaluated with:
- most recent 756 benchmark trading sessions, reserving 60 sessions for outcomes
- weekly point-in-time sampling
- 40-trading-session per-ticker cooldown
- chronological 50% development / 25% validation / 25% final holdout
- 20/40/60D outcomes vs Nifty Midcap 150 and Nifty Smallcap 250 ETF proxies

No V6.1 threshold is tuned here. This remains research, not a live trading score.
"""
import json, os, sys
from collections import defaultdict
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import yfinance as yf

HERE=os.path.dirname(os.path.abspath(__file__))
ROOT=os.path.dirname(HERE)
sys.path.insert(0,HERE)
sys.path.insert(0,ROOT)
import stock_registry as registry
import v6_features

BATCH=35
PERIOD='5y'                 # includes warm-up; scored window is capped below
TEST_DAYS=756               # ~3 trading years
SAMPLE_EVERY=5              # weekly observation grid
HORIZONS=[20,40,60]
COOLDOWN=40                 # trading sessions, not observations
MID=['MID150BEES.NS']
SMALL=['HDFCSML250.NS','MOSMALL250.NS']

# Frozen after V6.1. Do not tune these from V6.2 results.
FROZEN_RULES={
  'R1_p60_80_t1_7_2_5_above_high_bull_strong': {
    'p_lo':0.60,'p_hi':0.80,'t_lo':1.70,'t_hi':2.50,'loc_lo':0.0,'loc_hi':5.0,
    'market':'bull','sector':'strong'
  },
  'R2_p80_100_t1_7_2_5_above_high_bull_strong': {
    'p_lo':0.80,'p_hi':1.01,'t_lo':1.70,'t_hi':2.50,'loc_lo':0.0,'loc_hi':5.0,
    'market':'bull','sector':'strong'
  },
  'R3_p60_80_t2_5plus_below_high_bull_strong': {
    'p_lo':0.60,'p_hi':0.80,'t_lo':2.50,'t_hi':1e99,'loc_lo':-5.0,'loc_hi':0.0,
    'market':'bull','sector':'strong'
  },
  'R4_p80_100_t1_3_1_7_below_high_mixed_strong': {
    'p_lo':0.80,'p_hi':1.01,'t_lo':1.30,'t_hi':1.70,'loc_lo':-5.0,'loc_hi':0.0,
    'market':'mixed','sector':'strong'
  },
  'R5_family_p60plus_t1_7_2_5_near_breakout_bull_strong': {
    'p_lo':0.60,'p_hi':1.01,'t_lo':1.70,'t_hi':2.50,'loc_lo':-5.0,'loc_hi':5.0,
    'market':'bull','sector':'strong'
  },
}

def fetch(ts):
    out={}
    for i in range(0,len(ts),BATCH):
        ch=ts[i:i+BATCH]
        print(f'fetch {i+1}-{i+len(ch)}/{len(ts)}',flush=True)
        z=yf.download(ch,period=PERIOD,interval='1d',group_by='ticker',auto_adjust=True,progress=False,threads=True)
        if z is None or z.empty:
            continue
        for t in ch:
            try:
                d=z[t] if isinstance(z.columns,pd.MultiIndex) else z
                d=d.dropna(subset=['Close'])
                if not d.empty:
                    out[t]=d
            except Exception:
                pass
    return out

def choose(cands):
    d=fetch(cands)
    v=[(t,x) for t,x in d.items() if len(x)>=700] or list(d.items())
    return max(v,key=lambda q:len(q[1])) if v else (None,None)

def pos(d,day):
    return pd.DatetimeIndex(d.index).searchsorted(day,side='right')-1

def pastret(d,p,n):
    if p<n: return None
    c=d['Close'].to_numpy(float)
    return float((c[p]/c[p-n]-1)*100)

def fwd(d,p,n):
    c=d['Close'].to_numpy(float)
    return None if p+n>=len(c) else float((c[p+n]/c[p]-1)*100)

def excursion(d,p,n):
    c=d['Close'].to_numpy(float)
    if p+n>=len(c): return None,None
    path=c[p+1:p+n+1]/c[p]-1
    return float(np.max(path)*100),float(np.min(path)*100)

def outcome(d,p,mid,mp,sm,sp):
    o={}
    for h in HORIZONS:
        r=fwd(d,p,h); mr=fwd(mid,mp,h); sr=fwd(sm,sp,h); mx,dd=excursion(d,p,h)
        o[f'ret{h}']=r
        o[f'alpha_mid{h}']=None if r is None or mr is None else r-mr
        o[f'alpha_small{h}']=None if r is None or sr is None else r-sr
        o[f'max{h}']=mx; o[f'dd{h}']=dd
    return o

def stats(rows,h):
    q=[r for r in rows if r.get(f'ret{h}') is not None and r.get(f'alpha_mid{h}') is not None and r.get(f'alpha_small{h}') is not None]
    if not q: return {'n':0}
    rr=np.array([r[f'ret{h}'] for r in q])
    am=np.array([r[f'alpha_mid{h}'] for r in q])
    ass=np.array([r[f'alpha_small{h}'] for r in q])
    mx=np.array([r[f'max{h}'] for r in q])
    dd=np.array([r[f'dd{h}'] for r in q])
    z=lambda x:round(float(x),2)
    return {
      'n':len(q),'median_return':z(np.median(rr)),'mean_return':z(np.mean(rr)),
      'win_rate':round(float((rr>0).mean()*100),1),
      'median_alpha_mid150':z(np.median(am)),'median_alpha_small250':z(np.median(ass)),
      'beat_mid150_rate':round(float((am>0).mean()*100),1),
      'beat_small250_rate':round(float((ass>0).mean()*100),1),
      'beat_either_rate':round(float(((am>0)|(ass>0)).mean()*100),1),
      'beat_both_rate':round(float(((am>0)&(ass>0)).mean()*100),1),
      'median_max_rise':z(np.median(mx)),'median_max_drawdown':z(np.median(dd)),
      'hit_plus10_rate':round(float((mx>=10).mean()*100),1),
      'hit_plus20_rate':round(float((mx>=20).mean()*100),1),
      'hit_plus30_rate':round(float((mx>=30).mean()*100),1)
    }

def market_regime(m20,m60,s20,s60):
    if any(x is None for x in (m20,m60,s20,s60)): return 'unknown'
    if m20>0 and m60>0 and s20>0 and s60>0: return 'bull'
    if m20<0 and s20<0 and (m60<0 or s60<0): return 'bear'
    return 'mixed'

def sector_regime(median20,breadth):
    if median20 is None: return 'unknown'
    if median20>2 and breadth>=0.55: return 'strong'
    if median20<-2 and breadth<0.45: return 'weak'
    return 'neutral'

def matches(rule,r):
    f=r['f']
    return (rule['p_lo']<=f['turnover_persistence']<rule['p_hi'] and
            rule['t_lo']<=f['turnover_change']<rule['t_hi'] and
            rule['loc_lo']<=f['from_high120']<=rule['loc_hi'] and
            r['market_regime']==rule['market'] and r['sector_regime']==rule['sector'])

def cooldown_rows(rows):
    kept=[]; last={}
    for r in sorted(rows,key=lambda x:(x['sample_idx'],x['ticker'])):
        t=r['ticker']; idx=r['sample_idx']
        if t in last and idx-last[t] < COOLDOWN:
            continue
        kept.append(r); last[t]=idx
    return kept

def period_stats(rows,period,h):
    a,b=period['start'],period['end']
    return stats([r['o'] for r in rows if a<=r['date']<=b],h)

def main():
    reg=registry.load(); meta=registry.by_yahoo(reg)
    uni=registry.read_universe(os.path.join(ROOT,'trend','universe.txt'))
    mid_sym,mid=choose(MID); sm_sym,sm=choose(SMALL)
    if mid is None or sm is None:
        raise RuntimeError('benchmark unavailable')
    print(f'mid benchmark {mid_sym}: {mid.index.min().date()} -> {mid.index.max().date()} ({len(mid)} rows)',flush=True)
    print(f'small benchmark {sm_sym}: {sm.index.min().date()} -> {sm.index.max().date()} ({len(sm)} rows)',flush=True)

    frames=fetch(uni)
    common=pd.DatetimeIndex(mid.index).intersection(pd.DatetimeIndex(sm.index)).sort_values()
    need=TEST_DAYS+max(HORIZONS)
    if len(common)<need:
        raise RuntimeError(f'need at least {need} common benchmark sessions, have {len(common)}')

    # Scored dates are strictly the most recent ~3 years whose 60D outcomes are known.
    scored_days=list(common[-need:-max(HORIZONS)])
    if len(scored_days)!=TEST_DAYS:
        raise RuntimeError(f'expected {TEST_DAYS} scored days, got {len(scored_days)}')
    sample_dates=scored_days[::SAMPLE_EVERY]

    # Fixed chronological split: 50% development, 25% validation, 25% final holdout.
    n=len(sample_dates); i1=n//2; i2=(3*n)//4
    segments={
      'development':{'start':str(pd.Timestamp(sample_dates[0]).date()),'end':str(pd.Timestamp(sample_dates[i1-1]).date())},
      'validation':{'start':str(pd.Timestamp(sample_dates[i1]).date()),'end':str(pd.Timestamp(sample_dates[i2-1]).date())},
      'final_holdout':{'start':str(pd.Timestamp(sample_dates[i2]).date()),'end':str(pd.Timestamp(sample_dates[-1]).date())},
    }
    print('segments',segments,flush=True)

    obs=[]
    for di,day in enumerate(sample_dates):
        if di%20==0: print(f'V6.2 {di+1}/{len(sample_dates)} {day.date()}',flush=True)
        mp=pos(mid,day); sp=pos(sm,day)
        if min(mp,sp)<145: continue
        bm={k:pastret(mid,mp,k) for k in (10,20,60)}
        mr=market_regime(pastret(mid,mp,20),pastret(mid,mp,60),pastret(sm,sp,20),pastret(sm,sp,60))
        dayrows=[]
        for t,d in frames.items():
            p=pos(d,day)
            if p<145: continue
            f=v6_features.features(d.iloc[:p+1],bm)
            if f is None or f['turnover_cr']<0.25 or f['event20']>20: continue
            sector=(meta.get(t) or {}).get('industry_group') or 'Unknown'
            dayrows.append({'date':str(day.date()),'sample_idx':di*SAMPLE_EVERY,'ticker':t,'sector':sector,'market_regime':mr,'f':f,'o':outcome(d,p,mid,mp,sm,sp)})
        sec=defaultdict(list)
        for r in dayrows:
            if r['f']['r20'] is not None: sec[r['sector']].append(r['f']['r20'])
        secstat={k:(float(np.median(v)),float(np.mean(np.array(v)>0))) for k,v in sec.items() if v}
        for r in dayrows:
            med,br=secstat.get(r['sector'],(None,None))
            r['sector_regime']=sector_regime(med,br)
            obs.append(r)

    results={}
    for name,rule in FROZEN_RULES.items():
        raw=[r for r in obs if matches(rule,r)]
        cd=cooldown_rows(raw)
        e={
          'rule':rule,
          'raw_observations':len(raw),'cooldown_observations':len(cd),
          'avg_signals_per_sample_day_raw':round(len(raw)/max(1,len(sample_dates)),2),
          'avg_signals_per_sample_day_cooldown':round(len(cd)/max(1,len(sample_dates)),2),
          'all':{str(h):stats([r['o'] for r in cd],h) for h in HORIZONS},
          'segments':{}
        }
        for seg,pdct in segments.items():
            e['segments'][seg]={'start':pdct['start'],'end':pdct['end']}
            for h in HORIZONS:
                e['segments'][seg][f'stats{h}']=period_stats(cd,pdct,h)
        results[name]=e

    # Final holdout is the primary promotion gate. Full-period results are context only.
    gates={}
    for name,e in results.items():
        s=e['segments']['final_holdout']['stats40']
        v=e['segments']['validation']['stats40']
        g={
          'holdout_enough_n':s.get('n',0)>=40,
          'holdout_positive_alpha_both':s.get('median_alpha_mid150',-999)>0 and s.get('median_alpha_small250',-999)>0,
          'holdout_beat_either_55':s.get('beat_either_rate',0)>=55,
          'validation_not_broken':v.get('n',0)>=40 and v.get('median_alpha_mid150',-999)>-1 and v.get('median_alpha_small250',-999)>-1,
          'passes_research_gate':False
        }
        g['passes_research_gate']=all(vv for kk,vv in g.items() if kk!='passes_research_gate')
        gates[name]=g

    payload={
      'generated':datetime.now(timezone.utc).isoformat(timespec='seconds'),
      'model':'V6.2 Recent 3-Year Frozen Validation',
      'project_backtest_policy':'Model-selection window capped at the most recent ~3 trading years. Older observations are warm-up only and not scored.',
      'benchmarks':{'midcap150':mid_sym,'smallcap250':sm_sym},
      'scored_trading_days':len(scored_days),'sample_dates':len(sample_dates),
      'scored_period_start':str(pd.Timestamp(scored_days[0]).date()),
      'scored_period_end':str(pd.Timestamp(scored_days[-1]).date()),
      'segments':segments,'history_resolved':len(frames),'raw_investable_observations':len(obs),
      'cooldown_sessions':COOLDOWN,
      'method':'Frozen V6.1 rules; recent 756 trading sessions only; weekly point-in-time observations; 40-session per-ticker cooldown; 50/25/25 chronological split; 20/40/60D outcomes.',
      'limitations':'Current-universe survivorship bias; current industry mapping; ETF benchmark tracking error; no costs/slippage; Yahoo stock-history quality varies; recent periods have already informed earlier V6 research, so the final holdout is stricter but not perfectly untouched. Prospective tracking remains the cleanest future validation.',
      'frozen_rules':FROZEN_RULES,'results':results,'research_gates':gates
    }
    path=os.path.join(HERE,'v6_2_validation.json')
    open(path,'w').write(json.dumps(payload,separators=(',',':')))
    print('written',path,flush=True)
    for name,e in results.items():
        print(name,'holdout40',e['segments']['final_holdout']['stats40'],gates[name],flush=True)

if __name__=='__main__':
    main()
