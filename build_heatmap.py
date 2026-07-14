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
import json, math, time, sys, datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf

IST = ZoneInfo("Asia/Kolkata")

HORIZONS = {          # calendar-day lookback per column
    "1D": 1, "1M": 30, "3M": 91, "6M": 182,
    "1Y": 365, "2Y": 730, "3Y": 1095, "5Y": 1825,
}
COLS = ["ltp52", "1D", "1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y"]
CHUNK = 60            # tickers per yfinance batch
SUFFIX = ".NS"        # NSE on Yahoo

# Tijori uses short constituent codes that don't always match the NSE symbol
# Yahoo expects. Map config-ticker -> Yahoo NSE symbol (without .NS suffix).
# Add an entry here whenever a stock is stuck on stale snapshot data because
# "<ticker>.NS" 404s on Yahoo. Verify at finance.yahoo.com/quote/<SYMBOL>.NS
#
# These were surfaced by validate_symbols.py. Each maps a Tijori code to the
# current NSE symbol on Yahoo (renames / brand-vs-legal-name mismatches).
SYMBOL_OVERRIDES = {
    "EMS":         "EMSLIMITED",   # EMS Ltd
    "MACROTECH":   "LODHA",        # Macrotech Developers -> Lodha
    "VIL":         "IDEA",         # Vodafone Idea
    "GAVL":        "GODREJAGRO",   # Godrej Agrovet
    "GMDC":        "GMDCLTD",      # Gujarat Mineral Development Corp
    "CARE":        "CARERATING",   # CARE Ratings
    "SAMMAAN":     "SAMMAANCAP",   # Sammaan Capital (ex-Indiabulls Housing)
    "MUTHOTCAP":   "MUTHOOTCAP",   # Muthoot Capital Services
    "PRISMJOHN":   "PRSMJOHNSN",   # Prism Johnson
    "GALAXY":      "GALAXYSURF",   # Galaxy Surfactants
    "DLINK":       "DLINKINDIA",   # D-Link India
    "EASEMYTRP":   "EASEMYTRIP",   # Easy Trip Planners
    "HAPPISTMND":  "HAPPSTMNDS",   # Happiest Minds Technologies
    "SURAYROSHNI": "SURYAROSNI",   # Surya Roshni (Tijori typo + NSE spelling)
    "DAAWAT":      "LTFOODS",      # LT Foods (Daawat brand)
    "FORBESLTD":   "FORBESCO",     # Forbes & Company
    "WAAREE":      "WAAREEENER",   # Waaree Energies
}

# Constituents that have been DELISTED / MERGED and have no live Yahoo symbol
# any more (insolvency, absorbed into another listed entity). These will stay
# on their bundled snapshot no matter what and are candidates to prune from
# sectors_config.json. Listed here for reference only — not queried differently.
DELISTED = {
    "RELCAPITAL",  # Reliance Capital — insolvency, delisted
    "SREINFRA",    # SREI Infrastructure Finance — insolvency, delisted
    "SICAL",       # Sical Logistics — insolvency
    "JPASSOCIAT",  # Jaiprakash Associates — insolvency, suspended
    "CIGNITITEC",  # Cigniti Technologies — acquired by Coforge, delisted
    "SHRIRAMCIT",  # Shriram City Union — merged into Shriram Finance
    "UJJIVAN",     # Ujjivan Financial — merged into Ujjivan SFB (UJJIVANSFB)
    "EQUITAS",     # Equitas Holdings — merged into Equitas SFB (EQUITASBNK)
    "SANGHIIND",   # Sanghi Industries — merged into Ambuja Cements
    "UDAICEMENT",  # Udaipur Cement — merged into JK Cement
}

SUFFIXES = (".NS", ".BO")     # Yahoo exchanges to try: NSE first, then BSE
STALE_DAYS = 10               # series whose last close is older than this are
                              # treated as unresolved (delisted names on BSE
                              # keep old history; a "1D" between two 2023
                              # closes must not masquerade as live data)

def ybase(t):
    return SYMBOL_OVERRIDES.get(t, t)

def ysymbol(t, suffix=SUFFIX):
    return ybase(t) + suffix

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

