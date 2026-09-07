#!/usr/bin/env python3
"""V5 3-year cross-sectional walk-forward lab.

Tests DAILY RANKED portfolios (top 5/10/20/30), not every qualifying signal.
Primary benchmarks: Midcap 150 and Smallcap 250 ETF proxies.
Uses point-in-time cross-sectional percentiles and a 20-session re-entry gap.
"""
import json, os, sys
from collections import defaultdict
from datetime import datetime, timezone
import numpy as np, pandas as pd, yfinance as yf
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
sys.path.insert(0,HERE); sys.path.insert(0,ROOT)
import early_scan as es, model_v5, stock_registry as registry
BATCH=35; PERIOD='5y'; H=[5,10,20,40]; TEST_DAYS=756; TOPS=[5,10,20,30]
MID=['MID150BEES.NS']; SMALL=['HDFCSML250.NS','MOSMALL250.NS']

def fetch(ts):
 out={}
 for i in range(0,len(ts),BATCH):
  ch=ts[i:i+BATCH]; print(f'fetch {i+1}-{i+len(ch)}/{len(ts)}',flush=True)
  z=yf.download(ch,period=PERIOD,interval='1d',group_by='ticker',auto_adjust=True,progress=False,threads=True)
  if z is None or z.empty: continue
  for t in ch:
   try:
    d=z[t] if isinstance(z.columns,pd.MultiIndex) else z; d=d.dropna(subset=['Close'])
    if not d.empty: out[t]=d
   except: pass
 return out

def proxy(c):
 d=fetch(c); v=[(t,x) for t,x in d.items() if len(x)>=500] or list(d.items())
 return max(v,key=lambda q:len(q[1])) if v else (None,None)
def pos(d,day): return pd.DatetimeIndex(d.index).searchsorted(day,side='right')-1
def ret(d,p,n):
 if p<n:return None
 c=d['Close'].to_numpy(float); return (c[p]/c[p-n]-1)*100
def fwd(d,p,n):
 c=d['Close'].to_numpy(float); return None if p+n>=len(c) else (c[p+n]/c[p]-1)*100
def pct(vals,v):
 a=np.asarray([x for x in vals if x is not None and np.isfinite(x)],float)
 return 50.0 if len(a)<10 or v is None else float((a<=v).mean()*100)
def persistence(d,p):
 if p<25:return 0
 c=d['Close'].to_numpy(float); vol=d['Volume'].to_numpy(float); ok=0
 for j in range(p-9,p+1):
  base=np.median(vol[max(0,j-20):j])
  if base>0 and vol[j]>=base and c[j]>=c[j-1]: ok+=1
 return ok/10

def stat(rows,h):
 q=[r for r in rows if r.get(f'ret{h}') is not None]
 if not q:return {'n':0}
 rr=np.array([r[f'ret{h}'] for r in q]); am=np.array([r[f'am{h}'] for r in q]); ass=np.array([r[f'as{h}'] for r in q])
 return {'n':len(q),'median_return':round(float(np.median(rr)),2),'mean_return':round(float(np.mean(rr)),2),'win_rate':round(float((rr>0).mean()*100),1),'median_alpha_mid150':round(float(np.median(am)),2),'median_alpha_small250':round(float(np.median(ass)),2),'beat_mid150_rate':round(float((am>0).mean()*100),1),'beat_small250_rate':round(float((ass>0).mean()*100),1),'beat_either_rate':round(float(((am>0)|(ass>0)).mean()*100),1),'beat_both_rate':round(float(((am>0)&(ass>0)).mean()*100),1)}

