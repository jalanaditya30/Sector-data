#!/usr/bin/env python3
"""Layer 1 - mechanical constraints on the scan universe.

This file makes NO claim about future returns. Every filter here removes names
that are untradeable, ineligible, or carry non-price risk you would not accept
regardless of how they screen. It is therefore not subject to a decision gate:
there is nothing to validate, only to apply correctly.

Filters, in order:
  1. Market cap floor            (stocks.csv `mcap_cr`)
  2. Median daily turnover floor (Yahoo OHLCV, own recent history)
  3. NSE series must be EQ       (latest sec_bhavdata_full)
  4. Not under ASM or GSM        (NSE surveillance reports)
  5. Excluded holdings           (optional exclude list)

Output:
  screen/constraint_universe.txt   ISIN,YAHOO lines, same format as trend/universe.txt
  screen/constraint_report.json    funnel counts and every dropped name with a reason

IMPORTANT - fail loud, never fail open.
If the NSE series or surveillance data cannot be fetched, this script ABORTS.
A surveillance filter that silently passes everything is worse than no filter,
because you would believe the shortlist was screened when it was not.

Point-in-time note: filters 3 and 4 use TODAY's NSE data and are therefore only
valid for building today's shortlist. Do NOT reuse this output as a historical
backtest universe - see momentum_test.py, which applies only the two filters
that can be computed point-in-time from price history.
"""
from __future__ import annotations

import io
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.basename(HERE) == "early" else HERE
sys.path.insert(0, ROOT)
import stock_registry as registry

OUTDIR = os.path.join(HERE, "screen")
UNIVERSE_OUT = os.path.join(OUTDIR, "constraint_universe.txt")
REPORT_OUT = os.path.join(OUTDIR, "constraint_report.json")
EXCLUDE_FILE = os.path.join(OUTDIR, "exclude_holdings.txt")  # optional, one NSE symbol per line

# ---- Constraint parameters. Change these deliberately; they are not tuned. ----
MIN_MCAP_CR = 1000.0        # governance / microcap risk floor
MIN_TURNOVER_CR = 5.0       # tradeability floor for a family-sized portfolio
TURNOVER_WINDOW = 60        # sessions used for the median turnover estimate
BATCH = 40

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"),
    "Accept": "text/csv,application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/all-reports",
    "Connection": "keep-alive",
}

BHAV_PATTERNS = [
    "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{stamp}.csv",
    "https://archives.nseindia.com/products/content/sec_bhavdata_full_{stamp}.csv",
]
# NSE publishes surveillance lists through its report API. Endpoints have moved
# before, so each is tried in turn and total failure aborts the run.
ASM_ENDPOINTS = [
    "https://www.nseindia.com/api/reportASM",
]
GSM_ENDPOINTS = [
    "https://www.nseindia.com/api/reportGSM",
]


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    for url in ("https://www.nseindia.com/", "https://www.nseindia.com/all-reports"):
        try:
            s.get(url, timeout=20)
        except requests.RequestException:
            pass
    return s


def fetch_latest_bhav(s: requests.Session, lookback_days: int = 10):
    """Most recent sec_bhavdata_full. Walks back until one parses."""
    day = pd.Timestamp.now(tz="Asia/Kolkata").normalize()
    for _ in range(lookback_days):
        stamp = day.strftime("%d%m%Y")
        for pat in BHAV_PATTERNS:
            try:
                r = s.get(pat.format(stamp=stamp), timeout=30)
                if r.status_code == 200 and len(r.content) > 5000:
                    df = pd.read_csv(io.StringIO(r.text))
                    df.columns = [c.strip().upper() for c in df.columns]
                    if {"SYMBOL", "SERIES"}.issubset(df.columns):
                        df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip().str.upper()
                        df["SERIES"] = df["SERIES"].astype(str).str.strip().str.upper()
                        return str(day.date()), df
            except (requests.RequestException, ValueError, pd.errors.ParserError):
                continue
        day -= timedelta(days=1)
    return None, None


def _symbols_from_payload(payload) -> set:
    """Pull SYMBOL-ish values out of NSE's nested report JSON without assuming shape."""
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and k.strip().lower() in {"symbol", "symbol_name"}:
                    found.add(v.strip().upper())
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return found


def fetch_surveillance(s: requests.Session, endpoints, label) -> set:
    for url in endpoints:
        try:
            r = s.get(url, timeout=30)
            if r.status_code != 200:
                continue
            syms = _symbols_from_payload(r.json())
            if syms:
                print(f"{label}: {len(syms)} symbols from {url}", flush=True)
                return syms
        except (requests.RequestException, ValueError):
            continue
    return set()


def fetch_prices(tickers):
    out = {}
    tickers = list(dict.fromkeys(tickers))
    for i in range(0, len(tickers), BATCH):
        ch = tickers[i:i + BATCH]
        print(f"prices {i+1}-{i+len(ch)}/{len(tickers)}", flush=True)
        z = yf.download(ch, period="6mo", interval="1d", group_by="ticker",
                        auto_adjust=False, progress=False, threads=True)
        if z is None or z.empty:
            continue
        for t in ch:
            try:
                d = z[t] if isinstance(z.columns, pd.MultiIndex) else z
                d = d.dropna(subset=["Close"])
                if not d.empty:
                    out[t] = d
            except Exception:
                pass
    return out