def _fetch_pass(tickers, suffix, data):
    """download `tickers` on one exchange suffix; fill resolved series into data"""
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i+CHUNK]
        batch = [ybase(t) + suffix for t in chunk]
        for attempt in range(3):
            try:
                df = yf.download(batch, period="5y", interval="1d",
                                 group_by="ticker", auto_adjust=False,
                                 progress=False, threads=True)
                break
            except Exception as e:
                print(f"  {suffix} batch {i} retry {attempt}: {e}", file=sys.stderr)
                time.sleep(3)
        else:
            continue
        for t in chunk:
            yn = ybase(t) + suffix
            try:
                # yfinance returns MultiIndex columns or flat columns depending
                # on version/input shape — detect, don't guess from batch size
                col = df[yn]["Close"] if isinstance(df.columns, pd.MultiIndex) \
                      else df["Close"]
                col = col.dropna()
                col.index = pd.to_datetime(col.index).tz_localize(None)
                if len(col): data[t] = col
            except Exception:
                pass
        print(f"  {suffix} fetched {min(i+CHUNK,len(tickers))}/{len(tickers)}", file=sys.stderr)
        time.sleep(1)

def fetch(tickers):
    """returns {ticker: pandas Series of Close}. Tries NSE, then BSE for the
    misses (many Tijori small/microcaps are BSE-only)."""
    data = {}
    remaining = list(tickers)
    for suffix in SUFFIXES:
        if not remaining: break
        print(f"exchange {suffix}: {len(remaining)} tickers", file=sys.stderr)
        _fetch_pass(remaining, suffix, data)
        remaining = [t for t in remaining if t not in data]
    return data

def compute_stock(closes, today):
    vals = {k: ret_asof(closes, d, today) for k, d in HORIZONS.items()}
    # 52w high: window can be empty (history that stops >1y ago) and .max()
    # of an empty series is NaN — NaN is truthy, and one NaN in the output
    # makes data.json invalid JSON and blanks the whole page. Guard it.
    hi = last = None
    if len(closes):
        w = closes[closes.index >= pd.Timestamp(today - dt.timedelta(days=365))]
        if len(w):
            m = w.max()
            if not pd.isna(m): hi = float(m)
        if not pd.isna(closes.iloc[-1]): last = float(closes.iloc[-1])
    vals["ltp52"] = (last / hi - 1.0) if (hi and last and hi > 0) else None
    return vals

def wavg(pairs):
    """weighted average over (value, weight) skipping None/non-finite; renormalise"""
    num = den = 0.0
    for v, w in pairs:
        if v is None or w is None: continue
        if not (math.isfinite(v) and math.isfinite(w)): continue
        num += v * w; den += w
    return (num / den) if den > 0 else None

def main():
    cfg = load_config()
    tickers = all_tickers(cfg)
    print(f"tickers: {len(tickers)}", file=sys.stderr)
    prices = fetch(tickers)
    today = dt.datetime.now(IST).date()
    # drop series that stopped trading (delisted names resolved on BSE with
    # years-old history) — they must fall back to snapshot + stale flag, not
    # present ancient closes as live returns
    cutoff = pd.Timestamp(today - dt.timedelta(days=STALE_DAYS))
    dead = [t for t, s in prices.items() if s.index[-1] < cutoff]
    for t in dead: del prices[t]
    if dead:
        print(f"dropped {len(dead)} dead series (last close >{STALE_DAYS}d old): "
              f"{', '.join(sorted(dead)[:15])}{'…' if len(dead) > 15 else ''}", file=sys.stderr)
    missing = [t for t in tickers if t not in prices]
    print(f"resolved {len(prices)}/{len(tickers)}  missing:{len(missing)}", file=sys.stderr)

    stock_vals = {t: compute_stock(prices[t], today) for t in prices}

    out_sectors = []
    for s in cfg:
        up = down = 0
        rows = []
        for st in s["stocks"]:
            v = stock_vals.get(st["ticker"])
            stale = v is None
            if stale:                           # live fetch failed: fall back to snapshot,
                v = st.get("snap", {k: None for k in COLS})  # but flag it as not-live
            d1 = v.get("1D")
            if d1 is not None:
                up += d1 > 0; down += d1 < 0
            rows.append({"name": st["name"], "ticker": st["ticker"],
                         "weight": st["weight"], "stale": stale,
                         "vals": {k: v.get(k) for k in COLS}})
        agg = {}
        for k in COLS:
            agg[k] = wavg([(r["vals"][k], r["weight"]) for r in rows])
        out_sectors.append({"sector": s["sector"], "agg": agg,
                            "up": up, "down": down, "n": len(rows), "stocks": rows})

    data = {
        "generated_at": dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M IST") + " · yfinance EOD/delayed",
        "source": "yfinance",
        "columns": COLS,
        "missing": missing,
        "sectors": out_sectors,
    }
    # allow_nan=False: if a NaN ever slips through the guards above, fail the
    # job loudly here instead of publishing invalid JSON that blanks the site
    json.dump(data, open("data.json", "w"), separators=(",", ":"), allow_nan=False)
    print(f"wrote data.json  ({len(out_sectors)} sectors, {len(missing)} tickers unresolved)")

if __name__ == "__main__":
    main()
