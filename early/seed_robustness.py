#!/usr/bin/env python3
"""R2 nuisance-seed robustness test.

This does NOT advance to Section 2. It asks a narrower question: does R2 beat a
sector + price-location matched random control robustly, or was the prior result
an artifact of hash-based oversubscription?

Changes versus portfolio_sim.py:
- 30 independent hash seeds for each N=10/20/40.
- Position size is current portfolio equity / N at each entry; no isolated sleeves.
- Random controls are matched on signal day, sector, AND from_high120 bucket.
- Same next-session-open entry, 40-session hold, liquidity floor and costs.
- Weekly sampling is retained only to diagnose the existing research sample; this
  result is explicitly not claimed to represent daily live cadence.
"""
import hashlib, json, math, os, sys
from collections import defaultdict
from datetime import datetime, timezone
import numpy as np
import pandas as pd

HERE=os.path.dirname(os.path.abspath(__file__))
ROOT=os.path.dirname(HERE)
sys.path.insert(0,HERE); sys.path.insert(0,ROOT)
import portfolio_sim as base

SEEDS=list(range(1,31))
CAPS=(10,20,40)
CONTROL_REPS=1000
CONTROL_SEED=862026
OUT=os.path.join(HERE,'seed_robustness_result.json')


def loc_bucket(x):
    if x is None:return 'missing'
    if x < -10:return '<-10'
    if x < -5:return '-10:-5'
    if x < 0:return '-5:0'
    if x <= 5:return '0:5'
    if x <= 10:return '5:10'
    return '>10'


def order_key(day,ticker,seed):
    return hashlib.sha256(f'{day}|{ticker}|R2|{seed}'.encode()).hexdigest()


def make_schedule_seed(day_signals,calendar,seed):
    sch=base.make_schedule(day_signals,calendar)
    for day,rows in sch.items():
        sch[day]=sorted(rows,key=lambda r:order_key(r['signal_date'],r['ticker'],seed))
    return sch


def mark_value(pos,frames,day,col='Close'):
    px=base.price_on(frames.get(pos['ticker']),day,col)
    if px is None:return pos['value']
    return pos['units']*px


def simulate_rebalanced(schedule,frames,calendar,n_slots):
    cash=1.0; positions=[]; curve=[]; concurrent=[]; traded=0.0; entries=exits=0
    for day in calendar:
        ds=str(pd.Timestamp(day).date())

        # Mark existing positions to today's open for sizing new entries.
        open_values=[]
        for p in positions:
            op=base.price_on(frames.get(p['ticker']),day,'Open')
            if op is None: op=p['last_px']
            open_values.append(p['units']*op)
        equity_open=cash+sum(open_values)

        # Enter new names at open, each targeted at current total equity/N.
        incoming=schedule.get(ds,[])
        held={p['ticker'] for p in positions}
        incoming=[r for r in incoming if r['ticker'] not in held]
        free=max(0,n_slots-len(positions))
        for r in incoming[:free]:
            op=base.price_on(frames.get(r['ticker']),day,'Open')
            if op is None: continue
            target=min(equity_open/n_slots,cash)
            if target<=0: break
            entry_cost=r['cost_rate']/2.0
            gross=target/(1.0+entry_cost)
            fee=target-gross
            units=gross/op
            cash-=target; traded+=gross; entries+=1
            positions.append({'ticker':r['ticker'],'units':units,'age':0,'last_px':op,
                              'cost_rate':r['cost_rate'],'sector':r['sector']})

        # Mark all positions to close, then age/exit at close after 40 sessions.
        keep=[]
        for p in positions:
            cp=base.price_on(frames.get(p['ticker']),day,'Close')
            if cp is None: cp=p['last_px']
            p['last_px']=cp; p['age']+=1
            value=p['units']*cp
            if p['age']>=base.HOLD:
                fee=value*(p['cost_rate']/2.0)
                cash+=value-fee; traded+=value; exits+=1
            else: keep.append(p)
        positions=keep
        eq=cash+sum(p['units']*p['last_px'] for p in positions)
        curve.append((ds,float(eq))); concurrent.append(len(positions))

    eq=np.array([v for _,v in curve],float); rr=np.zeros_like(eq); rr[1:]=eq[1:]/eq[:-1]-1
    years=max((len(eq)-1)/252.0,1/252); peak=np.maximum.accumulate(eq)
    sd=float(np.std(rr[1:],ddof=1)) if len(rr)>2 else 0.0
    return {'terminal_wealth':float(eq[-1]),'cagr':float(eq[-1]**(1/years)-1),
            'max_drawdown':float(np.min(eq/peak-1)),
            'sharpe':float(np.mean(rr[1:])/sd*math.sqrt(252)) if sd>0 else 0.0,
            'average_concurrent_positions':float(np.mean(concurrent)),
            'pct_days_fully_invested':float(np.mean(np.array(concurrent)==n_slots)),
            'annualized_turnover':float(traded/max(float(np.mean(eq)),1e-12)/years),
            'entries':entries,'exits':exits}


