#!/usr/bin/env python3
"""
trend_scan.py — finds stocks that grind quietly upward.

The goal: a big pop is already all over Twitter. What gets missed is the stock that
adds 0.8% a day, most days, for weeks. This finds those.

Two numbers do all the work, both of which you can check by eye on a chart:

  up_days      how many sessions in the window closed higher than the day before
  biggest_day  the largest single-day move in the window

A stock up 18% where no day moved more than 2.5% climbed in small steps.
A stock up 18% where one day moved 20% had one event and the rest nothing.
Same return. Only the first is a trend.

No regressions, no log scales, no statistics. Just counting.

Every window is computed for every stock — 30, 15 and 5 sessions — and all three
ship in trend.json, so the page switches between them with no refetch. The
up-day bar scales with the window (two thirds of it), so "most days" means the
same thing at 5 sessions as at 30.

Two readings are borrowed from the trend scanner, which measures the same move a
different way — counting says "most days went up", these say "and it travelled in
a straight line":

  eff (consistency)  efficiency ratio: net move / total distance travelled, 0..1.
                     A stock that goes up 10% in a straight line reads 1.00; one
                     that zigzags to the same place reads 0.3. Preferred over
                     R-squared, which reads a median 0.44 on pure noise and does
                     not improve with a longer window.
  score              annualised log drift x consistency squared. Meaningful *and*
                     tidy. Squaring consistency is what stops a violent move that
                     happens to end higher from outranking a quiet grind.

Both are computed on a winsorised log path (daily steps capped at WINSOR_PCT), so
one limit-up session cannot manufacture a trend. They sit on the same window as
everything else in the row, which is one close wider than the trend board's
version of the same window — that board fits n closes, this one fits the n+1
that bound n sessions of movement.

Two liquidity readings ride along, because a clean climb in something you cannot
buy is not an opportunity:

  vol_ratio    average daily volume over the last 5 sessions / over the last 30.
               Above 1 means more shares are changing hands as the move runs.
               Always 5-against-30 regardless of the window on screen — its whole
               job is to compare the recent leg to the month behind it.
  turn_mcap    average daily turnover over the window / market capitalisation,
               as a percent. What share of the company trades on a normal day —
               comparable across a Rs 500cr microcap and a Rs 5 lakh cr major in
               a way that a raw rupee turnover never is. This one does follow the
               window: it describes the same stretch the rest of the row does.

Identity: rows are keyed on ISIN, read from the repo-root registry `stocks.csv`.
The Yahoo symbol is only how the price is fetched — a rename or a BSE-only
listing must not change which company a row refers to. Market cap comes from the
same registry, carried forward on price (see live_mcap).

TWO files come out of a run, because the board and the dialog need very different
amounts of data:

  trend.json  what the board draws. Every row's numbers plus `daily`, the recent
              percent moves the row's bar chart needs. Small enough to block on.
  bars.json   what the dialog draws: 60 sessions of open/high/low/close/volume
              per stock, held columnar and keyed on ISIN. Several times the size
              of the board file, and needed only once you open a stock — so the
              page fetches it in the background after the board is on screen and
              merges it in when it lands.

Keeping the bars in trend.json would have meant a 7 MB download before the first
row appeared, for a chart most rows never open. The dates are shipped once as a
shared calendar rather than per stock; the handful whose sessions differ (recent
listings, suspensions) carry their own.

Data: Yahoo Finance via yfinance. EOD.
Output: trend.json
"""

import json, math, os, sys
from datetime import datetime, timezone
import numpy as np, pandas as pd, yfinance as yf

# stocks.csv and its loader live at the repo root; this script runs from quiet/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stock_registry as registry  # noqa: E402

WINDOWS = [30, 15, 5]    # every one is computed; the page picks
BASE_WINDOW = 30         # longest scoring window: sets the sort and the vol baseline
CHART_BARS = 60          # sessions of price and volume in the dialog chart
VOL_RECENT = 5           # "now" leg of the volume ratio
FETCH = "6mo"            # comfortably more sessions than CHART_BARS needs
QUIET_DAY = 15.0         # a single move above this is an "event", not a grind
WINSOR_PCT = 6.0         # daily cap applied before fitting score/consistency
STRONG_UP_FRAC = 2 / 3   # share of the window that must close up to call it a grind
MIN_TURNOVER_CR = 1.0
BATCH = 40

