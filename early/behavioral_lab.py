#!/usr/bin/env python3
"""Behavioral Market Lab v1.

Research layer, NOT a buy/sell model. Translate observable market behaviour into
independent evidence families without inventing an opaque composite score.

Lifecycle hypothesis:
IGNORED -> BEHAVIOUR CHANGE -> ACCUMULATION -> ACCEPTANCE -> RECOGNITION -> FOMO -> EXHAUSTION

Uses only OHLCV + cross-sectional sector/market context initially. Social/news,
delivery and fundamentals should be added later as independent datasets and
validated prospectively rather than retrofitted into this model.
"""
import json, os, sys
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import yfinance as yf

HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
sys.path.insert(0,ROOT)
import stock_registry as registry

BATCH=35; PERIOD='1y'; MIN_ROWS=150
MID='MID150BEES.NS'; SMALL='HDFCSML250.NS'

def ret(c,n): return float((c[-1]/c[-1-n]-1)*100) if len(c)>n else None

def fetch(tickers):
    out={}
    for i in range(0,len(tickers),BATCH):
        chunk=tickers[i:i+BATCH]
        print(f'fetch {i+1}-{i+len(chunk)}/{len(tickers)}',flush=True)
        try: raw=yf.download(chunk,period=PERIOD,auto_adjust=True,group_by='ticker',progress=False,threads=True)
        except Exception: continue
        if raw is None or raw.empty: continue
        for t in chunk:
            try:
                d=(raw[t] if isinstance(raw.columns,pd.MultiIndex) else raw).dropna(subset=['Close'])
                if len(d): out[t]=d
            except Exception: pass
    return out

def metrics(df):
    if df is None or len(df)<MIN_ROWS or 'Volume' not in df: return None
    c=df.Close.to_numpy(float); h=df.High.to_numpy(float); l=df.Low.to_numpy(float); v=df.Volume.fillna(0).to_numpy(float)
    if np.any(c[-121:]<=0): return None
    tv=c*v; base=float(np.median(tv[-70:-10])); recent=float(np.median(tv[-10:]));
    if base<=0:return None
    persist=float(np.mean(tv[-10:]>base)); expansion=recent/base
    prior120=float(np.max(c[-121:-1])); loc=(c[-1]/prior120-1)*100
    r5,r10,r20,r60=[ret(c,n) for n in (5,10,20,60)]
    # Acceptance: buyers repeatedly pay up and price holds those higher levels.
    last10=c[-10:]; higher_closes=float(np.mean(np.diff(last10)>0));
    closes_near_high=[]
    for j in range(len(c)-10,len(c)):
        rng=h[j]-l[j]; closes_near_high.append((c[j]-l[j])/rng if rng>0 else .5)
    close_location=float(np.mean(closes_near_high))
    # Supply response / absorption: high-turnover days whose downside is limited.
    d=np.diff(c[-11:])/c[-11:-1]*100; relturn=tv[-10:]/base
    heavy=relturn>=1.25
    absorption=float(np.mean((d>=-1.0)[heavy])) if np.any(heavy) else 0.0
    # Control: capital on up days vs down days and whether high-volume days close well.
    up=float(np.sum(tv[-10:][d>0])); down=float(np.sum(tv[-10:][d<0])); ud=up/max(down,1e-12)
    hv_close=float(np.mean(np.array(closes_near_high)[heavy])) if np.any(heavy) else close_location
    # Urgency/crowding proxies. Extreme expansion + fast return + large daily event = later behaviour.
    event=float(np.max(np.abs(np.diff(c[-21:])/c[-21:-1]))*100)
    extension=max(r5 or 0,0)+max((r20 or 0)/4,0)
    # Stability / willingness to defend: shallow pullback from recent 20D high.
    high20=float(np.max(c[-20:])); draw_from20=(c[-1]/high20-1)*100
    turnover_cr=recent/1e7
    return dict(last=float(c[-1]),r5=r5,r10=r10,r20=r20,r60=r60,turnover_expansion=expansion,
      persistence=persist,from_high120=loc,higher_close_rate=higher_closes,close_location=close_location,
      absorption=absorption,up_down_capital=ud,high_volume_close_location=hv_close,event20=event,
      extension_proxy=extension,draw_from_high20=draw_from20,turnover_cr=turnover_cr)

