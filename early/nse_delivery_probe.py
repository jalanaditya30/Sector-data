#!/usr/bin/env python3
"""Throwaway NSE delivery-data feasibility probe.

Scope is intentionally narrow:
- 30 scattered trading dates across 2023-2026.
- Fetch sec_bhavdata_full_DDMMYYYY.csv from NSE archive endpoints.
- Parse only SYMBOL, SERIES, DELIV_QTY, DELIV_PER.
- Join to this repo's current NSE universe and to Yahoo OHLCV availability by symbol/date.
- Report fetch success, symbol match coverage, blank DELIV_PER, and EQ/BE counts.

No feature engineering. No forward returns. No signal construction. No pipeline.
"""
from __future__ import annotations

import io
import json
import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UNIVERSE_PATH = os.path.join(ROOT, "trend", "universe.txt")
OUT = os.path.join(HERE, "nse_delivery_probe_result.json")

# Intentionally predeclared and scattered. Chosen as ordinary weekdays; any NSE
# holiday/missing report is counted as a failed/missing probe day rather than replaced.
PROBE_DATES = [
    "2023-01-16","2023-02-15","2023-04-18","2023-06-14","2023-08-17","2023-10-16","2023-11-20","2023-12-18",
    "2024-01-18","2024-03-14","2024-05-16","2024-07-18","2024-09-19","2024-11-18","2024-12-19",
    "2025-01-16","2025-03-17","2025-05-19","2025-07-17","2025-09-18","2025-11-17","2025-12-18",
    "2026-01-19","2026-03-19","2026-05-18","2026-06-18","2026-07-20","2026-08-17","2026-08-20","2026-09-03",
]

ARCHIVE_PATTERNS = [
    "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{stamp}.csv",
    "https://archives.nseindia.com/products/content/sec_bhavdata_full_{stamp}.csv",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "text/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/all-reports",
    "Connection": "keep-alive",
}