UNIVERSE = ["IONEXCHANG.NS","WABAG.NS","JASH.NS","KSB.NS","KIRLOSBROS.NS",
 "HITACHIENERGY.NS","SIEMENS.NS","ABB.NS","CGPOWER.NS","TRANSFORMERS.NS",
 "SANSERA.NS","DYNAMATECH.NS","NRBBEARING.NS","SCHAEFFLER.NS","THYROCARE.NS",
 "LALPATHLAB.NS","METROPOLIS.NS","SOLARA.NS","CAPRIGLOB.NS","NAUKRI.NS",
 "RELIANCE.NS","HDFCBANK.NS","TCS.NS","SUNPHARMA.NS","LT.NS"]


def strong_up(days):
    """Up-days needed to call a window a grind: two thirds of it, rounded up.

    Fixing this at 20 would have meant 20-of-30 (a real bar) but 20-of-15
    (impossible). As a fraction it reads the same at every length: 20/30, 10/15,
    4/5. Five sessions is still a coin flip dressed as a signal — 4-of-5 happens
    to noise about one time in six — which is why the page says so.
    """
    return math.ceil(STRONG_UP_FRAC * days)


def winsorise(log_prices):
    """Rebuild the log path with each daily step capped at +/- WINSOR_PCT.

    Without this a single limit-up session sets the slope and the whole window
    reads as a powerful trend on the strength of one day — exactly the thing the
    "one big day" verdict exists to call out.
    """
    steps = np.diff(log_prices)
    cap = math.log(1 + WINSOR_PCT / 100.0)
    return np.concatenate([[log_prices[0]],
                           log_prices[0] + np.cumsum(np.clip(steps, -cap, cap))])


def drift_and_eff(closes):
    """Annualised log drift, consistency, and the score that combines them.

    drift  slope of ln(price) against session number, x252x100 — a percent per
           year, so a 15-session window and a 30-session one are comparable.
    eff    net move / total distance travelled. 1.00 is a straight line; a path
           that wanders to the same place reads far lower.
    score  drift x eff^2. Squaring is deliberate: a big move that arrived by
           thrashing scores below a smaller one that walked there.
    """
    if len(closes) < 3 or np.any(closes <= 0):
        return None, None, None
    y = winsorise(np.log(closes))
    x = np.arange(len(y), dtype=float)

    slope = float(np.polyfit(x, y, 1)[0])
    drift = max(-2000.0, min(2000.0, slope * 252 * 100.0))

    distance = float(np.abs(np.diff(y)).sum())
    eff = abs(y[-1] - y[0]) / distance if distance > 0 else 0.0
    eff = max(0.0, min(1.0, eff))

    return round(drift, 1), round(eff, 3), round(drift * eff * eff, 1)


def verdict(up_days, total, biggest, days):
    """Plain-English label. Deliberately only four outcomes."""
    if biggest > QUIET_DAY:
        return "one big day"          # an event, not a grind — you already heard about it
    if up_days >= strong_up(days) and total > 0:
        return "quiet climb"          # the thing we are hunting
    if (days - up_days) >= strong_up(days) and total < 0:
        return "quiet slide"
    return "no pattern"


def live_mcap(meta, last_close):
    """Market cap in Rs crore, at today's price.

    stocks.csv holds a market cap and the price it was taken at. The share count
    implied by those two (mcap / price) is what does not move day to day, so
    carrying it forward on the latest close gives a cap that tracks the stock
    instead of freezing at the extract date. Without a usable price we fall back
    to the stored figure and let it be slightly stale rather than wrong.
    """
    try:
        mcap = float(meta.get("mcap_cr") or 0)
        price = float(meta.get("price") or 0)
    except (TypeError, ValueError):
        return None
    if mcap <= 0:
        return None
    if price > 0 and last_close > 0:
        moved = last_close / price
        # A close sitting far from the extract price is more likely a split or a
        # bonus issue than a genuine re-rating, and rolling the cap forward through
        # one would divide the company by the split factor. Outside a plausible
        # band, keep the figure that was true at the extract instead of publishing
        # a confidently wrong one.
        if 0.2 <= moved <= 5.0:
            return mcap * moved
    return mcap


