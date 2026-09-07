#!/usr/bin/env python3
"""Early Momentum Radar V4 candidate.

Design goal: find emerging mid/small-cap leaders, not Nifty-50 beaters.
V4 is NOT wired to the live board. It must pass the 3-year holdout lab first.

Core change from V2/V3:
market regime -> sector regime -> stock leadership -> trend quality -> participation.
OBV/turnover are confirmations, not dominant additive drivers.
"""


def classify(r, ctx):
    """Return (stage, score, reasons).

    ctx fields are constructed only from data available on the signal date:
      mid20/small20, breadth20, sector_median20, sector_breadth20,
      target_rs20 (stock return minus stronger of Midcap150/Smallcap250 20D).
    """
    reasons=[]
    r5=r.get("r5"); r20=r.get("r20"); r60=r.get("r60")
    rv=r.get("rvol"); turn=r.get("turnover_cr")
    eff=r.get("eff20"); fh=r.get("from_high60")
    obv_slope=r.get("obv_slope")

    late=(r20 is not None and r20>32) or (r5 is not None and r5>15) or (fh is not None and fh>6)
    if r.get("event") or late:
        return "Late / Event", -2.0, ["late_or_event"]
    if r.get("stale"):
        return "Review", -2.0, ["stale"]

    breadth=ctx.get("breadth20",0)
    mid20=ctx.get("mid20",0); small20=ctx.get("small20",0)
    sec_med=ctx.get("sector_median20",0); sec_breadth=ctx.get("sector_breadth20",0)
    target_rs=ctx.get("target_rs20")

    # Evidence families. These are intentionally broader and less correlated than V3.
    market_ok=(breadth>=0.48 and max(mid20,small20)>-4)
    market_strong=(breadth>=0.56 and max(mid20,small20)>0)
    sector_ok=(sec_breadth>=0.50 and sec_med>0)
    sector_strong=(sec_breadth>=0.60 and sec_med>2)
    leadership=(target_rs is not None and target_rs>1.0)
    strong_leadership=(target_rs is not None and target_rs>3.0)
    trend_ok=(eff is not None and eff>=0.30 and r20 is not None and r20>0)
    trend_strong=(eff is not None and eff>=0.45 and r20 is not None and r20>0)
    early_price=(r20 is not None and 3<=r20<=18 and r5 is not None and 0<r5<=9)
    continuation=(r60 is not None and r60>-8 and r20 is not None and r20>3)
    participation=(rv is not None and 1.10<=rv<=4.0)
    accumulation=(obv_slope is not None and obv_slope>0)
    location_ok=(fh is not None and -12<=fh<=3)
    liquid=(turn is None or turn>=0.5)

    checks={
        "market_ok":market_ok,"market_strong":market_strong,
        "sector_ok":sector_ok,"sector_strong":sector_strong,
        "leadership":leadership,"strong_leadership":strong_leadership,
        "trend_ok":trend_ok,"trend_strong":trend_strong,
        "early_price":early_price,"continuation":continuation,
        "participation":participation,"accumulation":accumulation,
        "location_ok":location_ok,"liquid":liquid,
    }
    reasons=[k for k,v in checks.items() if v]

    # V4 weights: regime/leadership/trend dominate; volume/OBV only confirm.
    score=0.0
    if market_ok: score+=1.25
    if market_strong: score+=0.75
    if sector_ok: score+=1.50
    if sector_strong: score+=0.50
    if leadership: score+=1.75
    if strong_leadership: score+=0.75
    if trend_ok: score+=1.50
    if trend_strong: score+=0.50
    if early_price: score+=1.25
    if continuation: score+=0.75
    if participation: score+=0.50
    if accumulation: score+=0.35
    if location_ok: score+=0.50
    if turn is not None:
        if turn<0.25: score-=2.0
        elif turn<0.5: score-=0.75
        elif turn>=2: score+=0.25
    if rv is not None and rv>6: score-=0.75
    if r60 is not None and r60<-20: score-=1.0

    # Hard lifecycle gates. Discovery is early but already showing trend quality.
    if (liquid and market_ok and sector_ok and leadership and trend_ok and
        early_price and continuation and location_ok):
        stage="Discovery"; score+=1.0
    elif (liquid and market_ok and sector_ok and leadership and trend_ok and
          r20 is not None and 5<=r20<=25 and r5 is not None and 0<r5<=12 and continuation):
        stage="Confirming"; score+=0.6
    elif (market_ok and sector_ok and leadership and trend_ok and continuation):
        stage="Watch"; score+=0.1
    else:
        stage="No setup"; score-=0.5

    return stage, round(score,2), reasons
