#!/usr/bin/env python3
"""Pre-registered test: does plain 12-1 momentum beat random picks after costs?

Reads its specification from momentum_decision_rule.json and refuses to run if
that file is missing. The gate is applied mechanically at the end and the verdict
is written into the result payload - there is no interpretation step.

Reuses portfolio_sim.py for data fetching and price helpers. Deliberately does
NOT reuse seed_robustness.simulate_rebalanced: that function processes new
entries before exits, so a signal arriving on the day a position matures cannot
take the freed slot. That is fixed here. Absolute figures are therefore not
comparable to the R2 runs; the strategy-versus-control comparison, which is what
the gate reads, is unaffected because both sides use the same simulator.
"""
from __future__ import annotations

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
import portfolio_sim as base

SPEC_PATH = os.path.join(HERE, "momentum_decision_rule.json")
OUT = os.path.join(HERE, "momentum_test_result.json")
PANEL_CACHE = os.path.join(HERE, ".cache", "momentum_panel_v2.pkl")
RAW_FRAME_CACHE = os.path.join(HERE, ".cache", "momentum_frames_raw_v2.pkl")


def load_spec() -> dict:
    if not os.path.exists(SPEC_PATH):
        sys.exit("ABORT: momentum_decision_rule.json not found. The gate must be "
                 "committed before this test runs.")
    spec = json.load(open(SPEC_PATH))
    if not spec.get("registered_before_results"):
        sys.exit("ABORT: specification is not marked as registered before results.")
    return spec


def _norm_df(d):
    if d is None or d.empty:
        return None
    d = d.copy()
    d.index = pd.DatetimeIndex(d.index).tz_localize(None)
    d = d[~d.index.duplicated(keep="last")].sort_index()
    d = d.dropna(subset=["Close"])
    # Preserve raw traded close strictly for cash-turnover calculation.
    d["TurnClose"] = d["Close"]
    # Use split/dividend-adjusted execution prices so corporate actions do not
    # create fake P&L jumps during a held position.
    if "Adj Close" in d.columns:
        f = (d["Adj Close"] / d["Close"]).replace([np.inf, -np.inf], np.nan).ffill().bfill().fillna(1.0)
    else:
        f = pd.Series(1.0, index=d.index)
    d["Open"] = d["Open"] * f
    d["Close"] = d["Close"] * f
    return d