def window_stats(closes, turnover, days, mcap):
    """Everything the board shows for one window length."""
    if len(closes) < days + 1:
        return None
    w = closes[-(days + 1):]
    daily = (w[1:] / w[:-1] - 1) * 100
    total = (w[-1] / w[0] - 1) * 100

    up_days = int((daily > 0).sum())
    biggest = float(np.abs(daily).max())

    # longest run of consecutive up days, and the median daily move ignoring
    # direction. Neither has a column at the moment; both are two lines to compute
    # and would need a rescan to bring back, so they ride along in the JSON.
    best = run = 0
    for v in daily:
        run = run + 1 if v > 0 else 0
        best = max(best, run)

    turn = float(np.mean(turnover[-days:])) if turnover is not None and len(turnover) >= days else None
    drift, eff, score = drift_and_eff(w)

    return {
        "up_days": up_days,
        "days": days,
        "total": round(total, 1),
        "biggest": round(biggest, 1),
        "verdict": verdict(up_days, total, biggest, days),
        "strong_up": strong_up(days),
        "score": score,          # drift x consistency^2, from the trend scanner
        "eff": eff,              # consistency: 0..1, 1.00 is a straight line
        "drift": drift,          # annualised log drift, percent per year
        "turnover_cr": round(turn, 2) if turn is not None else None,
        # turnover as a share of the company, in percent per day
        "turn_mcap": round(turn / mcap * 100, 3) if (turn is not None and mcap) else None,
        "thin": bool(turn is not None and turn < MIN_TURNOVER_CR),
        "typical": round(float(np.median(np.abs(daily))), 2),
        "streak": best,
    }


def analyse(symbol, df, meta=None):
    if df is None or df.empty:
        return None
    df = df.dropna(subset=["Close"])
    if len(df) < BASE_WINDOW + 1:
        return None

    frame = df.iloc[-(BASE_WINDOW + 1):]
    closes = frame["Close"].to_numpy(float)
    chart = df.iloc[-CHART_BARS:]          # longer than the scoring window, for the dialog
    meta = meta or {}
    mcap = live_mcap(meta, float(closes[-1]))

    # Turnover in Rs crore per session, over the whole fetched history so a
    # 30-session window still has 30 sessions of it to average.
    turnover = volume = None
    if "Volume" in df:
        vol = df["Volume"].astype(float)
        turnover = ((df["Close"] * vol).dropna() / 1e7).to_numpy(float)
        volume = vol.dropna().to_numpy(float)

    windows = {}
    for n in WINDOWS:
        st = window_stats(closes, turnover, n, mcap)
        if st is not None:
            windows[str(n)] = st
    if str(BASE_WINDOW) not in windows:
        return None

    # Volume ratio: the recent leg against the month behind it. Averages, not
    # sums — a sum of 5 sessions over a sum of 30 would read 0.17 for a stock
    # trading perfectly evenly, which tells you nothing without dividing it back
    # out. At 1.00 the last week traded like the month; at 2.00, twice as heavily.
    vol_ratio = None
    if volume is not None and len(volume) >= BASE_WINDOW:
        base = float(np.mean(volume[-BASE_WINDOW:]))
        if base > 0:
            vol_ratio = float(np.mean(volume[-VOL_RECENT:])) / base

    daily = (closes[1:] / closes[:-1] - 1) * 100

    # One bar per session for the candlestick chart. Prices to 2dp (paise), volume
    # as a whole number of shares — the JSON is served to a browser and a stock
    # priced at 138.4237 helps nobody.
    bars = chart[["Open", "High", "Low", "Close", "Volume"]] if \
        all(c in chart for c in ("Open", "High", "Low", "Close", "Volume")) else None
    ohlcv = None
    if bars is not None:
        # volume to the nearest hundred shares: the chart reads it as "12.3L", so
        # single shares are noise in the payload
        ohlcv = [[round(float(o), 2), round(float(h), 2), round(float(l), 2),
                  round(float(c), 2), int(round(v / 100.0) * 100) if v == v else 0]
                 for o, h, l, c, v in bars.to_numpy()]

    # BSE-only listings have no NSE symbol — their scrip code is the right short
    # label there, and the exchange field below tells the UI to say so.
    display = (meta.get("nse") or meta.get("bse")
               or meta.get("isin") or symbol.rsplit(".", 1)[0])

    return {
        "isin": meta.get("isin"),          # the identity — stable across renames
        "symbol": display,
        "yahoo": symbol,                   # only how the price was fetched
        "exchange": "BSE" if symbol.endswith(".BO") else "NSE",
        "name": meta.get("name") or display,
        "industry": meta.get("industry_group") or None,
        "mcap_cr": round(mcap, 1) if mcap else None,
        "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "recent_up": int((daily[-VOL_RECENT:] > 0).sum()),
        "windows": windows,
        "last": round(float(closes[-1]), 2),
        # ohlcv and dates are the same length and aligned; the page derives the
        # daily moves from the closes. A row whose feed had no OHLC keeps the old
        # percent series so the board still draws its bar chart.
        "ohlcv": ohlcv,                    # moved to bars.json before writing
        "dates": [d.strftime("%Y-%m-%d") for d in chart.index],
        "_dates": [d.strftime("%Y-%m-%d") for d in chart.index],
        # the board's own bar chart runs on these, so it never waits for bars.json
        "daily": [round(float(v), 2) for v in daily],
    }


