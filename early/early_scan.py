#!/usr/bin/env python3
"""Early Momentum Radar v2 — rank EARLINESS, not merely recent strength.

Objective: reduce the whole listed universe to a manageable review list of stocks
where participation appears to be arriving before the move becomes obvious.

Independent evidence families:
  * price acceleration (5/10/20/60 sessions)
  * rupee-turnover acceleration (median recent 5 vs previous 20)
  * OBV accumulation / OBV-price divergence
  * relative strength vs Nifty and own industry group
  * trend efficiency and proximity to a 60-session breakout

V2 adds an explicit lifecycle:
  DISCOVERY -> CONFIRMING -> MOMENTUM -> LATE/EVENT

A large move is therefore not automatically a better result. The ranking rewards
participation and improving price behaviour while price is still in a reasonable
"discovery zone", and penalises event candles, extreme extension, stale trading,
and impractically thin liquidity.

Universe: ../trend/universe.txt. If Yahoo fails for an NSE ticker and stocks.csv
contains a BSE code, the scanner makes a second attempt through <code>.BO so that
small/micro-cap coverage does not disappear silently because of a symbol problem.
"""
import json, math, os, sys
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import stock_registry as registry  # noqa: E402

FETCH = "6mo"
BENCHMARK = "^NSEI"
BATCH = 40
MIN_HISTORY = 66
RECENT = 5
BASE = 20
LIQ_LOOKBACK = 20
MIN_TURNOVER_CR = 0.25       # retain microcaps, but ranking penalises below this
PRACTICAL_TURNOVER_CR = 0.75 # soft preference, not an exclusion
MAX_STALE_FRAC = 0.35
EVENT_DAY = 15.0
WINSOR_PCT = 6.0


def fetch(tickers):
    out = {}
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i+BATCH]
        print(f"fetching {i+1}-{i+len(chunk)} of {len(tickers)}", flush=True)
        raw = yf.download(chunk, period=FETCH, interval="1d", group_by="ticker",
                          auto_adjust=True, progress=False, threads=True)
        if raw is None or raw.empty:
            continue
        for t in chunk:
            try:
                sub = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                sub = sub.dropna(subset=["Close"])
                if not sub.empty:
                    out[t] = sub
            except (KeyError, TypeError):
                pass
    return out


def ret(c, n):
    return (float(c[-1] / c[-(n+1)] - 1) * 100) if len(c) >= n+1 else None


def efficiency(c, n=20):
    if len(c) < n+1:
        return None
    x = np.log(c[-(n+1):])
    steps = np.diff(x)
    cap = math.log(1 + WINSOR_PCT/100)
    steps = np.clip(steps, -cap, cap)
    distance = float(np.abs(steps).sum())
    return abs(float(steps.sum())) / distance if distance else 0.0


def obv_metrics(close, volume):
    if len(close) < 61 or len(volume) < 61:
        return None, None, None
    direction = np.sign(np.diff(close))
    obv = np.concatenate([[0.0], np.cumsum(direction * volume[1:])])
    denom = float(np.median(volume[-20:])) * 20
    slope20 = float((obv[-1] - obv[-21]) / denom) if denom > 0 else 0.0
    obv_high = bool(obv[-1] >= np.max(obv[-60:]))
    price_high = bool(close[-1] >= np.max(close[-60:]))
    divergence = bool(obv_high and not price_high)
    return slope20, obv_high, divergence


def turnover_metrics(df):
    tv = (df["Close"].astype(float) * df["Volume"].astype(float) / 1e7).dropna()
    if len(tv) < RECENT + BASE:
        return None, None
    recent = float(tv.iloc[-RECENT:].median())
    baseline = float(tv.iloc[-(RECENT+BASE):-RECENT].median())
    ratio = recent / baseline if baseline > 0 else None
    liquid = float(tv.iloc[-LIQ_LOOKBACK:].median())
    return ratio, liquid


