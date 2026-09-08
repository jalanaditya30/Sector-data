#!/usr/bin/env python3
"""Portfolio-level test for the existing V6.2 R2 rule.

Purpose: replace per-signal medians with a tradeable equity-curve test.
This script intentionally tests ONLY the existing R2 rule and does not alter
live logic.

Design
------
* Historical signal grid matches V6.2 validation: ~3 years, sampled every 5
  benchmark sessions, with 60 future sessions reserved.
* R2 predicate is unchanged: persistence 80-100%, turnover 1.7-2.5x,
  close 0..+5% above prior 120D high, bull market, strong sector.
* Signals are known at close; entry is next benchmark session's OPEN to avoid
  same-close look-ahead.
* Hold exactly 40 benchmark trading sessions.
* No duplicate position in the same ticker while already held.
* Fixed-slot capital model: N independent equal capital sleeves, N in 10/20/40.
  Empty sleeves remain cash at 0%.
* If a signal day has more names than free slots, deterministic SHA256 ordering
  is used. This is arbitrary by design and not tuned to returns.
* Round-trip costs: 0.5% if turnover >= Rs2cr/day; 1.5% if Rs0.75-2cr/day;
  names below Rs0.75cr/day are excluded.
* Matched random control preserves each R2 signal day's sector counts and draws
  from that day's investable stocks in those sectors. 1,000 repetitions.

Important limitation: for comparability with the existing R2 pipeline this uses
v6_features.py, whose turnover feature currently comes from auto-adjusted Yahoo
prices. The audit-identified dividend-adjustment issue is NOT silently fixed in
this Section-1 test; it belongs to the later bug-fix stage.
"""
import hashlib
import json
import math
import os
import pickle
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import stock_registry as registry
import v6_features

BATCH = 35
PERIOD = "5y"
TEST_DAYS = 756
SAMPLE_EVERY = 5
WARMUP = 145
HOLD = 40
RESERVE_FUTURE = 60
MID = "MID150BEES.NS"
SMALL_CANDIDATES = ["HDFCSML250.NS", "MOSMALL250.NS"]
CAPS = (10, 20, 40)
RANDOM_REPS = 1000
SEED = 62026
MIN_TURNOVER_CR = 0.75
EVENT_CAP = 20.0
CACHE = os.path.join(HERE, ".cache", "portfolio_frames.pkl")
OUT = os.path.join(HERE, "portfolio_sim_result.json")

R2 = {
    "p_lo": 0.80, "p_hi": 1.01,
    "t_lo": 1.70, "t_hi": 2.50,
    "loc_lo": 0.0, "loc_hi": 5.0,
    "market": "bull", "sector": "strong",
}


def _norm_df(d):
    if d is None or d.empty:
        return None
    d = d.copy()
    d.index = pd.DatetimeIndex(d.index).tz_localize(None)
    d = d[~d.index.duplicated(keep="last")].sort_index()
    return d.dropna(subset=["Close"])


def fetch(tickers):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tickers = list(dict.fromkeys(tickers))
    cached = {}
    if os.path.exists(CACHE):
        try:
            cached = pickle.load(open(CACHE, "rb"))
        except Exception:
            cached = {}
    missing = [t for t in tickers if t not in cached]
    for i in range(0, len(missing), BATCH):
        ch = missing[i:i+BATCH]
        print(f"fetch {i+1}-{i+len(ch)}/{len(missing)}", flush=True)
        z = yf.download(ch, period=PERIOD, interval="1d", group_by="ticker",
                        auto_adjust=True, progress=False, threads=True)
        if z is None or z.empty:
            continue
        for t in ch:
            try:
                d = z[t] if isinstance(z.columns, pd.MultiIndex) else z
                d = _norm_df(d)
                if d is not None and not d.empty:
                    cached[t] = d
            except Exception:
                pass
        if i % (BATCH * 5) == 0:
            pickle.dump(cached, open(CACHE, "wb"), protocol=pickle.HIGHEST_PROTOCOL)
    pickle.dump(cached, open(CACHE, "wb"), protocol=pickle.HIGHEST_PROTOCOL)
    return {t: cached[t] for t in tickers if t in cached}


def pos(df, day):
    return pd.DatetimeIndex(df.index).searchsorted(pd.Timestamp(day), side="right") - 1


def pastret(df, p, n):
    if p < n:
        return None
    c = df["Close"].to_numpy(float)
    return float((c[p] / c[p-n] - 1) * 100)


def market_regime(m20, m60, s20, s60):
    if any(x is None for x in (m20, m60, s20, s60)):
        return "unknown"
    if m20 > 0 and m60 > 0 and s20 > 0 and s60 > 0:
        return "bull"
    if m20 < 0 and s20 < 0 and (m60 < 0 or s60 < 0):
        return "bear"
    return "mixed"