def main():
 uni=registry.read_universe(os.path.join(ROOT,'trend','universe.txt')); reg=registry.load(); meta=registry.by_yahoo(reg)
 mid_sym,mid=proxy(MID); sm_sym,sm=proxy(SMALL); frames=fetch(uni)
 common=pd.DatetimeIndex(mid.index).intersection(pd.DatetimeIndex(sm.index)); dates=list(common[-(TEST_DAYS+40):-40])[-TEST_DAYS:]
 if len(dates)<500: raise RuntimeError('insufficient history')
 signals=[]; daily={k:[] for k in TOPS}
 for di,day in enumerate(dates):
  if di%40==0: print(f'walk {di+1}/{len(dates)} {day.date()}',flush=True)
  mp=pos(mid,day); sp=pos(sm,day); rows=[]; pmap={}
  bm={n:ret(mid,mp,n) for n in (5,10,20,60)}
  for t,d in frames.items():
   p=pos(d,day)
   if p<65:continue
   r=es.analyse(t,d.iloc[:p+1],meta.get(t,{}),bm,source_symbol=t)
   if r: rows.append(r); pmap[t]=p
  es.add_sector_and_signals(rows)
  if not rows:continue
  vals={k:[r.get(k) for r in rows] for k in ['r5','r20','r60','eff20','rvol']}
  secs=defaultdict(list)
  for r in rows: secs[r.get('sector')].append(r)
  breadth=sum((r.get('r20') or -999)>0 for r in rows)/len(rows)
  ranked=[]
  for r in rows:
   t=r['requested_yahoo']; d=frames[t]; p=pmap[t]; sr=secs[r.get('sector')] or [r]
   sb=sum((x.get('r20') or -999)>0 for x in sr)/len(sr)
   x={'p5':pct(vals['r5'],r.get('r5')),'p20':pct(vals['r20'],r.get('r20')),'p60':pct(vals['r60'],r.get('r60')),'eff_p':pct(vals['eff20'],r.get('eff20')),'turnover_p':pct(vals['rvol'],r.get('rvol')),'sector_p20':pct([z.get('r20') for z in sr],r.get('r20')),'sector_p60':pct([z.get('r60') for z in sr],r.get('r60')),'persistence':persistence(d,p),'breadth20':breadth,'sector_breadth20':sb}
   sc,comp,why=model_v5.score(r,x); ranked.append((sc,t,r,x,comp,why))
  ranked.sort(reverse=True,key=lambda z:z[0])
  for N in TOPS:
   picks=ranked[:N]; daily[N].append({'date':str(day.date()),'scores':[z[0] for z in picks]})
   for rank,z in enumerate(picks,1):
    sc,t,r,x,comp,why=z; d=frames[t]; p=pmap[t]; rec={'date':str(day.date()),'rank':rank,'top_bucket':N,'ticker':t,'score':sc,'components':comp,'reasons':why}
    for h in H:
     a=fwd(d,p,h); mr=fwd(mid,mp,h); ss=fwd(sm,sp,h); rec[f'ret{h}']=None if a is None else round(a,2); rec[f'am{h}']=None if a is None or mr is None else round(a-mr,2); rec[f'as{h}']=None if a is None or ss is None else round(a-ss,2)
    signals.append(rec)
 # evaluate exact daily top-N by rank, avoiding duplicated top_bucket records
 summary={}
 for N in TOPS:
  rr=[r for r in signals if r['top_bucket']==N]
  summary[str(N)]={str(h):stat(rr,h) for h in H}
 # time thirds reveal regime robustness without tuning thresholds on one slice
 cuts=np.array_split(dates,3); thirds={}
 for i,c in enumerate(cuts,1):
  a=str(c[0].date()); b=str(c[-1].date()); thirds[f'fold{i}']={'start':a,'end':b,'top10':{str(h):stat([r for r in signals if r['top_bucket']==10 and a<=r['date']<=b],h) for h in H}}
 out={'generated':datetime.now(timezone.utc).isoformat(timespec='seconds'),'model':'V5 Emerging Relative Leadership','objective':'Daily top-ranked emerging leaders; beat Nifty Midcap 150 / Nifty Smallcap 250','benchmarks':{'midcap150':mid_sym,'smallcap250':sm_sym},'trading_days':len(dates),'history_resolved':len(frames),'method':'point-in-time cross-sectional percentile ranks; daily top 5/10/20/30; no future data in score construction','limitations':'Current-universe survivorship bias; ETF proxy tracking error; no costs/slippage; sector metadata is current registry metadata.','summary':summary,'folds':thirds,'signal_count':len(signals)}
 path=os.path.join(HERE,'v5_backtest.json'); open(path,'w').write(json.dumps(out,separators=(',',':'))); print('written',path); print('TOP10 40D',summary['10']['40']); print('TOP20 40D',summary['20']['40'])
if __name__=='__main__': main()
