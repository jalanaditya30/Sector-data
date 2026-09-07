# Early Momentum Radar

A discovery screen for the transition **ignored → accumulated → momentum**. It is intentionally different from `quiet/` (persistent small up-days) and `trend/` (already-established clean trends).

## What it looks for

Independent evidence families are shown rather than hidden inside a black-box indicator:

1. **Price acceleration** — recent dailyised return versus the 20/60-session background.
2. **Turnover acceleration** — median ₹ turnover in the latest 5 sessions divided by the median of the previous 20.
3. **OBV accumulation** — 20-session normalised OBV slope, including a divergence flag when OBV leads price.
4. **Market relative strength** — stock versus Nifty.
5. **Sector relative strength** — stock versus the median return of its industry group.
6. **Trend efficiency** — net log move divided by total log distance over 20 sessions.
7. **Breakout proximity** — distance from the prior 60-session high.

## Lifecycle

The screen ranks **earliness**, not simply the strongest recent return:

- **Discovery** — participation is arriving while the 20-day move is still moderate.
- **Confirming** — more independent evidence agrees, but the stock is not yet materially extended.
- **Watch** — some evidence exists but the full early setup is not present.
- **Momentum** — the move is established rather than early.
- **Late / Event** — price extension or a large event candle means the stock is no longer treated as early discovery.
- **Review** — stale or otherwise mechanically questionable price behaviour.

Very thin names remain visible because micro/small caps matter, but liquidity is a soft ranking penalty rather than an automatic exclusion.

## Universe and fallback

By default `early_scan.py` reads `../trend/universe.txt`, the repository's broad tradable company universe generated from `stocks.csv`. If an NSE Yahoo ticker fails and the same registry row has a BSE code, the scanner attempts that BSE ticker. A name is counted as `fallback_recovered` only when the fallback data also passes the full history/data-quality analysis.

## Prospective evidence archive

Every completed radar run still overwrites `early.json` for the live board, but it also creates:

`early/history/YYYY-MM-DD.json`

The archive stores compact rows for **Discovery, Confirming and Watch** only. This is deliberate: once a signal has been recorded, later code changes cannot rewrite the date on which the scanner originally found it.

## Forward-performance test

`backtest.py` reads the archive and evaluates the **first Discovery** and **first Confirming** signal for each company. It measures forward returns after:

- 5 trading sessions
- 10 trading sessions
- 20 trading sessions
- 40 trading sessions

For each horizon it records stock return, Nifty return, excess return, sample count, win rate and the proportion that beat Nifty. Results are written to `early/backtest.json`.

A new archive will initially have zero mature 20/40-session observations. That is expected. The purpose is prospective validation, not manufacturing a historical success rate from remembered winners.

## Run

```bash
cd early
python early_scan.py ../trend/universe.txt
python backtest.py
```

The `refresh-early.yml` workflow performs both steps automatically after NSE close and commits `early.json`, the daily history snapshot and `backtest.json`.

## Important limitations

- This is a **candidate generator**, not an entry/exit system or a buy recommendation.
- Yahoo Finance can miss/lag symbols, especially renames and very small listings.
- Sector comparison currently uses `industry_group` from the repository registry.
- OBV does not prove institutional buying.
- Corporate actions, block trades, surveillance restrictions, upper circuits and illiquidity can create attractive-looking signals.
- Do not optimize thresholds against a handful of remembered winners. Change rules only after enough prospective samples exist to show where the model is failing.