def sector_regime(median20, breadth):
    if median20 is None:
        return "unknown"
    if median20 > 2 and breadth >= 0.55:
        return "strong"
    if median20 < -2 and breadth < 0.45:
        return "weak"
    return "neutral"


def r2_match(f, market, sector):
    return (R2["p_lo"] <= f["turnover_persistence"] < R2["p_hi"] and
            R2["t_lo"] <= f["turnover_change"] < R2["t_hi"] and
            R2["loc_lo"] <= f["from_high120"] <= R2["loc_hi"] and
            market == "bull" and sector == "strong")


def cost_rate(turn_cr):
    if turn_cr is None or turn_cr < 0.75:
        return None
    return 0.005 if turn_cr >= 2.0 else 0.015


def stable_order(day, ticker):
    raw = f"{day}|{ticker}|R2|{SEED}".encode()
    return hashlib.sha256(raw).hexdigest()


def choose_small(frames):
    options = [(t, frames.get(t)) for t in SMALL_CANDIDATES]
    options = [(t, d) for t, d in options if d is not None]
    if not options:
        raise RuntimeError("smallcap benchmark unavailable")
    return max(options, key=lambda x: len(x[1]))


def build_signal_panel(frames, universe, meta, mid, small):
    common = pd.DatetimeIndex(mid.index).intersection(pd.DatetimeIndex(small.index)).sort_values()
    need = TEST_DAYS + RESERVE_FUTURE
    if len(common) < need:
        raise RuntimeError(f"need >= {need} common benchmark sessions, got {len(common)}")
    scored_days = list(common[-need:-RESERVE_FUTURE])
    sample_days = scored_days[::SAMPLE_EVERY]
    day_all = {}
    day_r2 = {}

    for di, day in enumerate(sample_days):
        if di % 20 == 0:
            print(f"signal panel {di+1}/{len(sample_days)} {day.date()}", flush=True)
        mp, sp = pos(mid, day), pos(small, day)
        if min(mp, sp) < WARMUP:
            continue
        bm = {k: pastret(mid, mp, k) for k in (10, 20, 60)}
        mr = market_regime(pastret(mid, mp, 20), pastret(mid, mp, 60),
                           pastret(small, sp, 20), pastret(small, sp, 60))
        rows = []
        for t in universe:
            d = frames.get(t)
            if d is None:
                continue
            p = pos(d, day)
            if p < WARMUP:
                continue
            f = v6_features.features(d.iloc[:p+1], bm)
            if f is None or f["event20"] > EVENT_CAP:
                continue
            cr = float(f["turnover_cr"])
            cst = cost_rate(cr)
            if cst is None:
                continue
            sector = (meta.get(t) or {}).get("industry_group") or "Unknown"
            rows.append({
                "date": str(pd.Timestamp(day).date()), "ticker": t, "sector": sector,
                "turnover_cr": cr, "cost_rate": cst, "f": f,
            })
        bysec = defaultdict(list)
        for r in rows:
            if r["f"]["r20"] is not None:
                bysec[r["sector"]].append(r["f"]["r20"])
        secstat = {s: (float(np.median(v)), float(np.mean(np.array(v) > 0)))
                   for s, v in bysec.items() if v}
        for r in rows:
            med, breadth = secstat.get(r["sector"], (None, None))
            r["sector_regime"] = sector_regime(med, breadth)
            r["market_regime"] = mr
        key = str(pd.Timestamp(day).date())
        day_all[key] = rows
        day_r2[key] = [r for r in rows if r2_match(r["f"], mr, r["sector_regime"])]

    return scored_days, sample_days, day_all, day_r2


def next_session(calendar, day):
    i = calendar.searchsorted(pd.Timestamp(day), side="right")
    return None if i >= len(calendar) else calendar[i]


def make_schedule(day_signals, calendar):
    schedule = defaultdict(list)
    for day, rows in day_signals.items():
        entry = next_session(calendar, day)
        if entry is None:
            continue
        for r in rows:
            x = dict(r)
            x["signal_date"] = day
            x["entry_date"] = str(pd.Timestamp(entry).date())
            schedule[x["entry_date"]].append(x)
    return schedule


def price_on(df, day, col):
    if df is None:
        return None
    day = pd.Timestamp(day)
    if day not in df.index or col not in df:
        return None
    x = df.at[day, col]
    try:
        return float(x) if np.isfinite(x) and float(x) > 0 else None
    except Exception:
        return None


