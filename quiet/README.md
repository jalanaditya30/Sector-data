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

## Getting to a stock

Two small buttons stand in front of every company name — **S** for Screener,
**T** for TradingView — so the common jump takes one click straight from the
board. They open in a new tab.

The company name itself opens a dialog on the page: a **candlestick chart of the
last 30 sessions with volume underneath**, the same daily-moves bar chart the row
carries, the window's numbers, and the same two links. Hover any candle for its
open, high, low, close and volume. When the board is on a 5- or 15-session window, the chart still shows
all 30 and shades the ones being scored. Escape, the ×, or a click outside closes it.

Both links are the same shape: a fixed prefix plus this row's symbol.

    https://www.screener.in/company/ASIANTILES/
    https://www.tradingview.com/chart/?symbol=NSE:ASIANTILES

For the 128 companies with no NSE listing, the symbol is the BSE scrip code and
both links carry that instead (`.../company/543225/`, `?symbol=BSE:543225`); the
dialog says so when it happens.

Drawing candles means the scan now ships raw bars — `ohlcv`, one
`[open, high, low, close, volume]` per session, 31 of them so the first session's
move is computable. The daily percent moves are no longer shipped: they are one
division away from consecutive closes, and sending both would be sending the same
numbers twice. `trend.json` grows from roughly 1.6 MB to 4 MB (about 1 MB over the
wire, gzipped).

## Filtering

Four gates, all independent:

- **the verdict chips** at the top are buttons. Click one to show that verdict,
  click again to drop it, pick several at once. `quiet climb` is on by default —
  the same board the old "quiet climbs only" checkbox gave you, which the chips
  replace. "show all" clears the pick. The counts stay live: each chip counts the
  board *before* the verdict pick, so picking one does not zero the other three.
- **the search box** matches company name, ticker or ISIN.
- **hide illiquid** drops anything under ₹1cr median daily turnover.
- **range sliders** — the funnel in a numeric column's header opens a two-handle
  slider already set to that column's full spread. Drag the ends in; the board
  narrows as you drag. The header text still sorts, so the click you already knew
  still does what it did.

  The slider steps through the column's **distinct sorted values**, not evenly
  from lowest to highest. On a straight linear track, market cap would cram every
  company under ₹20,000cr — which is most of them — into the first few pixels,
  and the handle would be useless exactly where the data lives. Stepping through
  the readings themselves spreads the track over the companies instead: every
  stop lands on a real value and both ends are exact.

  A row with no reading for that column is dropped while its slider is narrowed —
  a blank is not a number. Pushing both handles back to the ends clears the
  filter. Hiding a column clears its slider, so nothing filters invisibly, and
  the funnel lights up on any column that is filtering.

## Why the board renders the way it does

Two charts in every row, on a board that can hold 1,800 of them, is a lot of
layout. Three things keep it usable:

- **Both row charts are a handful of SVG `<path>`s**, not one element per
  session. Per-element bars built 170,000 DOM nodes and cost nearly six seconds
  of layout; the paths draw the same picture for a fraction of that. The trade is
  per-session hovering in the row, which the dialog does properly anyway.
- **`table-layout: fixed`** with widths declared in a `<colgroup>` the page
  builds. Automatic layout has to measure every cell in every row before it can
  settle on column widths — that alone was most of the cost.
- **Rows are painted in chunks.** The first screenful goes in synchronously, the
  rest follows a frame at a time, so the board is usable while it fills.

Together: about half a second before the first rows are on screen, against
roughly six before.

## Everything is remembered

The window, which columns are on, the verdict chips, every slider, the sort
column and direction, the search text and the liquidity switch are all saved in
the browser and restored on the next visit — under one key, written on every
change. **reset view** puts the whole board back to defaults, which is the way
out of a filter that has hidden everything.

A slider is remembered by its *values*, not by handle positions, so a range set
today still means the same thing tomorrow after the numbers have moved.

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
| `Nd score` | annualised log drift × consistency², borrowed from the trend scanner. A move that was both **meaningful and tidy**. Squaring consistency is what stops a violent move that happens to end higher from outranking a quiet grind |
| `consistency` | efficiency ratio: net move ÷ total distance travelled, 0–1. **1.00 is a straight line**; a path that wanders to the same place reads far lower. Both it and the score are computed on a winsorised log path — daily steps capped at 6% — so one limit-up session cannot manufacture a trend |
| `vol 5d/30d` | average daily **volume** over the last 5 sessions ÷ over the last 30. 1.00× means the recent leg traded like the month behind it. Always 5-against-30 — comparing the recent leg to the month is the whole point — so it does not follow the window |
| `turnover/mcap` | average daily **turnover** over the window ÷ market cap, as a percent: what share of the company changes hands on a normal day. ₹5cr a day is thin for a ₹5,000cr company and enormous for a ₹50cr one |
| `turnover ₹cr` | the same turnover in rupees, average per session over the window |
| `mcap ₹cr` | market capitalisation (see below) |
| `last 5` | up days in the most recent week, to catch one rolling over. Fixed at 5 — it does not follow the window |
| `verdict` | the label from the table above |
| `last N sessions` | one bar per session, oldest left, height = size of the move |
| `Nd candles` | the same window as candlesticks — open, high, low, close per session. The dialog has the readable version with volume and prices |

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
