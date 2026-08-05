# Trend Scanner

Lives in `trend/` so it does not collide with the sectoral heatmap at the repo root.
Shares nothing with it — separate script, separate JSON, separate workflow.

Live at: https://jalanaditya30.github.io/Sector-data/trend/

## Score

    drift = OLS slope of ln(close) on session number, x252x100   (annualised, log terms)
    eff   = efficiency ratio: |net move| / sum(|daily moves|), 0..1
    score = drift x eff^2

Computed over 30, 14 and 5 sessions. Both inputs use a winsorised log path
(daily moves capped at 6%); displayed moves are uncapped.

## Why efficiency ratio, not R-squared

Fed driftless random walks, R-squared reads 0.44 median and clears 0.8 on 14.5% of
trials — and that false-positive rate does not decay with window length (any random
walk regressed on time looks trendy). Efficiency ratio on identical input reads 0.16
median, 0% above 0.8 at n=30.

| n  | R2 noise (med / %>0.8) | ER noise (med / %>0.8) |
|----|------|------|
| 5  | 0.53 / 24.8% | 0.48 / 23.8% |
| 14 | 0.45 / 15.7% | 0.24 / 1.3%  |
| 30 | 0.44 / 14.5% | 0.16 / 0.0%  |
| 60 | 0.43 / 14.5% | 0.11 / 0.0%  |

## Why the 5-session column is dimmed

At n=5 every consistency measure collapses — noise reads ~0.48, a real trend ~0.90.
That is a property of five data points, not a fixable defect. The 5d score is shown
but is **not a sort key**; it feeds only the turn call in `state`.

## States

`trending up/down` — established, confirmed by the 14-session window
`turning up/down` — established, but the 5-session window flipped sign with conviction
`cooling up/down` — the 14-session window flipped against the 30
`choppy` — consistency below 0.45, or no meaningful direction

Gated on consistency, not score magnitude: a shallow but very clean decline is a
trend, and is exactly what a percent-change sort buries.

## Flags (hidden by default)

`GAP` one session moved >15% · `THIN` median turnover <Rs 1cr · `STALE` too many
unchanged closes

## Adding stocks

Edit `universe.txt` only. One NSE ticker per line, Yahoo format (`SYMBOL.NS`).
Blank lines and `#` comments ignored. Unresolved tickers are printed at the end of
the Actions log, never silently dropped.

## Data source

Yahoo Finance via `yfinance` — free, no key, EOD closes adjusted for splits/bonuses.
Not an official feed: occasional bad ticks, Yahoo's NSE symbols sometimes differ from
NSE's own, coverage thins on microcaps. Verify against NSE before acting on a number.

## Knobs (trend_scan.py)

`WINDOWS` [30,14,5] · `RANK_WINDOW` 30 · `WINSOR_PCT` 6.0 · `GAP_FLAG_PCT` 15.0
`MIN_TURNOVER_CR` 1.0 · `TREND_MIN_EFF` 0.45 · `TURN_MIN_SCORE` 25.0 · `BATCH` 40
