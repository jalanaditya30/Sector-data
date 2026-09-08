#!/usr/bin/env python3
"""V6 point-in-time accumulation / behaviour-change features.

No composite bullish score is defined here.  V6 research starts with raw features
and asks which changes actually preceded benchmark-relative outperformance and
large forward winners.
"""
import numpy as np

EPS=1e-12

def _ret(c,n):
    return None if len(c)<=n else float((c[-1]/c[-1-n]-1)*100)

def _atr_frac(h,l,c,start,end):
    vals=[]
    for i in range(max(1,start),end):
        tr=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
        vals.append(tr/max(c[i],EPS))
    return float(np.median(vals)) if vals else None

def features(df, benchmark_returns=None):
    """Return raw point-in-time V6 features using only rows in df up to today."""
    if df is None or len(df)<145: return None
    c=df['Close'].to_numpy(float); h=df['High'].to_numpy(float); l=df['Low'].to_numpy(float); v=df['Volume'].to_numpy(float)
    turn=c*v

    recent10=float(np.median(turn[-10:])); base60=float(np.median(turn[-70:-10]))
    turnover_change=recent10/max(base60,EPS)
    persistence=float(np.mean(turn[-10:] > base60))

    # Directional participation: capital on positive sessions versus negative sessions.
    d=np.diff(c[-16:]); tv=turn[-15:]
    up=float(np.sum(tv[d>0])); down=float(np.sum(tv[d<0]))
    up_down_turnover=up/max(down,EPS)

    # Absorption proxy: participation expands but price response stays controlled.
    r15=_ret(c,15) or 0.0
    participation_price_ratio=turnover_change/max(abs(r15),2.0)

    # Volatility/range compression versus the stock's own earlier behaviour.
    recent_atr=_atr_frac(h,l,c,len(c)-20,len(c))
    prior_atr=_atr_frac(h,l,c,len(c)-80,len(c)-20)
    atr_compression=None if recent_atr is None or prior_atr is None else recent_atr/max(prior_atr,EPS)
    recent_range=(float(np.max(h[-20:]))-float(np.min(l[-20:])))/max(c[-1],EPS)
    prior_range=(float(np.max(h[-80:-20]))-float(np.min(l[-80:-20])))/max(c[-21],EPS)
    range_compression=recent_range/max(prior_range,EPS)

    # Accumulation consistency: positive-close sessions with above-baseline turnover.
    base_daily=np.median(turn[-70:-10])
    acc_days=0
    for i in range(len(c)-10,len(c)):
        if c[i]>c[i-1] and turn[i]>base_daily: acc_days+=1
    accumulation_days=acc_days/10.0

    r5=_ret(c,5); r10=_ret(c,10); r20=_ret(c,20); r60=_ret(c,60)
    br=benchmark_returns or {}
    a10=None if r10 is None or br.get(10) is None else r10-br[10]
    a20=None if r20 is None or br.get(20) is None else r20-br[20]
    a60=None if r60 is None or br.get(60) is None else r60-br[60]
    # Has relative strength improved recently versus its medium-term pace?
    rs_accel=None if a10 is None or a60 is None else a10-(a60/6.0)
    rs_accel20=None if a20 is None or a60 is None else a20-(a60/3.0)

    prior120=float(np.max(c[-121:-1])); from_high120=(c[-1]/prior120-1)*100
    extension20=(r20 or 0.0)
    event20=float(np.max(np.abs(np.diff(c[-21:])/c[-21:-1]))*100)

    return {
      'turnover_change':turnover_change,'turnover_persistence':persistence,
      'up_down_turnover':up_down_turnover,'participation_price_ratio':participation_price_ratio,
      'atr_compression':atr_compression,'range_compression':range_compression,
      'accumulation_days':accumulation_days,'rs_accel10':rs_accel,'rs_accel20':rs_accel20,
      'r5':r5,'r10':r10,'r20':r20,'r60':r60,'from_high120':from_high120,
      'event20':event20,'turnover_cr':recent10/1e7,
      # Descriptive readiness flag only; NOT a score.
      'quiet_participation': bool(turnover_change>=1.35 and persistence>=0.6 and -5<=r15<=10 and event20<12),
      'compression': bool(atr_compression is not None and atr_compression<=0.9 and range_compression<=0.9),
      'near_structure': bool(-15<=from_high120<=2),
    }
