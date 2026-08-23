# Quiet Climbers

Finds stocks that rise **a little, most days, for weeks**.

Lives in `quiet/` so it does not collide with the sectoral heatmap at the repo root or
the trend scanner in `trend/`. Separate script, separate JSON, separate workflow — the
only things shared are the ISIN registry (`../stocks.csv`) and the board switcher.

Live at: https://jalanaditya30.github.io/Sector-data/quiet/

Scans **every listed company** with a tradable listing — 1,989 names in
`universe.txt`, generated from the repo-root ISIN registry `../stocks.csv`.
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
steps, a real climb. The other had one 15% day and 29 days of nothing — an event.
Same return. Only the first is what you are looking for.

That is it. No regressions, no log scales, no statistics.

## Verdicts

| verdict | means |
|---|---|
| `quiet climb` | 20+ up days out of 30, positive, no single day above 5% |
| `quiet slide` | the same in reverse |
| `one big day` | a single session moved more than 5% — an event, not a climb |
| `no pattern` | neither |

## Columns

`up days` sessions closing higher · `best run` longest unbroken up streak ·
`30d %` total move · `typical day` median daily move ignoring direction — under
about 1% is the quiet kind · `biggest day` largest single move · `last 5` up days
in the most recent week, to catch one rolling over · `turnover cr` median daily
traded value in Rs crore.

## Why 30 sessions

The objective is stocks trending over a long period. A 10-session window is two
weeks and cannot see one. 30 sessions is about six calendar weeks. Raise `DAYS` to
60 if you want a longer view — it is one constant.

## Knobs (trend_scan.py)

`DAYS` 30 — window length
`STRONG_UP` 20 — up days needed to call it a climb
`QUIET_DAY` 5.0 — a single move above this makes it an event
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