def simulate(schedule, frames, calendar, n_slots):
    # Each sleeve starts with equal capital; empty sleeves are cash.
    slots = [{"capital": 1.0 / n_slots, "pos": None} for _ in range(n_slots)]
    curve = []
    concurrent = []
    traded_notional = 0.0
    entries = exits = 0

    for day in calendar:
        daystr = str(pd.Timestamp(day).date())

        # Existing positions mark close-to-close. New positions enter later below at today's open.
        for sl in slots:
            p = sl["pos"]
            if not p:
                continue
            df = frames.get(p["ticker"])
            px = price_on(df, day, "Close")
            if px is not None and p["last_px"] is not None:
                sl["capital"] *= px / p["last_px"]
                p["last_px"] = px
            p["age"] += 1
            if p["age"] >= HOLD:
                # Exit at today's close; half of round-trip cost on exit.
                exit_cost = p["cost_rate"] / 2.0
                notional = sl["capital"]
                sl["capital"] *= (1.0 - exit_cost)
                traded_notional += notional
                exits += 1
                sl["pos"] = None

        # Fill free sleeves from signals known at the previous signal close.
        incoming = schedule.get(daystr, [])
        if incoming:
            held = {sl["pos"]["ticker"] for sl in slots if sl["pos"]}
            incoming = [r for r in incoming if r["ticker"] not in held]
            incoming = sorted(incoming, key=lambda r: stable_order(r["signal_date"], r["ticker"]))
            free = [sl for sl in slots if sl["pos"] is None]
            for sl, r in zip(free, incoming):
                df = frames.get(r["ticker"])
                opx = price_on(df, day, "Open")
                cpx = price_on(df, day, "Close")
                if opx is None or cpx is None:
                    continue
                entry_cost = r["cost_rate"] / 2.0
                notional = sl["capital"]
                sl["capital"] *= (1.0 - entry_cost)
                traded_notional += notional
                # Participate from next-session open to same-day close.
                sl["capital"] *= cpx / opx
                sl["pos"] = {
                    "ticker": r["ticker"], "age": 1, "last_px": cpx,
                    "cost_rate": r["cost_rate"], "sector": r["sector"],
                }
                entries += 1

        equity = float(sum(sl["capital"] for sl in slots))
        npos = sum(sl["pos"] is not None for sl in slots)
        curve.append((daystr, equity))
        concurrent.append(npos)

    eq = np.array([x[1] for x in curve], dtype=float)
    rets = np.zeros_like(eq)
    rets[1:] = eq[1:] / eq[:-1] - 1.0
    years = max((len(eq) - 1) / 252.0, 1/252)
    cagr = eq[-1] ** (1.0 / years) - 1.0
    peak = np.maximum.accumulate(eq)
    maxdd = float(np.min(eq / peak - 1.0))
    sd = float(np.std(rets[1:], ddof=1)) if len(rets) > 2 else 0.0
    sharpe = float(np.mean(rets[1:]) / sd * math.sqrt(252)) if sd > 0 else 0.0
    avg_eq = float(np.mean(eq))
    annual_turnover = traded_notional / max(avg_eq, 1e-12) / years
    return {
        "terminal_wealth": float(eq[-1]),
        "cagr": float(cagr), "max_drawdown": maxdd, "sharpe": sharpe,
        "annualized_turnover": float(annual_turnover),
        "average_concurrent_positions": float(np.mean(concurrent)),
        "pct_days_fully_invested": float(np.mean(np.array(concurrent) == n_slots)),
        "entries": entries, "exits": exits,
        "equity_curve": [{"date": d, "equity": round(v, 8)} for d, v in curve],
    }


def benchmark_curve(small, calendar):
    vals = []
    base = None
    for day in calendar:
        px = price_on(small, day, "Close")
        if px is None:
            vals.append((str(pd.Timestamp(day).date()), None))
            continue
        if base is None:
            base = px
        vals.append((str(pd.Timestamp(day).date()), px / base))
    clean = [v for _, v in vals if v is not None]
    arr = np.array(clean, float)
    years = max((len(arr)-1)/252.0, 1/252)
    peak = np.maximum.accumulate(arr)
    rets = arr[1:] / arr[:-1] - 1
    sd = float(np.std(rets, ddof=1)) if len(rets)>1 else 0
    return {
        "terminal_wealth": float(arr[-1]),
        "cagr": float(arr[-1] ** (1/years) - 1),
        "max_drawdown": float(np.min(arr/peak - 1)),
        "sharpe": float(np.mean(rets)/sd*math.sqrt(252)) if sd>0 else 0.0,
        "equity_curve": [{"date": d, "equity": None if v is None else round(v,8)} for d,v in vals],
    }


def random_signal_days(day_all, day_r2, rng):
    out = {}
    for day, sigs in day_r2.items():
        if not sigs:
            out[day] = []
            continue
        counts = defaultdict(int)
        for s in sigs:
            counts[s["sector"]] += 1
        pools = defaultdict(list)
        for r in day_all.get(day, []):
            pools[r["sector"]].append(r)
        picks = []
        for sector, k in counts.items():
            pool = pools.get(sector, [])
            if not pool:
                continue
            take = min(k, len(pool))
            idx = rng.choice(len(pool), size=take, replace=False)
            picks.extend([pool[int(i)] for i in np.atleast_1d(idx)])
        out[day] = picks
    return out