def median_turnover_cr(df) -> float | None:
    """Median rupee turnover over the recent window, in crore.

    Uses auto_adjust=False so Close is the raw traded price. Turnover is a cash
    quantity - adjusting it for dividends, as the retired V6.2 pipeline did,
    biases the level.
    """
    if df is None or len(df) < TURNOVER_WINDOW:
        return None
    c = df["Close"].to_numpy(float)[-TURNOVER_WINDOW:]
    v = df["Volume"].to_numpy(float)[-TURNOVER_WINDOW:]
    turn = c * v
    turn = turn[np.isfinite(turn)]
    return float(np.median(turn) / 1e7) if len(turn) else None


def load_excludes() -> set:
    if not os.path.exists(EXCLUDE_FILE):
        return set()
    out = set()
    with open(EXCLUDE_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip().upper()
            if line:
                out.add(line)
    return out


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    rows = [r for r in registry.load() if r.get("yahoo", "").endswith(".NS") and r.get("nse")]
    print(f"registry NSE-listed rows: {len(rows)}", flush=True)

    s = session()
    bhav_date, bhav = fetch_latest_bhav(s)
    if bhav is None:
        sys.exit("ABORT: could not fetch sec_bhavdata_full. Refusing to build a "
                 "shortlist without the NSE series check.")
    series_by_symbol = dict(zip(bhav["SYMBOL"], bhav["SERIES"]))
    print(f"bhavcopy {bhav_date}: {len(series_by_symbol)} symbols", flush=True)

    asm = fetch_surveillance(s, ASM_ENDPOINTS, "ASM")
    gsm = fetch_surveillance(s, GSM_ENDPOINTS, "GSM")
    if not asm and not gsm:
        sys.exit("ABORT: neither ASM nor GSM could be fetched. A surveillance filter "
                 "that silently passes everything is worse than none. Fix the endpoint "
                 "or drop a manually downloaded list into screen/ and load it here.")
    surveillance = asm | gsm

    frames = fetch_prices([r["yahoo"] for r in rows])

    kept, dropped = [], []
    excludes = load_excludes()
    for r in rows:
        sym, yah = r["nse"].upper(), r["yahoo"]
        mcap = r.get("mcap", 0.0)
        turn = median_turnover_cr(frames.get(yah))
        series = series_by_symbol.get(sym)

        if mcap < MIN_MCAP_CR:
            reason = f"mcap {mcap:.0f}cr < {MIN_MCAP_CR:.0f}cr"
        elif turn is None:
            reason = "insufficient price history for turnover estimate"
        elif turn < MIN_TURNOVER_CR:
            reason = f"turnover {turn:.2f}cr/day < {MIN_TURNOVER_CR:.2f}cr"
        elif series is None:
            reason = "symbol absent from latest bhavcopy"
        elif series != "EQ":
            reason = f"series {series} (not EQ)"
        elif sym in surveillance:
            reason = "ASM" if sym in asm else "GSM"
        elif sym in excludes:
            reason = "excluded holding"
        else:
            kept.append({"isin": r["isin"], "yahoo": yah, "nse": sym, "name": r.get("name", ""),
                         "sector": r.get("industry_group", "Unknown"),
                         "mcap_cr": round(mcap, 1), "turnover_cr": round(turn, 2)})
            continue
        dropped.append({"nse": sym, "name": r.get("name", ""), "reason": reason})

    kept.sort(key=lambda x: -x["turnover_cr"])
    with open(UNIVERSE_OUT, "w", encoding="utf-8") as f:
        f.write("# Constraint-filtered scan universe. Generated by constraint_layer.py.\n")
        f.write(f"# Built {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
                f"from bhavcopy {bhav_date}.\n")
        f.write(f"# Filters: mcap>={MIN_MCAP_CR:.0f}cr, turnover>={MIN_TURNOVER_CR:.1f}cr/day "
                f"(median of {TURNOVER_WINDOW} sessions), series==EQ, not ASM/GSM.\n")
        f.write("# TODAY-ONLY. Not valid as a historical backtest universe.\n")
        for k in kept:
            f.write(f"{k['isin']},{k['yahoo']}\n")

    report = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bhavcopy_date": bhav_date,
        "parameters": {"min_mcap_cr": MIN_MCAP_CR, "min_turnover_cr": MIN_TURNOVER_CR,
                       "turnover_window_sessions": TURNOVER_WINDOW},
        "surveillance_counts": {"asm": len(asm), "gsm": len(gsm)},
        "funnel": {"registry_nse_listed": len(rows), "kept": len(kept), "dropped": len(dropped)},
        "drop_reasons": dict(Counter(d["reason"].split(" ")[0] for d in dropped).most_common()),
        "sector_counts": dict(Counter(k["sector"] for k in kept).most_common()),
        "kept": kept,
        "dropped": dropped,
        "note": "Constraints only. No predictive claim is made by this file.",
    }
    with open(REPORT_OUT, "w") as f:
        json.dump(report, f, separators=(",", ":"))

    print(f"\n{len(rows)} -> {len(kept)} names", flush=True)
    print("drop reasons:", report["drop_reasons"], flush=True)
    print("written", UNIVERSE_OUT, "and", REPORT_OUT, flush=True)


if __name__ == "__main__":
    main()