def matched_random_days(day_all,day_r2,rng):
    out={}
    for day,sigs in day_r2.items():
        if not sigs: out[day]=[]; continue
        need=defaultdict(int)
        for s in sigs: need[(s['sector'],loc_bucket(s['f']['from_high120']))]+=1
        pools=defaultdict(list)
        for r in day_all.get(day,[]): pools[(r['sector'],loc_bucket(r['f']['from_high120']))].append(r)
        picks=[]
        for key,k in need.items():
            pool=pools.get(key,[])
            if not pool: continue
            take=min(k,len(pool)); idx=rng.choice(len(pool),size=take,replace=False)
            picks.extend(pool[int(i)] for i in np.atleast_1d(idx))
        out[day]=picks
    return out


def diststats(a):
    a=np.asarray(a,float)
    return {'n':len(a),'mean':float(np.mean(a)),'median':float(np.median(a)),
            'p05':float(np.quantile(a,.05)),'p25':float(np.quantile(a,.25)),
            'p75':float(np.quantile(a,.75)),'p95':float(np.quantile(a,.95)),
            'min':float(np.min(a)),'max':float(np.max(a)),'std':float(np.std(a,ddof=1))}


def main():
    reg=base.registry.load(); meta=base.registry.by_yahoo(reg)
    universe=base.registry.read_universe(os.path.join(ROOT,'trend','universe.txt'))
    frames=base.fetch(universe+[base.MID]+base.SMALL_CANDIDATES)
    mid=frames.get(base.MID)
    if mid is None: raise RuntimeError('Midcap benchmark unavailable')
    small_sym,small=base.choose_small(frames)
    scored,sample,day_all,day_r2=base.build_signal_panel(frames,universe,meta,mid,small)
    calendar=pd.DatetimeIndex(mid.index).intersection(pd.DatetimeIndex(small.index)).sort_values()
    first=pd.Timestamp(scored[0]); last=pd.Timestamp(scored[-1]); end_i=calendar.searchsorted(last,side='right')+base.HOLD
    simcal=calendar[(calendar>=first)][:max(1,end_i-calendar.searchsorted(first))]

    # Seed distribution: only oversubscription ordering changes.
    strategy={}
    for n in CAPS:
        vals=[]; detail=[]
        for seed in SEEDS:
            m=simulate_rebalanced(make_schedule_seed(day_r2,simcal,seed),frames,simcal,n)
            vals.append(m['terminal_wealth']); detail.append({'seed':seed,**m})
        strategy[str(n)]={'distribution':diststats(vals),'runs':detail}

    # Matched control distribution. Seed used only for random drawing; each draw then uses fixed neutral ordering.
    rng=np.random.default_rng(CONTROL_SEED); controls={str(n):[] for n in CAPS}
    for rep in range(CONTROL_REPS):
        if rep%100==0: print(f'control {rep}/{CONTROL_REPS}',flush=True)
        rd=matched_random_days(day_all,day_r2,rng)
        for n in CAPS:
            sch=make_schedule_seed(rd,simcal,seed=0)
            controls[str(n)].append(simulate_rebalanced(sch,frames,simcal,n)['terminal_wealth'])

    result={}
    for n in CAPS:
        k=str(n); sd=np.array([x['terminal_wealth'] for x in strategy[k]['runs']]); cd=np.array(controls[k])
        # Pairwise probability is easier to interpret than selecting one nuisance seed.
        pair=float(np.mean(sd[:,None]>cd[None,:]))
        result[k]={'r2_seed_distribution':strategy[k]['distribution'],
                   'control_distribution':diststats(cd),
                   'prob_r2_seed_beats_random_control':pair,
                   'r2_median_percentile_in_control':float(np.mean(cd<np.median(sd))*100),
                   'r2_runs':strategy[k]['runs']}

    payload={'generated':datetime.now(timezone.utc).isoformat(timespec='seconds'),
      'test':'R2 seed robustness: 30 nuisance seeds vs 1000 sector+location matched controls',
      'sampling_note':'Weekly sample retained to diagnose existing research only; not a daily live-cadence test.',
      'capital_model':'Each new entry sized at current total portfolio equity / N; cash residual earns 0%.',
      'control_match':'signal day + sector + from_high120 bucket; same liquidity floor and costs.',
      'seeds':SEEDS,'caps':list(CAPS),'control_repetitions':CONTROL_REPS,
      'signal_days':sum(bool(v) for v in day_r2.values()),'raw_signals':sum(len(v) for v in day_r2.values()),
      'results':result}
    with open(OUT,'w') as f: json.dump(payload,f,separators=(',',':'))
    print(json.dumps({n:{k:v for k,v in result[n].items() if k!='r2_runs'} for n in result},indent=2),flush=True)

if __name__=='__main__': main()