def compact_metrics(x):
    return {k: round(v, 6) if isinstance(v, float) else v for k, v in x.items() if k != "equity_curve"}


def main():
    reg = registry.load(); meta = registry.by_yahoo(reg)
    universe = registry.read_universe(os.path.join(ROOT, "trend", "universe.txt"))
    frames = fetch(universe + [MID] + SMALL_CANDIDATES)
    mid = frames.get(MID)
    if mid is None:
        raise RuntimeError("Midcap150 benchmark unavailable")
    small_sym, small = choose_small(frames)

    scored_days, sample_days, day_all, day_r2 = build_signal_panel(frames, universe, meta, mid, small)
    calendar = pd.DatetimeIndex(mid.index).intersection(pd.DatetimeIndex(small.index)).sort_values()
    first = pd.Timestamp(scored_days[0])
    last_signal = pd.Timestamp(scored_days[-1])
    end_i = calendar.searchsorted(last_signal, side="right") + HOLD
    sim_calendar = calendar[(calendar >= first)][:max(1, end_i - calendar.searchsorted(first))]

    raw_signal_count = sum(len(v) for v in day_r2.values())
    signal_days = sum(bool(v) for v in day_r2.values())
    strategy = {}
    controls = {}
    base_schedule = make_schedule(day_r2, sim_calendar)
    bench = benchmark_curve(small, sim_calendar)

    for n in CAPS:
        print(f"simulate strategy N={n}", flush=True)
        st = simulate(base_schedule, frames, sim_calendar, n)
        strategy[str(n)] = st
        rng = np.random.default_rng(SEED + n)
        terminal = np.empty(RANDOM_REPS, float)
        for rep in range(RANDOM_REPS):
            if rep % 100 == 0:
                print(f"random N={n} rep {rep}/{RANDOM_REPS}", flush=True)
            rd = random_signal_days(day_all, day_r2, rng)
            rs = make_schedule(rd, sim_calendar)
            rr = simulate(rs, frames, sim_calendar, n)
            terminal[rep] = rr["terminal_wealth"]
        controls[str(n)] = {
            "repetitions": RANDOM_REPS,
            "terminal_wealth_mean": float(np.mean(terminal)),
            "terminal_wealth_median": float(np.median(terminal)),
            "terminal_wealth_p05": float(np.quantile(terminal, .05)),
            "terminal_wealth_p95": float(np.quantile(terminal, .95)),
            "strategy_percentile": float(np.mean(terminal < st["terminal_wealth"]) * 100),
        }

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "test": "Section 1 only: R2 portfolio equity curve + matched random control",
        "rule": R2,
        "signal_generation": {
            "sample_every_sessions": SAMPLE_EVERY,
            "scored_sessions": len(scored_days),
            "sample_dates": len(sample_days),
            "raw_r2_signals_after_liquidity_floor": raw_signal_count,
            "distinct_r2_signal_days": signal_days,
            "entry": "next benchmark session open",
            "holding_sessions": HOLD,
            "liquidity_floor_cr_per_day": MIN_TURNOVER_CR,
            "costs": {">=2cr": 0.005, "0.75-2cr": 0.015, "<0.75cr": "excluded"},
            "oversubscription": "deterministic SHA256 ordering; not return-ranked",
        },
        "window": {
            "scored_start": str(pd.Timestamp(scored_days[0]).date()),
            "scored_end": str(pd.Timestamp(scored_days[-1]).date()),
            "simulation_end": str(pd.Timestamp(sim_calendar[-1]).date()),
        },
        "benchmarks": {"midcap150": MID, "smallcap250": small_sym},
        "smallcap250_buy_hold": bench,
        "strategy": strategy,
        "matched_random_control": controls,
        "headline": {str(n): {"strategy": compact_metrics(strategy[str(n)]),
                              "control": {k: round(v,6) if isinstance(v,float) else v for k,v in controls[str(n)].items()}}
                     for n in CAPS},
        "limitations": [
            "Current-universe survivorship bias remains in this Section-1 test.",
            "Sector mapping is today's registry mapping applied historically.",
            "Yahoo adjusted-price turnover bias is intentionally not fixed yet, to keep the R2 feature definition comparable to the existing pipeline.",
            "Random control preserves signal-day sector counts but not other stock characteristics.",
            "No inference claim is made here; day-level bootstrap inference is Section 2 and is intentionally not implemented in this file.",
        ],
    }
    with open(OUT, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print("written", OUT, flush=True)
    print(json.dumps(payload["headline"], indent=2), flush=True)

if __name__ == "__main__":
    main()