def fetch_raw(tickers):
    """Fetch raw OHLCV, retain raw close for turnover, adjust execution prices."""
    os.makedirs(os.path.dirname(RAW_FRAME_CACHE), exist_ok=True)
    tickers = list(dict.fromkeys(tickers))
    cached = {}
    if os.path.exists(RAW_FRAME_CACHE):
        try:
            cached = pickle.load(open(RAW_FRAME_CACHE, "rb"))
        except Exception:
            cached = {}
    missing = [t for t in tickers if t not in cached]
    for i in range(0, len(missing), base.BATCH):
        ch = missing[i:i + base.BATCH]
        print(f"fetch raw {i+1}-{i+len(ch)}/{len(missing)}", flush=True)
        z = yf.download(ch, period=base.PERIOD, interval="1d", group_by="ticker",
                        auto_adjust=False, progress=False, threads=True)
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
        with open(RAW_FRAME_CACHE, "wb") as fh:
            pickle.dump(cached, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return {t: cached[t] for t in tickers if t in cached}


def order_key(day, ticker, seed):
    return hashlib.sha256(f"{day}|{ticker}|MOM|{seed}".encode()).hexdigest()


def median_turnover_cr(c, v, window):
    if len(c) < window:
        return None
    turn = (c[-window:] * v[-window:])
    turn = turn[np.isfinite(turn)]
    return float(np.median(turn) / 1e7) if len(turn) else None


def build_panel(frames, universe, meta, mid, small, spec):
    """Per sample date: the investable set and each name's formation return."""
    if os.path.exists(PANEL_CACHE):
        try:
            cached = pickle.load(open(PANEL_CACHE, "rb"))
            print("panel loaded from cache", flush=True)
            return cached
        except Exception:
            pass

    sig = spec["signal"]
    inv = spec["investable_set"]
    form, skip = sig["formation_return_sessions"], sig["skip_recent_sessions"]
    need_hist = form + skip + 5

    common = pd.DatetimeIndex(mid.index).intersection(pd.DatetimeIndex(small.index)).sort_values()
    total = base.TEST_DAYS + base.RESERVE_FUTURE
    if len(common) < total:
        raise RuntimeError(f"need >= {total} common sessions, got {len(common)}")
    scored = list(common[-total:-base.RESERVE_FUTURE])
    sample_days = scored[::sig["sampling_every"] if "sampling_every" in sig else base.SAMPLE_EVERY]

    day_all, day_sig = {}, {}
    for di, day in enumerate(sample_days):
        if di % 20 == 0:
            print(f"panel {di+1}/{len(sample_days)} {day.date()}", flush=True)
        rows = []
        for t in universe:
            d = frames.get(t)
            if d is None:
                continue
            p = base.pos(d, day)
            if p < need_hist:
                continue
            c_raw = d["TurnClose"].to_numpy(float)[:p + 1]
            v = d["Volume"].to_numpy(float)[:p + 1]
            turn = median_turnover_cr(c_raw, v, inv["turnover_window_sessions"])
            if turn is None or turn < inv["min_turnover_cr_per_day"]:
                continue
            m = meta.get(t) or {}
            if float(m.get("mcap", 0.0)) < inv["min_mcap_cr"]:
                continue
            # 12-1 momentum uses the already adjusted Close series.
            c_mom = d["Close"].to_numpy(float)[:p + 1]
            base_px, end_px = c_mom[-(form + skip)], c_mom[-(skip + 1)]
            if not (np.isfinite(base_px) and base_px > 0 and np.isfinite(end_px)):
                continue
            rows.append({
                "date": str(pd.Timestamp(day).date()), "ticker": t,
                "sector": m.get("industry_group") or "Unknown",
                "turnover_cr": turn, "cost_rate": base.cost_rate(turn),
                "formation_return": float(end_px / base_px - 1.0),
            })
        rows = [r for r in rows if r["cost_rate"] is not None]
        key = str(pd.Timestamp(day).date())
        day_all[key] = rows
        if rows:
            k = max(1, int(round(len(rows) * 0.10)))
            day_sig[key] = sorted(rows, key=lambda r: -r["formation_return"])[:k]
        else:
            day_sig[key] = []

    os.makedirs(os.path.dirname(PANEL_CACHE), exist_ok=True)
    payload = (scored, sample_days, day_all, day_sig)
    pickle.dump(payload, open(PANEL_CACHE, "wb"), protocol=pickle.HIGHEST_PROTOCOL)
    return payload


def simulate(schedule, frames, calendar, n_slots, hold):
    """Exits are processed before entries, so a maturing slot is reusable same day."""
    cash, positions, curve, concurrent = 1.0, [], [], []
    traded = 0.0
    entries = exits = 0

    for day in calendar:
        ds = str(pd.Timestamp(day).date())

        # 1. Age and exit at today's open.
        keep = []
        for p in positions:
            op = base.price_on(frames.get(p["ticker"]), day, "Open") or p["last_px"]
            if p["age"] >= hold:
                value = p["units"] * op
                cash += value * (1.0 - p["cost_rate"] / 2.0)
                traded += value
                exits += 1
            else:
                keep.append(p)
        positions = keep

        # 2. Size against equity marked at today's open.
        open_val = 0.0
        for p in positions:
            op = base.price_on(frames.get(p["ticker"]), day, "Open") or p["last_px"]
            open_val += p["units"] * op
        equity_open = cash + open_val

        # 3. Enter at today's open.
        held = {p["ticker"] for p in positions}
        incoming = [r for r in schedule.get(ds, []) if r["ticker"] not in held]
        for r in incoming[:max(0, n_slots - len(positions))]:
            op = base.price_on(frames.get(r["ticker"]), day, "Open")
            if op is None:
                continue
            target = min(equity_open / n_slots, cash)
            if target <= 0:
                break
            gross = target / (1.0 + r["cost_rate"] / 2.0)
            cash -= target
            traded += gross
            entries += 1
            positions.append({"ticker": r["ticker"], "units": gross / op, "age": 0,
                              "last_px": op, "cost_rate": r["cost_rate"]})

        # 4. Mark to close and age.
        for p in positions:
            p["last_px"] = base.price_on(frames.get(p["ticker"]), day, "Close") or p["last_px"]
            p["age"] += 1
        eq = cash + sum(p["units"] * p["last_px"] for p in positions)
        curve.append(float(eq))
        concurrent.append(len(positions))

    eq = np.array(curve, float)
    rr = np.zeros_like(eq)
    rr[1:] = eq[1:] / eq[:-1] - 1
    years = max((len(eq) - 1) / 252.0, 1 / 252)
    sd = float(np.std(rr[1:], ddof=1)) if len(rr) > 2 else 0.0
    return {
        "terminal_wealth": float(eq[-1]),
        "cagr": float(eq[-1] ** (1 / years) - 1),
        "max_drawdown": float(np.min(eq / np.maximum.accumulate(eq) - 1)),
        "sharpe": float(np.mean(rr[1:]) / sd * math.sqrt(252)) if sd > 0 else 0.0,
        "average_concurrent_positions": float(np.mean(concurrent)),
        "annualized_turnover": float(traded / max(float(np.mean(eq)), 1e-12) / years),
        "entries": entries, "exits": exits,
    }


def schedule_for(day_signals, calendar, seed):
    sch = base.make_schedule(day_signals, calendar)
    for day, rows in sch.items():
        sch[day] = sorted(rows, key=lambda r: order_key(r["signal_date"], r["ticker"], seed))
    return sch


def random_days(day_all, day_sig, rng):
    """Unmatched draw: same count per day, drawn from that day's whole investable set."""
    out = {}
    for day, sigs in day_sig.items():
        pool = day_all.get(day, [])
        if not sigs or not pool:
            out[day] = []
            continue
        take = min(len(sigs), len(pool))
        idx = rng.choice(len(pool), size=take, replace=False)
        out[day] = [pool[int(i)] for i in np.atleast_1d(idx)]
    return out


def diststats(a):
    a = np.asarray(a, float)
    return {"n": len(a), "mean": float(np.mean(a)), "median": float(np.median(a)),
            "p05": float(np.quantile(a, .05)), "p95": float(np.quantile(a, .95)),
            "min": float(np.min(a)), "max": float(np.max(a)), "std": float(np.std(a, ddof=1))}


def main():
    spec = load_spec()
    hold = spec["signal"]["holding_sessions"]
    caps = spec["caps"]

    reg = base.registry.load()
    meta = base.registry.by_yahoo(reg)
    universe = base.registry.read_universe(os.path.join(ROOT, "trend", "universe.txt"))
    frames = fetch_raw(universe + [base.MID] + base.SMALL_CANDIDATES)
    mid = frames.get(base.MID)
    if mid is None:
        raise RuntimeError("Midcap benchmark unavailable")
    small_sym, small = base.choose_small(frames)

    scored, sample_days, day_all, day_sig = build_panel(frames, universe, meta, mid, small, spec)
    calendar = pd.DatetimeIndex(mid.index).intersection(pd.DatetimeIndex(small.index)).sort_values()
    first, last = pd.Timestamp(scored[0]), pd.Timestamp(scored[-1])
    end_i = calendar.searchsorted(last, side="right") + hold
    simcal = calendar[(calendar >= first)][:max(1, end_i - calendar.searchsorted(first))]

    seeds = list(range(1, spec["nuisance_seeds"] + 1))
    strategy = {}
    for n in caps:
        runs = [{"seed": s, **simulate(schedule_for(day_sig, simcal, s), frames, simcal, n, hold)}
                for s in seeds]
        strategy[str(n)] = runs
        print(f"strategy N={n} median terminal "
              f"{np.median([r['terminal_wealth'] for r in runs]):.4f}", flush=True)

    rng = np.random.default_rng(20260908)
    controls = {str(n): [] for n in caps}
    for rep in range(spec["control"]["repetitions"]):
        if rep % 100 == 0:
            print(f"control {rep}/{spec['control']['repetitions']}", flush=True)
        rd = random_days(day_all, day_sig, rng)
        for n in caps:
            controls[str(n)].append(
                simulate(schedule_for(rd, simcal, 0), frames, simcal, n, hold)["terminal_wealth"])

    results = {}
    for n in caps:
        k = str(n)
        sd = np.array([r["terminal_wealth"] for r in strategy[k]])
        cd = np.array(controls[k])
        results[k] = {
            "strategy_seed_distribution": diststats(sd),
            "control_distribution": diststats(cd),
            "median_percentile_in_control": float(np.mean(cd < np.median(sd)) * 100),
            "prob_strategy_seed_beats_control": float(np.mean(sd[:, None] > cd[None, :])),
            "runs": strategy[k],
        }

    checks = []
    for cond in spec["gate"]["conditions"]:
        actual = results[str(cond["cap"])][cond["metric"]]
        ok = actual >= cond["threshold"]
        checks.append({"id": cond["id"], "cap": cond["cap"], "metric": cond["metric"],
                       "required": cond["threshold"], "actual": round(actual, 4),
                       "result": "PASS" if ok else "FAIL"})
    verdict = "PASS" if all(c["result"] == "PASS" for c in checks) else "FAIL - MOMENTUM RETIRED"

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hypothesis_id": spec["hypothesis_id"],
        "specification": spec,
        "window": {"start": str(first.date()), "end": str(pd.Timestamp(simcal[-1]).date())},
        "benchmarks": {"midcap150": base.MID, "smallcap250": small_sym},
        "sample_dates": len(sample_days),
        "avg_investable_per_date": round(float(np.mean([len(v) for v in day_all.values()])), 1),
        "avg_signals_per_date": round(float(np.mean([len(v) for v in day_sig.values()])), 1),
        "results": results,
        "gate_checks": checks,
        "verdict": verdict,
    }
    with open(OUT, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print("\n".join(f"{c['result']} {c['id']} (N={c['cap']}) required {c['required']} "
                    f"actual {c['actual']}" for c in checks), flush=True)
    print("VERDICT:", verdict, flush=True)
    print("written", OUT, flush=True)


if __name__ == "__main__":
    main()
