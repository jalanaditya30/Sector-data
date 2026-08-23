# Trend Scanner

Lives in `trend/` so it does not collide with the sectoral heatmap at the repo root.
Shares nothing with it — separate script, separate JSON, separate workflow.

Live at: https://jalanaditya30.github.io/Sector-data/trend/

## Score

    drift = OLS slope of ln(close) on session number, x252x100   (annualised, log terms)
    eff   = efficiency ratio: |net move| / sum(|daily moves|), 0..1
    score = drift x eff^2

Computed over **15, 10 and 5 sessions**. Both inputs use a winsorised log path
(daily moves capped at 6%); displayed moves are uncapped.

The **15-session window** is the trend, supplies the consistency figure and is the
sort key. The 10-session window is confirmation only (flags a trend that is cooling).
The 5-session window flags a turn and is never a sort key.

## Why efficiency ratio, not R-squared

Fed driftless random walks, R-squared reads 0.44 median and clears 0.8 on 14.5% of
trials — and that false-positive rate does not decay with window length (any random
walk regressed on time looks trendy). Efficiency ratio on identical input reads 0.16
median, 0% above 0.8 at n=30.

Share of **driftless random walks** (pure noise, 20k trials) that clear the
consistency gate — i.e. the false-positive rate, measured on the current code:

| n  | eff median | % eff > 0.45 | % eff > 0.75 | false trends per 1,989 names @0.45 |
|----|-----|-----|-----|-----|
| 5  | 0.48 | 52.7% | 27.2% | ~1,048 |
| 10 | 0.30 | 29.8% | 6.4%  | ~593 |
| 15 | 0.24 | 18.3% | 1.7%  | ~364 |
| 30 | 0.16 | 5.0%  | 0.1%  | ~99  |

(The rates are per name and do not change with the universe; only the last column
does. It was written for the old 752-name universe — scanning every listed company
scales the count of lucky-looking trends with it, which is the argument for the
tighter gate below, not against the wider scan.)

This is why 15 is the ranking window, and why **`TREND_MIN_EFF` is 0.75**. It was
0.45, calibrated back when the ranking window was 30 sessions. Measured on the
current code with the 0.75 gate, **98.3% of pure noise is correctly called
`choppy`** — only ~1.7% (about 34 of 1,989 names) still earns a trend label by luck,
down from ~364. The cost is that some genuine but untidy trends now read `choppy`;
that is the right trade for a screen whose job is to narrow the list. A clean move
of even 0.3%/day still scores 76 with eff 1.00 and reads `trending up`.

## Why the 5-session column is dimmed

At n=5 every consistency measure collapses — noise reads ~0.48, a real trend ~0.90.
That is a property of five data points, not a fixable defect. The 5d score is shown
but is **not a sort key**; it feeds only the turn call in `state`.

## States

`trending up/down` — established on the 15-session window
`turning up/down` — established, but the 5-session window flipped sign with conviction
`cooling up/down` — 15 sessions still intact, but the 10-session leg has rolled over
`choppy` — consistency below 0.75, or no meaningful direction

Gated on consistency, not score magnitude: a shallow but very clean decline is a
trend, and is exactly what a percent-change sort buries.

## Flags (hidden by default)

`GAP` one session moved >15% · `THIN` median turnover <Rs 1cr · `STALE` too many
unchanged closes

## Universe and identity

`universe.txt` holds **every listed company** with a tradable listing — 1,989
names, 1,861 on NSE and 128 BSE-only. One company per line, `<ISIN>,<Yahoo symbol>`:

    INE002A01018,RELIANCE.NS
    INE0BWS23018,543225.BO

Blank lines and `#` comments are ignored, and a bare ticker per line still parses,
so an older or hand-edited file keeps working. Don't edit it by hand though: it is
generated from the repo-root registry with `python build_universe.py`. To add or
drop a company, edit `../stocks.csv` and re-run that. Unresolved tickers are
printed at the end of the Actions log, never silently dropped.

The registry is also where a row gets its **ISIN**, company name and industry. The
board shows the name (*Sansera Engineering Ltd.*, not *SANSERA*) with the ticker
and ISIN beneath it, and the filter box matches any of the three. The ISIN is the
identity: tickers get renamed and BSE-only listings have none, and neither should
change which company a row refers to. A symbol the registry does not know is still
scanned — it just carries no ISIN, and the run log says how many.

## Refresh cadence

The `refresh-trend` GitHub Action runs **at least hourly while NSE is open**
(two cron slots per hour, 09:20–15:35 IST on weekdays, to survive GitHub's
best-effort scheduler skipping a slot), plus a final run at ~15:55 IST for the
settled close. Intraday, Yahoo's daily bar carries the latest price, so each run
moves `last` and the day's return. Trigger one on demand from
**Actions → refresh-trend → Run workflow**, or POST a `refresh-trend`
`repository_dispatch` from a free external scheduler to guarantee the cadence.

## Data source

Yahoo Finance via `yfinance` — free, no key, EOD closes adjusted for splits/bonuses.
Not an official feed: occasional bad ticks, Yahoo's NSE symbols sometimes differ from
NSE's own, coverage thins on microcaps. Verify against NSE before acting on a number.

## Knobs (trend_scan.py)

`WINDOWS` [15,10,5] · `RANK_WINDOW` 15 · `MID_WINDOW` 10 · `SHORT_WINDOW` 5
`WINSOR_PCT` 6.0 · `GAP_FLAG_PCT` 15.0 · `MIN_TURNOVER_CR` 1.0 · `TREND_MIN_EFF` 0.75
`TURN_MIN_SCORE` 25.0 · `RVOL_RECENT` 5 · `RVOL_BASE` 20 · `BATCH` 40

## Volume

`volume` is **relative volume**: median turnover of the last 5 sessions divided by the
median of the 20 sessions before them. Turnover (price x volume), not share count, so it
is comparable across stocks; medians so one block deal cannot set the level.

Above **1.5x** money is arriving as the trend runs. Below **0.8x** the move is happening
on fading interest — a clean trend on shrinking volume deserves suspicion. This is a
confirmation column, not a filter: it never changes the score or the state.

## Beating Nifty

A stock can trace a flawless straight line and still be doing nothing the index is
not already doing. The **beating nifty** filter (on by default) keeps only moves that
diverge from the benchmark: **ahead** of Nifty for an up-trend, **behind** it for a
down-trend. Hover any `15d %` figure to see that stock's margin over the index.

`rel` in `trend.json` is the stock's 15-session return minus Nifty's over the same
15 sessions. Rows with no benchmark reading are kept rather than dropped, so a failed
index fetch cannot silently empty the board.

Worked example: a stock climbing a clean 0.2%/day for 15 sessions is +3.0% with
consistency 1.00 — it reads `trending up` and ranks well. But if Nifty did +4.5% over
the same stretch, it *lagged the market*, and the filter removes it. That is beta, not
a trend worth acting on.
