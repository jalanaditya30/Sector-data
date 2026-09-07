# Early Momentum Radar v2

A discovery screen for the transition **ignored → accumulated → momentum**. It is intentionally different from `quiet/` (persistent small up-days) and `trend/` (already-established clean trends).

The key v2 change is philosophical: **a bigger recent move is not automatically a better result**. The radar now tries to rank *earliness* — participation and relative strength arriving while price is still in a reasonable discovery zone — and separates already-exploded/event-driven names.

## What it looks for

Independent evidence families remain visible rather than buried in a black-box model:

1. **Price acceleration** — recent dailyised return is faster than the 20/60-session background.
2. **Turnover acceleration** — median ₹ turnover in the latest 5 sessions / median of the previous 20. The baseline excludes the recent leg.
3. **OBV accumulation** — 20-session normalised OBV slope. `obv_leads_price` appears when OBV reaches a 60-session high before price does.
4. **Market relative strength** — 20-session stock return minus Nifty return.
5. **Sector relative strength** — stock return minus the median 20-session return of its `industry_group` peers.
6. **Trend efficiency** — net log move / total log distance over 20 sessions, with daily steps winsorised at 6%.
7. **Breakout proximity** — price is close to its prior 60-session high.
8. **Lifecycle / extension** — the score explicitly distinguishes early participation from an already-extended or event-driven move.

## Lifecycle

- **Discovery** — preferred early zone: roughly +2% to +15% over 20 sessions, recent price is positive but not explosive, participation is expanding, and several independent confirmations agree.
- **Confirming** — price and participation are agreeing more clearly, but the move is still reasonably early.
- **Watch** — interesting evidence exists but confirmation is incomplete.
- **Momentum** — a genuine move is present but it is no longer the earliest stage.
- **Late / Event** — extension/event behaviour (for example very large 5/20-session move or >15% single-day event); retained for context but deliberately penalised.
- **Review / No setup** — stale or weak/incomplete signals.

The default web view is **Discovery + Confirming**, because the product is intended to reduce the market to the charts most worth reviewing *before* everyone can see the move.

## Score design

`radar_score` is transparent and deliberately heuristic. It rewards:

- moderate turnover expansion more than extreme one-off volume,
- OBV accumulation and especially OBV-leading-price divergence,
- positive price acceleration,
- relative strength vs market and sector,
- improving trend efficiency,
- proximity to a breakout,
- being inside the 20-session discovery return zone.

It penalises:

- event candles,
- already-extended 5/20-session returns,
- stale trading,
- impractically thin turnover.

Liquidity remains a **soft penalty**, not a hard exclusion, so micro/small caps remain visible.

## Universe and fallback

By default `early_scan.py` reads `../trend/universe.txt`, the repository's broad tradable company universe generated from `stocks.csv`.

If Yahoo fails for an NSE ticker and the registry contains a BSE code, v2 automatically attempts the BSE Yahoo symbol (`<code>.BO`). Recovered rows remain the same company/ISIN and carry `fallback=true` so the UI can show that BSE data was used. This specifically reduces silent loss of small/micro-cap candidates from symbol/data-source problems.

## Run

```bash
cd early
python early_scan.py ../trend/universe.txt
```

Output is `early/early.json`. Open `early/index.html` through GitHub Pages. The workflow `refresh-early.yml` rebuilds it automatically.

## Important limitations

- This is a **candidate generator**, not a buy/entry system.
- The thresholds are hypotheses and should be tested prospectively; do not tune them until they perfectly rediscover remembered winners.
- Yahoo Finance can still miss new/renamed/illiquid listings even with the BSE fallback.
- Sector comparison currently uses `industry_group`; a cleaner custom sector taxonomy could materially improve peer-relative strength.
- OBV does not prove institutional accumulation; it is only a price-direction-weighted participation proxy.
- Corporate actions, block trades, surveillance restrictions, upper circuits and illiquidity can distort price/volume signals.
- The correct validation is walk-forward: record what the radar surfaced on each date, then measure subsequent 10/20/40/60-session outcomes rather than judging with hindsight.