def analyse(symbol, df, meta, bench, source_symbol=None):
    if df is None or len(df) < MIN_HISTORY or "Volume" not in df:
        return None
    close = df["Close"].to_numpy(float)
    volume = df["Volume"].fillna(0).to_numpy(float)
    if np.any(close[-61:] <= 0):
        return None

    r5, r10, r20, r60 = (ret(close, n) for n in (5, 10, 20, 60))
    # Dailyised acceleration: recent pace minus the longer background pace.
    accel = (r5/5) - (r20/20)
    accel_mid = (r10/10) - (r60/60)
    rvol, turn = turnover_metrics(df)
    obv_slope, obv_high, obv_div = obv_metrics(close, volume)
    eff = efficiency(close, 20)

    prior_high60 = float(np.max(close[-61:-1]))
    from_high = (float(close[-1])/prior_high60 - 1)*100 if prior_high60 > 0 else None
    near_breakout = bool(from_high is not None and -7 <= from_high <= 2)

    daily20 = (close[-20:] / close[-21:-1] - 1)*100
    stale = float((np.abs(daily20) < .001).mean())
    biggest = float(np.max(np.abs(daily20)))
    display = meta.get("nse") or meta.get("bse") or meta.get("isin") or symbol.split(".")[0]
    bench20 = bench.get(20)
    market_rs = r20-bench20 if bench20 is not None else None

    return {
      "isin": meta.get("isin"), "symbol": display,
      "yahoo": source_symbol or symbol,
      "requested_yahoo": symbol,
      "exchange": "BSE" if (source_symbol or symbol).endswith(".BO") else "NSE",
      "fallback": bool(source_symbol and source_symbol != symbol),
      "name": meta.get("name") or display,
      "sector": meta.get("industry_group") or "Unclassified",
      "last": round(float(close[-1]), 2),
      "r5": round(r5, 2), "r10": round(r10, 2), "r20": round(r20, 2), "r60": round(r60, 2),
      "accel": round(accel, 3), "accel_mid": round(accel_mid, 3),
      "rvol": round(rvol, 2) if rvol is not None else None,
      "turnover_cr": round(turn, 2) if turn is not None else None,
      "obv_slope": round(obv_slope, 3) if obv_slope is not None else None,
      "obv_high": obv_high, "obv_div": obv_div,
      "eff20": round(eff, 3) if eff is not None else None,
      "market_rs": round(market_rs, 2) if market_rs is not None else None,
      "from_high60": round(from_high, 2) if from_high is not None else None,
      "near_breakout": near_breakout,
      "biggest20": round(biggest, 2),
      "event": bool(biggest > EVENT_DAY),
      "thin": bool(turn is not None and turn < MIN_TURNOVER_CR),
      "stale": bool(stale > MAX_STALE_FRAC)
    }


def discovery_quality(r):
    """Return transparent component score and lifecycle stage.

    Score intentionally peaks before the move becomes extreme. Thresholds are
    hypotheses to be validated prospectively; they are kept readable so we can
    later see exactly why a name ranked rather than fitting an opaque model.
    """
    flags = []
    score = 0.0

    # 1) Participation: useful when increasing, but 20x is not 10x better than 2x.
    rv = r["rvol"]
    if rv is not None:
        if 1.25 <= rv <= 4.0:
            flags.append("turnover_arriving"); score += 1.5
        elif 4.0 < rv <= 8.0:
            flags.append("turnover_surge"); score += 1.0
        elif rv > 8.0:
            flags.append("turnover_extreme"); score += 0.35

    # 2) OBV: give divergence extra weight because participation can lead price.
    if r["obv_slope"] is not None and r["obv_slope"] > 0.15:
        flags.append("obv_rising"); score += 1.0
    if r["obv_div"]:
        flags.append("obv_leads_price"); score += 1.35
    elif r["obv_high"]:
        flags.append("obv_high"); score += 0.55

    # 3) Price awakening. We prefer positive acceleration before a huge extension.
    if r["accel"] > 0.20 and r["r5"] > 0:
        flags.append("price_waking"); score += 1.0
    if r["accel_mid"] > 0 and r["r10"] > 0:
        flags.append("medium_accel"); score += 0.55

    # 4) Relative strength.
    if r["market_rs"] is not None and r["market_rs"] > 2:
        flags.append("beats_market"); score += 0.8
    if r["sector_rs"] is not None and r["sector_rs"] > 2:
        flags.append("beats_sector"); score += 0.8

    # 5) Structure / location.
    if r["eff20"] is not None and r["eff20"] >= 0.40 and r["r20"] > 0:
        flags.append("cleaning_up"); score += 0.65
    if r["near_breakout"]:
        flags.append("near_breakout"); score += 0.75

    # Discovery-zone reward. A stock up 7-12% with participation is more aligned
    # with this screen than one already up 35% in a month.
    if 2 <= r["r20"] <= 15:
        flags.append("discovery_zone"); score += 1.35
    elif 15 < r["r20"] <= 22:
        score += 0.45

    if 0 < r["r5"] <= 8:
        score += 0.6
    elif r["r5"] > 12:
        score -= 0.7

    # Liquidity stays a SOFT penalty: keep microcaps visible, but do not let a
    # Rs 6 lakh/day chart outrank a similarly good, actually tradable candidate.
    turn = r["turnover_cr"]
    if turn is not None:
        if turn < MIN_TURNOVER_CR:
            score -= 1.75
        elif turn < PRACTICAL_TURNOVER_CR:
            score -= 0.55
        elif turn >= 2:
            score += 0.2

    # Extension/event penalties define the lifecycle rather than deleting names.
    late = (r["r20"] > 30 or r["r5"] > 15 or
            (r["from_high60"] is not None and r["from_high60"] > 5))
    if r["event"]:
        score -= 2.0
    if late:
        score -= 1.25
    if r["stale"]:
        score -= 2.0

    # Lifecycle classification. It describes where the setup is NOW; score ranks
    # within the useful stages.
    if r["event"] or late:
        stage = "Late / Event"
    elif r["stale"]:
        stage = "Review"
    elif (2 <= r["r20"] <= 15 and r["r5"] <= 8 and rv is not None and rv >= 1.25
          and len(flags) >= 4):
        stage = "Discovery"
    elif (r["r20"] <= 22 and r["r5"] <= 12 and len(flags) >= 5):
        stage = "Confirming"
    elif r["r20"] > 0 and len(flags) >= 5:
        stage = "Momentum"
    elif len(flags) >= 3:
        stage = "Watch"
    else:
        stage = "No setup"

    # Stage bonus makes the default ranking explicitly favour early lifecycle.
    score += {"Discovery": 1.5, "Confirming": 1.0, "Watch": 0.2,
              "Momentum": -0.3, "Review": -1.0, "Late / Event": -2.0,
              "No setup": -0.5}.get(stage, 0)
    return flags, round(score, 2), stage