def classify(x,sector_ret,sector_breadth,market):
    participation=x['persistence']>=.6 and 1.3<=x['turnover_expansion']<=3.5
    strong_part=x['persistence']>=.8 and 1.7<=x['turnover_expansion']<=2.5
    acceptance=x['close_location']>=.58 and x['higher_close_rate']>=.44 and x['draw_from_high20']>=-7
    buyer_control=x['up_down_capital']>=1.15 and x['high_volume_close_location']>=.55
    absorption=x['absorption']>=.65 and x['turnover_expansion']>=1.3
    structure=-7<=x['from_high120']<=5
    sector=sector_ret is not None and sector_ret>2 and sector_breadth>=.55
    crowded=x['turnover_expansion']>4 or x['r5']>12 or x['event20']>15 or x['from_high120']>10
    evidence={
      'participation_change':participation,'persistent_commitment':strong_part,
      'higher_price_acceptance':acceptance,'buyer_control':buyer_control,
      'seller_absorption':absorption,'near_major_anchor':structure,'sector_social_proof':sector,
      'supportive_risk_regime':market=='bull','crowding_warning':crowded}
    positives=sum(evidence[k] for k in evidence if k!='crowding_warning')
    if crowded and positives>=4: stage='FOMO / Crowded'
    elif positives>=7 and structure: stage='Recognition'
    elif positives>=5 and structure: stage='Acceptance'
    elif positives>=4 and participation: stage='Accumulation'
    elif positives>=2 and participation: stage='Behaviour Change'
    else: stage='Ignored / Unclear'
    return evidence,stage,positives

def main():
    universe=registry.read_universe(os.path.join(ROOT,'trend','universe.txt')); reg=registry.load(); meta=registry.by_yahoo(reg)
    frames=fetch(universe+[MID,SMALL]); mid=frames.get(MID); small=frames.get(SMALL)
    market='unknown'
    if mid is not None and small is not None:
        mc=mid.Close.to_numpy(float); sc=small.Close.to_numpy(float)
        vals=[ret(mc,20),ret(mc,60),ret(sc,20),ret(sc,60)]
        market='bull' if all(v is not None and v>0 for v in vals) else ('bear' if vals[0]<0 and vals[2]<0 and (vals[1]<0 or vals[3]<0) else 'mixed')
    raw=[]
    for t in universe:
        x=metrics(frames.get(t));
        if not x: continue
        m=meta.get(t,{})
        raw.append({'symbol':m.get('nse') or t.replace('.NS',''),'name':m.get('name') or t,'sector':m.get('industry_group') or 'Unclassified','yahoo':t,**x})
    groups={}
    for r in raw: groups.setdefault(r['sector'],[]).append(r)
    sector_stats={}
    for s,rr in groups.items():
        vals=[r['r20'] for r in rr if r['r20'] is not None]
        sector_stats[s]={'ret20':float(np.median(vals)) if vals else None,'breadth':float(np.mean([v>0 for v in vals])) if vals else 0,'count':len(rr)}
    rows=[]
    for r in raw:
        ss=sector_stats[r['sector']]; ev,stage,n=classify(r,ss['ret20'],ss['breadth'],market)
        r.update(evidence=ev,behavior_stage=stage,evidence_count=n,sector_ret20=ss['ret20'],sector_breadth=ss['breadth'])
        # Research priority only: count confirmations, never presented as probability/expected return.
        rows.append(r)
    order={'Behaviour Change':0,'Accumulation':1,'Acceptance':2,'Recognition':3,'FOMO / Crowded':4,'Ignored / Unclear':5}
    rows.sort(key=lambda r:(order.get(r['behavior_stage'],9),-r['evidence_count'],-r['turnover_cr']))
    payload={'generated':datetime.now(timezone.utc).isoformat(),'model':'Behavioral Market Lab v1','research_only':True,
      'market_regime':market,'requested':len(universe),'resolved':len(raw),'method':'Independent behavioral evidence; no predictive composite score.',
      'lifecycle':['Ignored / Unclear','Behaviour Change','Accumulation','Acceptance','Recognition','FOMO / Crowded'],
      'definitions':{'participation':'persistent capital arrival','acceptance':'willingness to transact and close at higher prices','control':'which side wins high-participation sessions','absorption':'heavy turnover without meaningful downside','anchor':'behaviour around prior 120D high','social_proof':'sector breadth/strength','crowding':'urgency/extreme participation/extension'},
      'rows':rows,'sectors':sector_stats}
    with open(os.path.join(HERE,'behavioral.json'),'w') as f: json.dump(payload,f,separators=(',',':'))
    print('resolved',len(raw),'market',market)
    from collections import Counter; print(Counter(r['behavior_stage'] for r in rows))
if __name__=='__main__': main()
