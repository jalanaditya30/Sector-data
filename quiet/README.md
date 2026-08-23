# Quiet Climbers

Finds stocks that rise **a little, most days, for weeks**.

Lives in `quiet/` so it does not collide with the sectoral heatmap at the repo root or
the trend scanner in `trend/`. Separate script, separate JSON, separate workflow — the
only things shared are the ISIN registry (`../stocks.csv`) and the board switcher.

Live at: https://jalanaditya30.github.io/Sector-data/quiet/

Scans **every listed company** with a tradable listing — 1,989 names in
`universe.txt`, generated from the repo-root ISIN registry `../stocks.csv`.
Three windows — **5, 15 and 30 sessions** — are computed for every stock and
switchable on the page.
The `refresh-quiet` GitHub Action rebuilds `trend.json` at least hourly while NSE is
open (two cron slots per hour, offset from `refresh-trend` so the two jobs don't hit
Yahoo in the same minute). Trigger one on demand from
**Actions → refresh-quiet → Run workflow**.

A 10–15% pop is already on Twitter by the time you could act on it. What gets missed
is the stock adding 0.8% a day, most days, for six weeks. That is what this finds.

## The whole logic

Two numbers. Both countable by eye on any chart.

**1. How many of the last 30 sessions closed higher than the day before?**

    29/30  a grind
    23/30  a grind
    16/30  a coin flip that happened to end higher

**2. How big was the biggest single day?**

Two stocks both up 18%. One never moved more than 2.5% on any day — thirty small
steps, a real climb. The other had one 20% day and 29 days of nothing — an event.
Same return. Only the first is what you are looking for.

That is it. No regressions, no log scales, no statistics.

## Window: 5, 15 or 30 sessions

The buttons above the table switch it, and **everything except the volume ratio is
recomputed**: up days, the total move, the verdict, the liquidity figures and the
bar chart. All three windows are computed by the scan and shipped in
`trend.json`, so switching is instant — no refetch.

The up-day bar scales with the window rather than sitting at a fixed 20, which
would be a real bar at 30 sessions and impossible at 15. It is two thirds,
rounded up: **20 of 30, 10 of 15, 4 of 5**.

Treat the 5-session view with suspicion. Four up days out of five happens to pure
noise about one time in six, so that view will show a screen full of "climbs"
that are nothing of the sort. It is there to check whether a longer-window climb
is still going, not to screen on.

## Verdicts

| verdict | means |
|---|---|
| `quiet climb` | two thirds of the window up, positive, no single day above 15% |
| `quiet slide` | the same in reverse |
| `one big day` | a single session moved more than 15% — an event, not a climb |
| `no pattern` | neither |

The event threshold is **15%**, raised from 5%. At 5% it was firing on ordinary
small-cap volatility and throwing away genuine six-week grinds because one session
ran hot. 15% keeps the label for what it was meant to catch: the single jump you
already read about.

## Columns

Every column except `company` has a switch above the table; the choice is
remembered in the browser.

| column | what it is |
|---|---|
| `up days` | sessions in the window closing higher than the day before |
| `Nd %` | total move over the window |
| `vol 5d/30d` | average daily **volume** over the last 5 sessions ÷ over the last 30. 1.00× means the recent leg traded like the month behind it. Always 5-against-30 — comparing the recent leg to the month is the whole point — so it does not follow the window |
| `turnover/mcap` | average daily **turnover** over the window ÷ market cap, as a percent: what share of the company changes hands on a normal day. ₹5cr a day is thin for a ₹5,000cr company and enormous for a ₹50cr one |
| `turnover ₹cr` | the same turnover in rupees, average per session over the window |
| `mcap ₹cr` | market capitalisation (see below) |
| `last 5` | up days in the most recent week, to catch one rolling over. Fixed at 5 — it does not follow the window |
| `verdict` | the label from the table above |
| `last N sessions` | one bar per session, oldest left, height = size of the move |

`best run`, `typical day` and `biggest day` are off the board for now. The scan
still computes all three and ships them in `trend.json`, so putting a column back
is a change to `index.html` alone — no rescan.

## Market cap

From `../stocks.csv`, which holds a cap and the price it was taken at. The share
count those two imply is what does not move day to day, so it is carried forward
on the latest close — the cap tracks the stock instead of freezing at the extract
date. A close more than 5× or less than 0.2× the extract price is treated as a
split or bonus rather than a re-rating, and the stored figure is kept instead:
rolling a cap through a split divides the company by the split factor.

## Why 30 sessions is the default

The objective is stocks trending over a long period. 30 sessions is about six
calendar weeks — long enough for a grind to show and short enough to still be
current. 15 is the check that it is still running; 5 is a glance, not a screen.

## Knobs (trend_scan.py)

`WINDOWS` [30, 15, 5] — every window computed and shipped
`BASE_WINDOW` 30 — sets the chart length, the default sort and the volume baseline
`STRONG_UP_FRAC` 2/3 — share of the window that must close up to call it a climb
`QUIET_DAY` 15.0 — a single move above this makes it an event
`VOL_RECENT` 5 — the "now" leg of the volume ratio
`MIN_TURNOVER_CR` 1.0 — illiquidity floor

## Adding stocks

Edit `../stocks.csv` — the ISIN-keyed registry of every listed company — and run
`python build_universe.py` from the repo root. That rewrites `universe.txt`, one
company per line as `<ISIN>,<Yahoo symbol>`:

    INE002A01018,RELIANCE.NS
    INE0BWS23018,543225.BO

(A bare ticker per line still parses, so an older file keeps working.) Unresolved
tickers are printed at the end of the Actions log, never silently dropped.

## Identity

Rows are keyed on **ISIN**, not ticker: tickers get renamed and the 128 BSE-only
companies never had one. Each row shows the company name with its ticker and ISIN
beneath, and the filter box matches any of the three.

## Data

Yahoo Finance via `yfinance`. EOD closes, adjusted for splits and bonuses. Not an
official feed — verify against NSE before acting on a number.
