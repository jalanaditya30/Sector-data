#!/usr/bin/env python3
"""V6.2 Live Candidate Radar + prospective tracker.

Research-only companion to the production V2 radar. Frozen R2/R5 rules are taken
from V6.2 validation and MUST NOT be tuned by this live script.

A separate Near Candidate layer is descriptive only: it shows stocks satisfying
4 of the 5 R5 building blocks so a blank R2/R5 day still shows what is brewing.
Near Candidates NEVER enter the immutable R2/R5 prospective archive.
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
import stock_registry as registry
import v6_features

BATCH=35
PERIOD='1y'
HORIZONS=(20,40,60)
MID='MID150BEES.NS'
SMALL='HDFCSML250.NS'
MIN_TURNOVER_CR=0.25
EVENT_CAP=20.0

R2={'p_lo':0.80,'p_hi':1.01,'t_lo':1.70,'t_hi':2.50,'loc_lo':0.0,'loc_hi':5.0}
R5={'p_lo':0.60,'p_hi':1.01,'t_lo':1.70,'t_hi':2.50,'loc_lo':-5.0,'loc_hi':5.0}


def fetch(tickers, period=PERIOD):
    out={}; tickers=list(dict.fromkeys(tickers))
    for i in range(0,len(tickers),BATCH):
        ch=tickers[i:i+BATCH]
        print(f'fetch {i+1}-{i+len(ch)}/{len(tickers)}',flush=True)
        z=yf.download(ch,period=period,interval='1d',group_by='ticker',auto_adjust=True,progress=False,threads=True)
        if z is None or z.empty: continue
        for t in ch:
            try:
                d=z[t] if isinstance(z.columns,pd.MultiIndex) else z
                d=d.dropna(subset=['Close'])
                if not d.empty: out[t]=d
            except Exception: pass
    return out


def ret(df,n):
    if df is None or len(df)<=n:return None
    c=df['Close'].to_numpy(float)
    return float((c[-1]/c[-1-n]-1)*100)


def market_regime(mid,small):
    vals=(ret(mid,20),ret(mid,60),ret(small,20),ret(small,60))
    if any(x is None for x in vals): return 'unknown',vals
    m20,m60,s20,s60=vals
    if m20>0 and m60>0 and s20>0 and s60>0:return 'bull',vals
    if m20<0 and s20<0 and (m60<0 or s60<0):return 'bear',vals
    return 'mixed',vals


def sector_regime(median20,breadth):
    if median20 is None:return 'unknown'
    if median20>2 and breadth>=0.55:return 'strong'
    if median20<-2 and breadth<0.45:return 'weak'
    return 'neutral'


def match(rule,f):
    return (rule['p_lo']<=f['turnover_persistence']<rule['p_hi'] and
            rule['t_lo']<=f['turnover_change']<rule['t_hi'] and
            rule['loc_lo']<=f['from_high120']<=rule['loc_hi'])


def liquidity_label(turn):
    if turn is None:return 'unknown'
    if turn<0.75:return 'low'
    if turn<2.0:return 'moderate'
    return 'practical'


def near_r5_checks(f,market,sector):
    checks={
      'Persistent participation': bool(R5['p_lo']<=f['turnover_persistence']<R5['p_hi']),
      'Controlled turnover 1.7–2.5×': bool(R5['t_lo']<=f['turnover_change']<R5['t_hi']),
      'Within ±5% of 120D high': bool(R5['loc_lo']<=f['from_high120']<=R5['loc_hi']),
      'Bull market': bool(market=='bull'),
      'Strong sector': bool(sector=='strong'),
    }
    return checks,sum(checks.values())


def display_row(r,f,s,sr,mr,is_r2=False,is_r5=False):
    return {
      'ticker':r['ticker'],'symbol':r['symbol'],'name':r['name'],'sector':r['sector'],
      'bar_date':r['bar_date'],'entry_price':r['entry_price'],
      'candidate':'R2 High Conviction' if is_r2 else ('R5 Candidate' if is_r5 else 'Near R5'),
      'r2':bool(is_r2),'r5':bool(is_r5),'turnover_persistence':round(f['turnover_persistence']*100,1),
      'turnover_change':round(f['turnover_change'],2),'turnover_cr':round(f['turnover_cr'],2),
      'liquidity':liquidity_label(f['turnover_cr']),'from_high120':round(f['from_high120'],2),
      'r5_ret':round(f['r5'],2) if f['r5'] is not None else None,
      'r20':round(f['r20'],2) if f['r20'] is not None else None,
      'r60':round(f['r60'],2) if f['r60'] is not None else None,
      'market_regime':mr,'sector_regime':sr,
      'sector_median20':round(s.get('median20',0),2),'sector_breadth20':round(s.get('breadth20',0)*100,1),
      'event20':round(f['event20'],2)
    }


def build_live():
    reg=registry.load(); meta=registry.by_yahoo(reg)
    uni=registry.read_universe(os.path.join(ROOT,'trend','universe.txt'))
    frames=fetch(uni+[MID,SMALL])
    mid=frames.get(MID); small=frames.get(SMALL)
    if mid is None or small is None: raise RuntimeError('benchmark data unavailable')
    mr,mvals=market_regime(mid,small)
    bm={10:ret(mid,10),20:ret(mid,20),60:ret(mid,60)}

    rows=[]
    for t in uni:
        d=frames.get(t)
        if d is None or len(d)<145:continue
        f=v6_features.features(d,bm)
        if f is None or f['turnover_cr']<MIN_TURNOVER_CR or f['event20']>EVENT_CAP:continue
        m=meta.get(t,{})
        rows.append({'ticker':t,'symbol':m.get('nse') or t.replace('.NS',''),'name':m.get('name') or t,
          'sector':m.get('industry_group') or 'Unknown','bar_date':str(pd.Timestamp(d.index[-1]).date()),
          'entry_price':round(float(d['Close'].iloc[-1]),2),'f':f})

    sec=defaultdict(list)
    for r in rows:
        if r['f']['r20'] is not None:sec[r['sector']].append(r['f']['r20'])
    secstat={k:{'median20':float(np.median(v)),'breadth20':float(np.mean(np.array(v)>0))} for k,v in sec.items() if v}

    candidates=[]; near=[]
    for r in rows:
        s=secstat.get(r['sector'],{})
        sr=sector_regime(s.get('median20'),s.get('breadth20',0)); f=r['f']
        is_r2=(mr=='bull' and sr=='strong' and match(R2,f))
        is_r5=(mr=='bull' and sr=='strong' and match(R5,f))
        if is_r5:
            candidates.append(display_row(r,f,s,sr,mr,is_r2,is_r5=True))
            continue
        checks,count=near_r5_checks(f,mr,sr)
        if count>=4:
            x=display_row(r,f,s,sr,mr)
            x['near_match_count']=count
            x['near_checks']=checks
            x['missing_conditions']=[k for k,v in checks.items() if not v]
            near.append(x)

    candidates.sort(key=lambda x:(not x['r2'],-x['turnover_persistence'],-x['turnover_cr']))
    near.sort(key=lambda x:(-x['near_match_count'],len(x['missing_conditions']),-x['turnover_persistence'],-x['turnover_cr']))

    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    payload={'generated':now,'model':'V6.2 Live Research Candidate','status':'research_candidate_not_buy_signal',
      'rules_frozen':True,'market_regime':mr,
      'benchmark_returns':{'mid20':round(mvals[0],2),'mid60':round(mvals[1],2),'small20':round(mvals[2],2),'small60':round(mvals[3],2)},
      'universe_requested':len(uni),'history_resolved':sum(t in frames for t in uni),'investable_rows':len(rows),
      'r2_count':sum(x['r2'] for x in candidates),'r5_total_count':len(candidates),
      'near_count':len(near),'candidates':candidates,'near_candidates':near[:100],
      'notes':'R2/R5 thresholds remain frozen. Near R5 is descriptive only: 4/5 R5 building blocks, never archived as a validated signal.'}
    with open(os.path.join(HERE,'v62_live.json'),'w') as f:json.dump(payload,f,separators=(',',':'))

    # Immutable archive contains ONLY genuine frozen R2/R5 signals.
    hist=os.path.join(HERE,'v62_history'); os.makedirs(hist,exist_ok=True)
    day=max((x['bar_date'] for x in candidates),default=str(pd.Timestamp(mid.index[-1]).date()))
    hp=os.path.join(hist,day+'.json')
    if not os.path.exists(hp):
        snap={'date':day,'generated':now,'market_regime':mr,'rules_frozen':True,'signals':candidates}
        with open(hp,'w') as f:json.dump(snap,f,separators=(',',':'))
        print('archived',hp,len(candidates),'signals',flush=True)
    else: print('archive exists; preserving immutable snapshot',hp,flush=True)
    return payload


def _pos(df,day): return pd.DatetimeIndex(df.index).searchsorted(pd.Timestamp(day),side='right')-1

def _fwd(df,p,n):
    if p<0 or p+n>=len(df):return None
    c=df['Close'].to_numpy(float); return float((c[p+n]/c[p]-1)*100)


def update_prospective():
    hist=os.path.join(HERE,'v62_history')
    if not os.path.isdir(hist):return
    snaps=[]
    for fn in sorted(os.listdir(hist)):
        if fn.endswith('.json'):
            try:snaps.append(json.load(open(os.path.join(hist,fn))))
            except Exception:pass
    signals=[]; seen=set()
    for s in snaps:
        for r in s.get('signals',[]):
            key=(r.get('ticker'),r.get('bar_date'),r.get('candidate'))
            if key in seen:continue
            seen.add(key); signals.append(r)
    if not signals:
        out={'generated':datetime.now(timezone.utc).isoformat(timespec='seconds'),'signals':[],'summary':{}}
        json.dump(out,open(os.path.join(HERE,'v62_prospective.json'),'w'),separators=(',',':'));return

    tickers=sorted({r['ticker'] for r in signals}|{MID,SMALL}); frames=fetch(tickers,period='1y')
    mid=frames.get(MID); small=frames.get(SMALL); evaluated=[]
    for r in signals:
        d=frames.get(r['ticker']); x=dict(r); x['outcomes']={}
        if d is not None and mid is not None and small is not None:
            p=_pos(d,r['bar_date']); mp=_pos(mid,r['bar_date']); sp=_pos(small,r['bar_date'])
            for h in HORIZONS:
                rr=_fwd(d,p,h); mr=_fwd(mid,mp,h); sr=_fwd(small,sp,h)
                x['outcomes'][str(h)]={'return':None if rr is None else round(rr,2),
                  'alpha_mid150':None if rr is None or mr is None else round(rr-mr,2),
                  'alpha_small250':None if rr is None or sr is None else round(rr-sr,2)}
        evaluated.append(x)

    summary={}
    for label in ('R2 High Conviction','R5 Candidate','All R5'):
        rr=[x for x in evaluated if (label=='All R5' or x['candidate']==label)]; summary[label]={}
        for h in HORIZONS:
            q=[x['outcomes'].get(str(h),{}) for x in rr]
            q=[z for z in q if z.get('return') is not None and z.get('alpha_mid150') is not None and z.get('alpha_small250') is not None]
            if not q:summary[label][str(h)]={'n':0};continue
            a=np.array([z['return'] for z in q]); am=np.array([z['alpha_mid150'] for z in q]); ass=np.array([z['alpha_small250'] for z in q])
            summary[label][str(h)]={'n':len(q),'median_return':round(float(np.median(a)),2),
              'median_alpha_mid150':round(float(np.median(am)),2),'median_alpha_small250':round(float(np.median(ass)),2),
              'beat_either_rate':round(float(np.mean((am>0)|(ass>0))*100),1)}
    out={'generated':datetime.now(timezone.utc).isoformat(timespec='seconds'),'method':'Immutable first-seen R2/R5 signals; forward 20/40/60 trading-session returns vs Midcap150 and Smallcap250 ETF proxies.','signals':evaluated,'summary':summary}
    with open(os.path.join(HERE,'v62_prospective.json'),'w') as f:json.dump(out,f,separators=(',',':'))


def main():
    p=build_live(); update_prospective()
    print(f"V6.2 live: market={p['market_regime']} R2={p['r2_count']} R5-total={p['r5_total_count']} near={p['near_count']}",flush=True)

if __name__=='__main__':main()