def fetch(tickers):
    out = {}
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i+BATCH]
        print(f"  {i+1}-{i+len(chunk)} of {len(tickers)}", flush=True)
        raw = yf.download(chunk, period=FETCH, interval="1d", group_by="ticker",
                          auto_adjust=True, progress=False, threads=True)
        if raw is None or raw.empty:
            continue
        for t in chunk:
            try:
                sub = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                if not sub.dropna(subset=["Close"]).empty:
                    out[t] = sub
            except KeyError:
                pass
    return out


def main():
    universe = UNIVERSE
    if len(sys.argv) > 1:
        universe = registry.read_universe(sys.argv[1])
    print(f"{len(universe)} symbols, windows {WINDOWS} (base {BASE_WINDOW})")

    # Identity — and market cap — come from stocks.csv. A symbol the registry does
    # not know is still scanned; it just carries no ISIN and no cap, and the run
    # says how many.
    try:
        meta = registry.by_yahoo()
        print(f"  registry: {len(meta)} companies with a price symbol")
    except FileNotFoundError:
        meta = {}
        print("  note: stocks.csv not found — rows will carry no ISIN or market cap")
    unknown = sum(1 for t in universe if t not in meta)
    if unknown:
        print(f"  {unknown} symbols not in the registry (no ISIN, no market cap)")

    frames = fetch(universe)
    rows, failed = [], []
    for t in universe:
        r = analyse(t, frames.get(t), meta.get(t))
        (rows.append(r) if r else failed.append(t))

    # sort on the base window: quiet climbs first, then by up-day count, then by
    # size of move. The page re-sorts when you switch windows.
    order = {"quiet climb": 0, "no pattern": 1, "one big day": 2, "quiet slide": 3}
    base = lambda r: r["windows"][str(BASE_WINDOW)]
    rows.sort(key=lambda r: (order[base(r)["verdict"]], -base(r)["up_days"], -base(r)["total"]))

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # --- split the bars out before writing the board file ----------------------
    # the most common calendar wins the shared `dates`; only stocks that traded on
    # different days carry their own
    from collections import Counter
    tally = Counter(tuple(r.pop("dates", []) or []) for r in rows)
    shared = list(tally.most_common(1)[0][0]) if tally else []

    bars = {}
    for r in rows:
        ohlcv = r.pop("ohlcv", None)
        dates = r.pop("_dates", None)
        if not ohlcv:
            continue
        entry = {"o": [b[0] for b in ohlcv], "h": [b[1] for b in ohlcv],
                 "l": [b[2] for b in ohlcv], "c": [b[3] for b in ohlcv],
                 "v": [b[4] for b in ohlcv]}
        if dates and list(dates) != shared:
            entry["d"] = list(dates)
        bars[r["isin"] or r["symbol"]] = entry

    json.dump({"generated": stamp, "chart_bars": CHART_BARS,
               "dates": shared, "bars": bars},
              open("bars.json", "w"), separators=(",", ":"))

    json.dump({"generated": stamp,
               "identity": "isin",         # rows are keyed on ISIN, not ticker
               "windows": WINDOWS, "base_window": BASE_WINDOW,
               "chart_bars": CHART_BARS,
               "vol_recent": VOL_RECENT, "quiet_day": QUIET_DAY,
               "days": BASE_WINDOW,        # legacy key: length of the row bar chart
               "requested": len(universe), "resolved": len(rows),
               "failed": failed, "rows": rows},
              open("trend.json", "w"), separators=(",", ":"))

    print(f"\n{len(rows)} scored, {len(failed)} unresolved")
    if failed:
        print("unresolved: " + ", ".join(failed))
    print(f"\n{'symbol':<15}{'up days':>9}{str(BASE_WINDOW)+'d %':>8}{'biggest':>9}"
          f"{'vol5/30':>9}{'turn/mcap':>11}{'mcap cr':>12}  verdict")
    for r in rows:
        b = base(r)
        vr = f"{r['vol_ratio']:>9.2f}" if r["vol_ratio"] is not None else f"{'-':>9}"
        tm = f"{b['turn_mcap']:>10.3f}%" if b["turn_mcap"] is not None else f"{'-':>11}"
        mc = f"{r['mcap_cr']:>12,.0f}" if r["mcap_cr"] else f"{'-':>12}"
        print(f"{r['symbol']:<15}{str(b['up_days'])+'/'+str(BASE_WINDOW):>9}"
              f"{b['total']:>8.1f}{b['biggest']:>9.1f}{vr}{tm}{mc}  {b['verdict']}"
              f"{'  THIN' if b['thin'] else ''}")


if __name__ == "__main__":
    main()