def load_symbols() -> list[str]:
    out=[]
    with open(UNIVERSE_PATH, encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if not line or line.startswith("#"): continue
            parts=[x.strip() for x in line.split(",")]
            if len(parts)<2: continue
            y=parts[1]
            if y.endswith(".NS"):
                out.append(y[:-3].upper())
    return sorted(set(out))


def session() -> requests.Session:
    s=requests.Session(); s.headers.update(HEADERS)
    # Prime NSE cookies. Failure here is not fatal; archive host may still permit access.
    try:
        s.get("https://www.nseindia.com/", timeout=15)
        s.get("https://www.nseindia.com/all-reports", timeout=15)
    except requests.RequestException:
        pass
    return s


def fetch_day(s: requests.Session, day: str) -> tuple[pd.DataFrame|None, dict]:
    stamp=pd.Timestamp(day).strftime("%d%m%Y")
    attempts=[]
    for pattern in ARCHIVE_PATTERNS:
        url=pattern.format(stamp=stamp)
        try:
            r=s.get(url, timeout=25, allow_redirects=True)
            attempts.append({"url":url,"status":r.status_code,"bytes":len(r.content),"content_type":r.headers.get("content-type")})
            if r.status_code != 200 or len(r.content) < 100:
                continue
            text=r.content.decode("utf-8-sig", errors="replace")
            df=pd.read_csv(io.StringIO(text), skipinitialspace=True)
            df.columns=[str(c).strip().upper() for c in df.columns]
            need={"SYMBOL","SERIES","DELIV_QTY","DELIV_PER"}
            if not need.issubset(set(df.columns)):
                attempts[-1]["parse_error"]="required columns missing: "+str(sorted(need-set(df.columns)))
                continue
            z=df[["SYMBOL","SERIES","DELIV_QTY","DELIV_PER"]].copy()
            z["SYMBOL"]=z["SYMBOL"].astype(str).str.strip().str.upper()
            z["SERIES"]=z["SERIES"].astype(str).str.strip().str.upper()
            z["DELIV_QTY"]=pd.to_numeric(z["DELIV_QTY"], errors="coerce")
            z["DELIV_PER"]=pd.to_numeric(z["DELIV_PER"], errors="coerce")
            return z, {"ok":True,"source_url":url,"attempts":attempts}
        except Exception as e:
            attempts.append({"url":url,"error":f"{type(e).__name__}: {e}"})
        time.sleep(0.2)
    return None, {"ok":False,"attempts":attempts}


def fetch_ohlcv_presence(symbols: list[str]) -> set[tuple[str,str]]:
    # Feasibility join only. We fetch once for the date span and retain only whether
    # an OHLCV row exists on each probe date. No returns are calculated.
    start=(pd.Timestamp(min(PROBE_DATES))-pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    end=(pd.Timestamp(max(PROBE_DATES))+pd.Timedelta(days=4)).strftime("%Y-%m-%d")
    wanted=set(PROBE_DATES); present=set()
    batch=60
    tickers=[s+".NS" for s in symbols]
    for i in range(0,len(tickers),batch):
        chunk=tickers[i:i+batch]
        print(f"Yahoo presence {i+1}-{i+len(chunk)}/{len(tickers)}", flush=True)
        try:
            z=yf.download(chunk,start=start,end=end,interval="1d",group_by="ticker",auto_adjust=False,progress=False,threads=True)
        except Exception:
            continue
        if z is None or z.empty: continue
        for t in chunk:
            try:
                d=z[t] if isinstance(z.columns,pd.MultiIndex) else z
                d=d.dropna(subset=["Close"])
                sym=t[:-3]
                for idx in d.index:
                    ds=str(pd.Timestamp(idx).date())
                    if ds in wanted: present.add((ds,sym))
            except Exception:
                pass
    return present


def main():
    symbols=load_symbols(); universe=set(symbols)
    print(f"Universe symbols: {len(symbols)}", flush=True)
    s=session()
    daily=[]; matched_pairs=set(); nse_pairs=set(); blank_total=0; eq_total=0; be_total=0
    fetched_frames={}
    for i,day in enumerate(PROBE_DATES,1):
        print(f"NSE {i:02d}/30 {day}", flush=True)
        df,meta=fetch_day(s,day)
        row={"date":day,**meta}
        if df is not None:
            fetched_frames[day]=df
            df=df.copy(); df["in_universe"]=df["SYMBOL"].isin(universe)
            m=df[df["in_universe"]]
            row.update({
                "rows":int(len(df)),
                "matched_universe_symbols":int(m["SYMBOL"].nunique()),
                "blank_deliv_per_rows":int(df["DELIV_PER"].isna().sum()),
                "blank_deliv_per_matched_rows":int(m["DELIV_PER"].isna().sum()),
                "eq_rows":int((df["SERIES"]=="EQ").sum()),
                "be_rows":int((df["SERIES"]=="BE").sum()),
                "other_series_rows":int((~df["SERIES"].isin(["EQ","BE"])).sum()),
            })
            blank_total += row["blank_deliv_per_rows"]; eq_total += row["eq_rows"]; be_total += row["be_rows"]
            for sym in m["SYMBOL"].unique(): matched_pairs.add((day,sym))
            for sym in df["SYMBOL"].unique(): nse_pairs.add((day,sym))
        daily.append(row)

    # Only after NSE fetches: availability join to the existing Yahoo symbol/date convention.
    ohlcv_presence=fetch_ohlcv_presence(symbols)
    joined_pairs=matched_pairs & ohlcv_presence
    fetched_days=[d for d in daily if d.get("ok")]
    distinct_matched={sym for _,sym in matched_pairs}
    distinct_joined={sym for _,sym in joined_pairs}

    payload={
        "generated":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "probe_only":True,
        "explicit_non_goals":["no pipeline","no feature engineering","no returns","no signal testing"],
        "runner_context":os.environ.get("GITHUB_ACTIONS","false"),
        "universe_symbols":len(symbols),
        "probe_dates":PROBE_DATES,
        "summary":{
            "days_requested":len(PROBE_DATES),
            "days_fetched_successfully":len(fetched_days),
            "days_failed_or_malformed":len(PROBE_DATES)-len(fetched_days),
            "distinct_universe_symbols_seen_in_nse_files":len(distinct_matched),
            "symbol_match_fraction_of_current_universe":round(len(distinct_matched)/max(len(symbols),1),6),
            "distinct_symbols_joining_cleanly_to_yahoo_ohlcv_on_same_probe_dates":len(distinct_joined),
            "same_date_symbol_pairs_nse":len(matched_pairs),
            "same_date_symbol_pairs_joined_to_ohlcv":len(joined_pairs),
            "same_date_pair_join_rate":round(len(joined_pairs)/max(len(matched_pairs),1),6),
            "blank_deliv_per_rows_all_fetched_files":blank_total,
            "eq_rows_all_fetched_files":eq_total,
            "be_rows_all_fetched_files":be_total,
        },
        "daily":daily,
        "verdict":"PASS if GitHub Actions can fetch a substantial majority of days with parseable delivery fields and high symbol/date join coverage; otherwise FAIL and do not build ingestion pipeline."
    }
    with open(OUT,"w",encoding="utf-8") as f: json.dump(payload,f,indent=2)
    print(json.dumps(payload["summary"],indent=2),flush=True)

if __name__=="__main__": main()