def add_sector_and_signals(rows):
    groups = {}
    for r in rows:
        groups.setdefault(r["sector"], []).append(r)

    sector_stats = {}
    for s, rr in groups.items():
        vals = [x["r20"] for x in rr if x["r20"] is not None]
        rv = [x["rvol"] for x in rr if x["rvol"] is not None]
        sector_stats[s] = {
          "count": len(rr),
          "ret20": round(float(np.median(vals)), 2) if vals else None,
          "rvol": round(float(np.median(rv)), 2) if rv else None,
          "positive20": round(sum(x["r20"] > 0 for x in rr)/len(rr)*100, 1) if rr else None
        }

    for r in rows:
        sr = sector_stats[r["sector"]]["ret20"]
        r["sector_rs"] = round(r["r20"]-sr, 2) if sr is not None else None
        flags, score, stage = discovery_quality(r)
        r["flags"] = flags
        r["radar_score"] = score
        r["stage"] = stage
    return sector_stats


def fallback_map(rows_reg):
    """Map NSE Yahoo ticker -> BSE Yahoo ticker using the stable registry row."""
    out = {}
    for m in rows_reg:
        nse = str(m.get("nse") or "").strip()
        bse = str(m.get("bse") or "").strip()
        if nse and bse:
            out[nse + ".NS"] = bse + ".BO"
    return out


def main():
    universe_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "trend", "universe.txt")
    universe = registry.read_universe(universe_path)
    rows_reg = registry.load()
    meta = registry.by_yahoo(rows_reg)
    fb = fallback_map(rows_reg)
    print(f"Early Momentum Radar v2: {len(universe)} symbols")

    bench_df = fetch([BENCHMARK]).get(BENCHMARK)
    bench = {}
    if bench_df is not None:
        bc = bench_df["Close"].dropna().to_numpy(float)
        for n in (5, 10, 20, 60):
            bench[n] = ret(bc, n)

    frames = fetch(universe)

    # Second chance for unresolved NSE names using a registry BSE code.
    first_failed = [t for t in universe if t not in frames]
    fallback_requested = {t: fb[t] for t in first_failed if t in fb}
    fallback_frames = fetch(list(fallback_requested.values())) if fallback_requested else {}

    rows, failed, recovered = [], [], []
    for t in universe:
        frame = frames.get(t)
        source = t
        m = meta.get(t, {})
        if frame is None and t in fallback_requested:
            alt = fallback_requested[t]
            frame = fallback_frames.get(alt)
            if frame is not None:
                source = alt
                recovered.append(t)
        r = analyse(t, frame, m, bench, source_symbol=source) if frame is not None else None
        (rows.append(r) if r else failed.append(t))

    sectors = add_sector_and_signals(rows)
    stage_order = {"Discovery": 0, "Confirming": 1, "Watch": 2,
                   "Momentum": 3, "Review": 4, "Late / Event": 5, "No setup": 6}
    rows.sort(key=lambda x: (stage_order.get(x["stage"], 9), -x["radar_score"]))

    payload = {
      "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
      "method": "v2 lifecycle ranking: discovery first; independent confirmations; not a buy signal",
      "requested": len(universe), "resolved": len(rows), "failed": failed,
      "fallback_recovered": recovered,
      "benchmark": {str(k): round(v, 2) if v is not None else None for k, v in bench.items()},
      "sectors": sectors, "rows": rows
    }
    with open("early.json", "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    counts = {s: sum(r["stage"] == s for r in rows) for s in stage_order}
    print(f"wrote early.json — {len(rows)} resolved, {len(failed)} failed, {len(recovered)} recovered via BSE")
    print("stages: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for r in rows[:30]:
        print(f"{r['symbol']:<16} {r['stage']:<13} {r['radar_score']:>5.2f} "
              f"20d={r['r20']:>6.1f}% 5d={r['r5']:>6.1f}% rv={str(r['rvol']):>5} "
              + ",".join(r["flags"]))


if __name__ == "__main__":
    main()
