#!/usr/bin/env python3
"""Early Momentum Radar V5 candidate: Emerging Relative Leadership.

V5 is deliberately cross-sectional. It does not ask only whether a stock is
bullish; it asks whether the stock is becoming a leader faster than its peers,
while participation is persistent and extension is still controlled.

This module is research-only until the V5 walk-forward lab passes.
"""

def score(r, x):
    """Return (score, components, reasons).

    x contains point-in-time percentile ranks (0..100) and context built by the
    backtest/live engine. No future data should enter x.
    """
    p5=x.get('p5',50); p20=x.get('p20',50); p60=x.get('p60',50)
    sp20=x.get('sector_p20',50); sp60=x.get('sector_p60',50)
    tp=x.get('turnover_p',50); ep=x.get('eff_p',50)
    persistence=x.get('persistence',0.0)
    breadth=x.get('breadth20',0.0); sb=x.get('sector_breadth20',0.0)
    turn=r.get('turnover_cr') or 0; rv=r.get('rvol') or 0
    r5=r.get('r5') or 0; r20=r.get('r20') or 0; r60=r.get('r60') or 0
    fh=r.get('from_high60')

    # Rising leadership is the core V5 idea: medium-term rank is not already
    # extreme, while shorter-horizon ranks are improving materially.
    emergence=max(0.0, (p20-p60)*0.55 + (p5-p20)*0.45)
    leadership=0.45*p20 + 0.25*p5 + 0.30*sp20
    sector_transition=max(0.0, sp20-sp60)
    quality=0.55*ep + 45.0*persistence

    # Participation is confirmation, deliberately capped.
    participation=min(100.0, 0.55*tp + 45.0*max(0.0,min(1.0,(rv-0.8)/2.2)))

    # Regime is a gate/modifier, not the main source of points.
    regime=1.0
    if breadth < .40 or sb < .42: regime=.72
    elif breadth < .48 or sb < .50: regime=.88
    elif breadth >= .56 and sb >= .58: regime=1.05

    raw=(0.34*leadership + 0.24*min(100,emergence*3.0) +
         0.13*min(100,sector_transition*2.5) + 0.17*quality +
         0.12*participation)

    penalty=0.0; reasons=[]
    if r.get('event'): penalty+=25; reasons.append('event')
    if r.get('stale'): penalty+=25; reasons.append('stale')
    if turn < .25: penalty+=18; reasons.append('illiquid')
    elif turn < .5: penalty+=7
    if r20 > 28 or r5 > 14: penalty+=16; reasons.append('extended_return')
    if r60 > 55 and emergence < 8: penalty+=10; reasons.append('mature_leader')
    if fh is not None and fh > 5: penalty+=8
    if rv > 7: penalty+=8; reasons.append('turnover_spike')
    if p60 >= 92 and p20 <= p60: penalty+=8; reasons.append('leadership_not_accelerating')

    final=max(0.0,min(100.0,raw*regime-penalty))
    if p20>=70: reasons.append('universe_leader')
    if sp20>=70: reasons.append('sector_leader')
    if emergence>=8: reasons.append('leadership_emerging')
    if persistence>=.6: reasons.append('persistent_participation')

    return round(final,2), {
        'leadership':round(leadership,1),'emergence':round(emergence,1),
        'sector_transition':round(sector_transition,1),'quality':round(quality,1),
        'participation':round(participation,1),'regime_multiplier':regime,
        'penalty':round(penalty,1)
    }, reasons
