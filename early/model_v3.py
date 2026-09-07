#!/usr/bin/env python3
"""Early Momentum Radar v3 candidate.

This is intentionally NOT wired into the live board yet. It is a stricter quality
gate layered on top of the transparent v2 measurements, designed to test whether
'early + participation + leadership + accumulation' performs better out of sample.

Promotion rule: only replace v2 after the one-year walk-forward validation half
shows better forward alpha / hit-rate with a materially smaller daily shortlist.
"""


def classify(r):
    """Return (stage, score, reasons) for the v3 candidate.

    Uses only fields already computed by early_scan.analyse/add_sector_and_signals,
    so there is no future information and no new data dependency.
    """
    reasons = []

    # Hard reject the structural cases we do not want to call 'early'.
    late = (r.get("r20", 0) > 30 or r.get("r5", 0) > 15 or
            (r.get("from_high60") is not None and r["from_high60"] > 5))
    if r.get("event") or late:
        return "Late / Event", round((r.get("radar_score") or 0) - 1.0, 2), ["late_or_event"]
    if r.get("stale"):
        return "Review", round((r.get("radar_score") or 0) - 1.0, 2), ["stale"]

    r5 = r.get("r5")
    r20 = r.get("r20")
    rv = r.get("rvol")
    turn = r.get("turnover_cr")
    mrs = r.get("market_rs")
    srs = r.get("sector_rs")
    eff = r.get("eff20")
    fh = r.get("from_high60")

    # Core evidence families. We require breadth across families instead of allowing
    # several related flags from one family to dominate the classification.
    participation = rv is not None and 1.25 <= rv <= 6.0
    accumulation = bool(r.get("obv_div") or r.get("obv_high") or
                        (r.get("obv_slope") is not None and r["obv_slope"] > 0.15))
    leadership = (mrs is not None and mrs > 0) and (srs is not None and srs > 0)
    price_waking = r5 is not None and 0 < r5 <= 8 and r.get("accel", 0) > 0.10
    trend_ok = eff is not None and eff >= 0.20 and r20 is not None and r20 > 0
    location_ok = fh is not None and -10 <= fh <= 2
    liquid_enough = turn is None or turn >= 0.25

    checks = {
        "participation": participation,
        "accumulation": accumulation,
        "leadership": leadership,
        "price_waking": price_waking,
        "trend_ok": trend_ok,
        "location_ok": location_ok,
        "liquid_enough": liquid_enough,
    }
    reasons.extend([k for k,v in checks.items() if v])
    core_count = sum(participation for participation in
                     [participation, accumulation, leadership, price_waking, trend_ok, location_ok])

    # Score remains auditable. It starts from zero rather than inheriting v2's stage
    # bonus, so v3 can be judged independently.
    score = 0.0
    if participation:
        score += 1.5 if rv <= 4 else 1.0
    if accumulation:
        score += 1.5 if r.get("obv_div") else 1.0
    if leadership:
        score += 1.5
    elif (mrs is not None and mrs > 0) or (srs is not None and srs > 0):
        score += 0.5
    if price_waking:
        score += 1.25
    if trend_ok:
        score += 0.75
    if location_ok:
        score += 0.75
    if 2 <= (r20 or -999) <= 15:
        score += 1.0
    elif 15 < (r20 or -999) <= 22:
        score += 0.35
    if turn is not None:
        if turn < 0.25:
            score -= 2.0
        elif turn < 0.75:
            score -= 0.5
        elif turn >= 2:
            score += 0.25
    if rv is not None and rv > 8:
        score -= 1.0

    # V3 Discovery is deliberately hard to earn. It must be early, positive now,
    # show participation + accumulation, and lead both market and sector.
    if (liquid_enough and 2 <= (r20 or -999) <= 15 and price_waking and
        participation and accumulation and leadership and core_count >= 5):
        stage = "Discovery"
        score += 1.5
    # Confirming allows a little more extension and does not require perfect 5D
    # acceleration, but still requires genuine participation, accumulation and RS.
    elif (liquid_enough and 2 <= (r20 or -999) <= 22 and r5 is not None and
          0 < r5 <= 12 and rv is not None and 1.15 <= rv <= 8 and accumulation and
          mrs is not None and mrs > 0 and core_count >= 4):
        stage = "Confirming"
        score += 1.0
    elif core_count >= 4 and r20 is not None and r20 > 0:
        stage = "Watch"
        score += 0.2
    else:
        stage = "No setup"
        score -= 0.5

    return stage, round(score, 2), reasons
