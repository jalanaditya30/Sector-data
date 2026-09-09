#!/usr/bin/env python3
"""Shortlist generator - constraint universe ranked by the tested momentum rule.

Two layers, and only two:

  Layer 1  screen/constraint_universe.txt, built by constraint_layer.py.
           Mechanical constraints. No predictive claim.

  Layer 2  12-1 momentum ranking, exactly as specified in
           momentum_decision_rule.json and passed on 2026-09-08
           (N=20 percentile 99.9, pairwise 0.990, stable across caps).

Nothing else is added. No sector-strength filter, no turnover persistence, no
market regime gate, no compression features. Every one of those was tested and
failed, or was never tested at all. If a condition is not in the passed
specification it does not belong in this file - that is how V6.2 happened.

Output:
  screen/shortlist.json   full ranked decile with context columns
  screen/shortlist.md     readable table, top names first

WHAT THIS IS
  An attention-allocation tool. It tells you which names to look at.

WHAT THIS IS NOT
  A buy signal, a position sizer, or a claim about any individual name. The
  passed test was a portfolio-level result over 152 sample dates. It says
  nothing about whether any single stock on this list will rise.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.basename(HERE) == "early" else HERE
sys.path.insert(0, ROOT)
import stock_registry as registry

SCREEN = os.path.join(HERE, "screen")
UNIVERSE_IN = os.path.join(SCREEN, "constraint_universe.txt")
SPEC_PATH = os.path.join(HERE, "momentum_decision_rule.json")
JSON_OUT = os.path.join(SCREEN, "shortlist.json")
MD_OUT = os.path.join(SCREEN, "shortlist.md")

REVIEW_COUNT = 25          # how many names you actually read. See caveat below.
BATCH = 40


def load_spec() -> dict:
    spec = json.load(open(SPEC_PATH))
    if spec.get("hypothesis_id") != "MOM_12_1_TOPDECILE_V1":
        sys.exit("ABORT: unexpected hypothesis id. This file implements the rule that "
                 "passed on 2026-09-08 and nothing else.")
    return spec


def read_universe(path):
    if not os.path.exists(path):
        sys.exit(f"ABORT: {path} missing. Run constraint_layer.py first - the "
                 "shortlist must be built on a freshly constrained universe.")
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2 and parts[1]:
                out.append(parts[1])
    return list(dict.fromkeys(out))


def fetch(tickers):
    """auto_adjust=False so raw Close x Volume gives true cash turnover, while
    Adj Close / Close yields the factor used to adjust prices for returns."""
    out = {}
    for i in range(0, len(tickers), BATCH):
        ch = tickers[i:i + BATCH]
        print(f"fetch {i+1}-{i+len(ch)}/{len(tickers)}", flush=True)
        z = yf.download(ch, period="2y", interval="1d", group_by="ticker",
                        auto_adjust=False, progress=False, threads=True)
        if z is None or z.empty:
            continue
        for t in ch:
            try:
                d = z[t] if isinstance(z.columns, pd.MultiIndex) else z
                d = d.dropna(subset=["Close"]).copy()
                if d.empty:
                    continue
                if "Adj Close" in d.columns:
                    f = (d["Adj Close"] / d["Close"]).replace([np.inf, -np.inf], np.nan)
                    f = f.ffill().bfill().fillna(1.0)
                else:
                    f = pd.Series(1.0, index=d.index)
                d["TurnClose"] = d["Close"]
                d["AdjC"] = d["Close"] * f
                out[t] = d
            except Exception:
                pass
    return out


def main():
    spec = load_spec()
    sig, inv = spec["signal"], spec["investable_set"]
    form, skip = sig["formation_return_sessions"], sig["skip_recent_sessions"]
    win = inv["turnover_window_sessions"]
    need = form + skip + 5

    universe = read_universe(UNIVERSE_IN)
    meta = registry.by_yahoo(registry.load())
    print(f"constraint universe: {len(universe)} names", flush=True)
    frames = fetch(universe)

    rows, skipped = [], 0
    for t in universe:
        d = frames.get(t)
        if d is None or len(d) < need:
            skipped += 1
            continue
        adj = d["AdjC"].to_numpy(float)
        raw = d["TurnClose"].to_numpy(float)
        vol = d["Volume"].to_numpy(float)

        base_px, end_px = adj[-(form + skip)], adj[-(skip + 1)]
        if not (np.isfinite(base_px) and base_px > 0 and np.isfinite(end_px)):
            skipped += 1
            continue

        turn = raw[-win:] * vol[-win:]
        turn = turn[np.isfinite(turn)]
        turnover_cr = float(np.median(turn) / 1e7) if len(turn) else 0.0

        # Context only. Not filters, not part of the tested rule.
        hi120 = float(np.max(adj[-121:-1])) if len(adj) > 121 else np.nan
        step = adj[1:] / adj[:-1] - 1.0
        m = meta.get(t) or {}
        rows.append({
            "ticker": t,
            "nse": m.get("nse", t.replace(".NS", "")),
            "name": m.get("name", ""),
            "sector": m.get("industry_group") or "Unknown",
            "momentum_12_1_pct": round(float(end_px / base_px - 1.0) * 100, 1),
            "mcap_cr": round(float(m.get("mcap", 0.0)), 0),
            "turnover_cr": round(turnover_cr, 2),
            "last_close": round(float(raw[-1]), 2),
            "from_high120_pct": round(float(adj[-1] / hi120 - 1.0) * 100, 1) if np.isfinite(hi120) else None,
            "return_20d_pct": round(float(adj[-1] / adj[-21] - 1.0) * 100, 1) if len(adj) > 21 else None,
            "max_daily_move_20d_pct": round(float(np.max(np.abs(step[-20:]))) * 100, 1) if len(step) > 20 else None,
        })

    rows.sort(key=lambda r: -r["momentum_12_1_pct"])
    decile_n = max(1, int(round(len(rows) * 0.10)))
    decile = rows[:decile_n]
    for i, r in enumerate(decile, 1):
        r["rank"] = i

    sector_counts = {}
    for r in decile[:REVIEW_COUNT]:
        sector_counts[r["sector"]] = sector_counts.get(r["sector"], 0) + 1

    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {
        "generated": generated,
        "rule": "MOM_12_1_TOPDECILE_V1 (passed 2026-09-08)",
        "status": "attention_allocation_tool_not_a_buy_signal",
        "universe_size": len(rows),
        "decile_size": decile_n,
        "review_count": REVIEW_COUNT,
        "review_sector_counts": dict(sorted(sector_counts.items(), key=lambda x: -x[1])),
        "skipped_insufficient_history": skipped,
        "caveats": [
            "The tested rule is the top decile as a portfolio. Cutting to the top "
            f"{REVIEW_COUNT} is an attention budget, not a tested rule - rank 3 is not "
            "established to be better than rank 40.",
            "The passing window (2023-2026) was one continuous smallcap bull run and "
            "contains no momentum crash.",
            "Universe carries survivorship bias: today's listed names applied backwards.",
            "Context columns below the momentum figure are descriptive. They were not "
            "filters in the tested rule and must not be used as such without a new "
            "pre-registered test.",
        ],
        "decile": decile,
    }
    os.makedirs(SCREEN, exist_ok=True)
    with open(JSON_OUT, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    lines = [
        f"# Shortlist for manual review - {generated[:10]}",
        "",
        f"Rule: `MOM_12_1_TOPDECILE_V1`, passed 2026-09-08. "
        f"{len(rows)} constrained names, top decile = {decile_n}.",
        "",
        "**Not a buy signal.** This narrows what to read, nothing more. "
        "Context columns are descriptive and were not filters in the tested rule.",
        "",
        f"## Top {REVIEW_COUNT}",
        "",
        "| # | Symbol | Name | Sector | 12-1 mom | Mcap cr | Turn cr | From 120D hi | 20D | Max 1D move 20D |",
        "|--:|---|---|---|--:|--:|--:|--:|--:|--:|",
    ]
    for r in decile[:REVIEW_COUNT]:
        lines.append(
            f"| {r['rank']} | {r['nse']} | {r['name'][:34]} | {r['sector'][:22]} | "
            f"{r['momentum_12_1_pct']:.1f}% | {r['mcap_cr']:.0f} | {r['turnover_cr']:.1f} | "
            f"{r['from_high120_pct']}% | {r['return_20d_pct']}% | {r['max_daily_move_20d_pct']}% |")
    lines += ["", f"Sector spread in the top {REVIEW_COUNT}: " +
              ", ".join(f"{k} {v}" for k, v in payload["review_sector_counts"].items()),
              "", f"Full ranked decile ({decile_n} names) in `shortlist.json`.", ""]
    with open(MD_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n{len(rows)} constrained -> decile {decile_n} -> review {REVIEW_COUNT}", flush=True)
    print("sector spread:", payload["review_sector_counts"], flush=True)
    print("written", JSON_OUT, "and", MD_OUT, flush=True)


if __name__ == "__main__":
    main()
