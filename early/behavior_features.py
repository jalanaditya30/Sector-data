#!/usr/bin/env python3
"""Behavioral Market Lab — point-in-time OHLCV behavioral proxies.

These are hypotheses, not claims about investor intent. Each feature maps observable
market behavior to a behavioral-finance concept and is kept separate so it can be
tested before any composite score is created.
"""
import numpy as np
EPS=1e-12

def _ret(c,n): return None if len(c)<=n else float((c[-1]/c[-1-n]-1)*100)
def _med(x): return float(np.median(x)) if len(x) else None

def features(df):
    if df is None or len(df)<145: return None
    c=df['Close'].to_numpy(float); o=df['Open'].to_numpy(float); h=df['High'].to_numpy(float); l=df['Low'].to_numpy(float); v=df['Volume'].to_numpy(float)
    turn=c*v; base=_med(turn[-70:-10]); recent=turn[-10:]; d=np.diff(c)
    persistence=float(np.mean(recent>base))
    turnover_change=_med(recent)/max(base,EPS)

    # Acceptance: are participants repeatedly willing to transact/close at higher prices?
    higher_closes=float(np.mean(np.diff(c[-11:])>0))
    close_location=np.clip((c[-10:]-l[-10:])/np.maximum(h[-10:]-l[-10:],EPS),0,1)
    close_strength=float(np.mean(close_location))
    rising_acceptance=float(np.polyfit(np.arange(10),c[-10:]/c[-10],1)[0]*100)

    # Control: on high-participation days, do buyers or sellers control the close?
    active=recent>base
    buyer_control=float(np.mean(close_location[active])) if active.any() else None
    seller_control=None if buyer_control is None else 1-buyer_control

    # Absorption: unusually high capital traded with restrained price response.
    daily=np.diff(c[-11:])/c[-11:-1]*100
    active_count=int(active.sum())
    absorption=float(np.mean((recent/base)/np.maximum(np.abs(daily),1.0))) if base>0 else None

    # Pullback resilience: downside is shallow relative to upside progress over 20D.
    r20=_ret(c,20) or 0.0
    peak=np.maximum.accumulate(c[-21:]); dd=(c[-21:]/peak-1)*100
    maxdd=abs(float(np.min(dd)))
    resilience=float(max(r20,0)/max(maxdd,1.0))

    # Anchor test: old 120D high is a behavioral reference price.
    prior120=float(np.max(c[-121:-1])); from_high120=float((c[-1]/prior120-1)*100)
    above=np.where(c[-20:]>=prior120)[0]
    anchor_days=int(len(above))
    anchor_acceptance=float(np.mean(c[-10:]>=prior120))

    # Commitment: elevated participation while price acceptance rises.
    commitment=float(persistence*turnover_change*max(rising_acceptance,0))

    # Disagreement: high turnover + wide ranges; lower close-control implies unresolved conflict.
    tr=(h[-10:]-l[-10:])/np.maximum(c[-10:],EPS)*100
    prior_tr=(h[-70:-10]-l[-70:-10])/np.maximum(c[-70:-10],EPS)*100
    range_expansion=_med(tr)/max(_med(prior_tr),EPS)
    disagreement=float(turnover_change*range_expansion*(1-abs(close_strength-.5)*2))

    # Event/crowding proxies. These are deliberately descriptive, not penalties yet.
    event20=float(np.max(np.abs(np.diff(c[-21:])/c[-21:-1]))*100)
    r5=_ret(c,5); r10=_ret(c,10); r60=_ret(c,60)
    crowding=float(max(r5 or 0,0)+0.35*max(turnover_change-2.5,0)*10+0.25*event20)
    return {
      'turnover_persistence':persistence,'turnover_change':turnover_change,
      'higher_close_rate':higher_closes,'close_strength':close_strength,'rising_acceptance':rising_acceptance,
      'buyer_control':buyer_control,'seller_control':seller_control,'absorption':absorption,
      'pullback_resilience':resilience,'from_high120':from_high120,'anchor_days20':anchor_days,
      'anchor_acceptance10':anchor_acceptance,'commitment':commitment,'range_expansion':range_expansion,
      'disagreement':disagreement,'crowding_proxy':crowding,'event20':event20,
      'turnover_cr':_med(recent)/1e7,'r5':r5,'r10':r10,'r20':r20,'r60':r60,
    }
