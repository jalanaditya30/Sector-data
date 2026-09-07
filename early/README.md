# Early Momentum Radar

A discovery screen for the transition **ignored → accumulated → momentum**. It is intentionally different from `quiet/` (persistent small up-days) and `trend/` (already-established clean trends).

## What it looks for

Seven independent evidence families are shown rather than buried in a black-box score:

1. **Price acceleration** — recent dailyised return is faster than the 20/60-session background.
2. **Turnover acceleration** — median ₹ turnover in the latest 5 sessions / median of the previous 20. This uses traded value, not raw share count, and the baseline excludes the recent leg.
3. **OBV accumulation** — 20-session normalised OBV slope. A special divergence flag appears when OBV reaches a 60-session high before price does.
4. **Market relative strength** — 20-session stock return minus Nifty return.
5. **Sector relative strength** — stock return minus the median 20-session return of its `industry_group` peers.
6. **Trend efficiency** — net log move / total log distance over 20 sessions, with daily steps winsorised at 6% for robustness.
7. **Breakout proximity** — price is within roughly 5% of its prior 60-session high.

The board ranks primarily by the number of confirmations. `radar_score` only adds a small tie-break for turnover and market-relative strength; it is deliberately not a fitted prediction model.

## Stages

- **Strong emerging**: 6+ confirmations
- **Emerging**: 4–5
- **Watch**: 2–3
- **No setup**: 0–1
- **Review**: event-driven (>15% one-day move) or stale-price behaviour; inspect rather than trust mechanically

These thresholds are hypotheses, not truths. The purpose of v1 is to generate a small, explainable review list and collect evidence about which combinations work before optimizing weights.

## Universe and microcaps

By default `early_scan.py` reads `../trend/universe.txt`, the repository's broad tradable company universe generated from `stocks.csv`. This avoids maintaining a second stale universe. The liquidity warning is intentionally permissive at ₹0.25 crore median daily turnover so micro/small caps are not silently discarded. The UI hides very thin names by default but lets you include them.

## Run

```bash
cd early
python early_scan.py ../trend/universe.txt
```

Output is `early/early.json`. Open `early/index.html` through GitHub Pages. The workflow `refresh-early.yml` rebuilds it automatically.

## Important limitations

- This is a **candidate generator**, not an entry/exit system and not investment advice.
- Yahoo Finance can miss/lag symbols, especially renames and very small listings; unresolved names are retained in the JSON metadata.
- Sector comparison currently uses `industry_group` from the repository registry. A better curated sector taxonomy will improve this signal.
- OBV is price-direction-weighted volume; it does not prove institutional buying.
- Corporate actions, block trades, surveillance restrictions, upper circuits and illiquidity can create attractive-looking signals. Event/stale flags exist specifically to force review.
- Do not optimize thresholds against a handful of remembered winners. The next step is walk-forward validation against many winners and non-winners.
