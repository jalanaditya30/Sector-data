#!/usr/bin/env python3
"""
Refreshes data.json (the sectoral heatmap feed) from live NSE prices.

Data source : Yahoo Finance via yfinance  (server-side, no CORS, free).
              EOD + ~15-min-delayed intraday. NOT true tick real-time.
Constituents: sectors_config.json  (extracted from the Tijori export;
              80 sectors, 1,473 NSE symbols, sector weights).

Run:  python build_heatmap.py
Out:  data.json   (consumed by index.html)
"""
import json, time, sys, datetime as dt
import pandas as pd
import yfinance as yf

HORIZONS = {          # calendar-day lookback per column
    "1D": 1, "1M": 30, "3M": 91, "6M": 182,
    "1Y": 365, "2Y": 730, "3Y": 1095, "5Y": 1825,
}
COLS = ["ltp52", "1D", "1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y"]
CHUNK = 60            # tickers per yfinance batch
SUFFIX = ".NS"        # NSE on Yahoo

def load_config(path="sectors_config.json"):
    return json.load(open(path))

def all_tickers(cfg):
    seen, out = set(), []
    for s in cfg:
        for st in s["stocks"]:
            t = (st["ticker"] or "").strip()
            if t and t not in seen:
                seen.add(t); out.append(t)
    return out

def ret_asof(closes, days_back, today):
    """point-to-point return using nearest available close on/before target date"""
    if closes.empty: return None
    last = closes.iloc[-1]
    if days_back == 1:
        if len(closes) < 2: return None
        prev = closes.iloc[-2]
    else:
        target = today - dt.timedelta(days=days_back)
        sub = closes[closes.index <= pd.Timestamp(target)]
        if sub.empty: return None
        prev = sub.iloc[-1]
    if prev == 0 or pd.isna(prev) or pd.isna(last): return None
    return float(last / prev - 1.0)

def fetch(tickers):
    """returns {ticker: pandas Series of Close indexed by date}"""
    data = {}
    ynames = [t + SUFFIX for t in tickers]
    for i in range(0, len(ynames), CHUNK):
        batch = ynames[i:i+CHUNK]
        for attempt in range(3):
            try:
                df = yf.download(batch, period="5y", interval="1d",
                                 group_by="ticker", auto_adjust=False,
                                 progress=False, threads=True)
                break
            except Exception as e:
                print(f"  batch {i} retry {attempt}: {e}", file=sys.stderr)
                time.sleep(3)
        else:
            continue
        for t in tickers[i:i+CHUNK]:
            yn = t + SUFFIX
            try:
                col = df[yn]["Close"] if len(batch) > 1 else df["Close"]
                col = col.dropna()
                col.index = pd.to_datetime(col.index).tz_localize(None)
                if len(col): data[t] = col
            except Exception:
                pass
        print(f"  fetched {min(i+CHUNK,len(ynames))}/{len(ynames)}", file=sys.stderr)
        time.sleep(1)
    return data

def compute_stock(closes, today):
    vals = {k: ret_asof(closes, d, today) for k, d in HORIZONS.items()}
    hi = float(closes[closes.index >= pd.Timestamp(today - dt.timedelta(days=365))].max()) \
         if len(closes) else None
    last = float(closes.iloc[-1]) if len(closes) else None
    vals["ltp52"] = (last / hi - 1.0) if (hi and last and hi > 0) else None
    return vals

def wavg(pairs):
    """weighted average over (value, weight) skipping None; renormalise"""
    num = den = 0.0
    for v, w in pairs:
        if v is None or w is None: continue
        num += v * w; den += w
    return (num / den) if den > 0 else None

def main():
    cfg = load_config()
    tickers = all_tickers(cfg)
    print(f"tickers: {len(tickers)}", file=sys.stderr)
    prices = fetch(tickers)
    today = dt.date.today()
    missing = [t for t in tickers if t not in prices]
    print(f"resolved {len(prices)}/{len(tickers)}  missing:{len(missing)}", file=sys.stderr)

    stock_vals = {t: compute_stock(prices[t], today) for t in prices}

    out_sectors = []
    for s in cfg:
        up = down = 0
        rows = []
        for st in s["stocks"]:
            v = stock_vals.get(st["ticker"])
            if v is None:                       # keep last-known snapshot if live missing
                v = st.get("snap", {k: None for k in COLS})
            d1 = v.get("1D")
            if d1 is not None:
                up += d1 > 0; down += d1 < 0
            rows.append({"name": st["name"], "ticker": st["ticker"],
                         "weight": st["weight"], "vals": {k: v.get(k) for k in COLS}})
        agg = {}
        for k in COLS:
            agg[k] = wavg([(r["vals"][k], r["weight"]) for r in rows])
        out_sectors.append({"sector": s["sector"], "agg": agg,
                            "up": up, "down": down, "n": len(rows), "stocks": rows})

    data = {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M IST") + " · yfinance EOD/delayed",
        "source": "yfinance",
        "columns": COLS,
        "missing": missing,
        "sectors": out_sectors,
    }
    json.dump(data, open("data.json", "w"), separators=(",", ":"))
    print(f"wrote data.json  ({len(out_sectors)} sectors, {len(missing)} tickers unresolved)")

if __name__ == "__main__":
    main()
