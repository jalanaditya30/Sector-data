# NSE Sectoral Heatmap

Self-refreshing clone of the Tijori sectoral view. 80 sectors, 1,473 NSE
constituents, weighted sector aggregates across 1D / 1M / 3M / 6M / 1Y / 2Y / 3Y / 5Y
plus distance-below-52-week-high and up/down breadth.

## The one thing that matters: the data feed

Rebuilding the *view* is easy — the hard part (curated constituent lists) is already
solved in `sectors_config.json`, extracted from your Tijori export with every NSE
symbol intact. What's left is a price feed, and that dictates every design choice:

- **Browser-only won't work.** NSE/BSE endpoints are CORS- and cookie-hostile; a static
  page's `fetch()` gets blocked. So the price pull runs *server-side*, not in the page.
- **This is EOD / ~15-min-delayed, not true tick real-time.** Source is Yahoo Finance
  via `yfinance` (free, reliable enough, covers virtually all these symbols). Good for a
  once-a-day post-close heatmap. If you need live intraday, see "Real-time upgrade" below.

## Files
| file | role |
|---|---|
| `index.html` | the heatmap UI. Renders the embedded snapshot instantly, then live-loads `data.json`. |
| `data.json` | the feed the UI reads. Ships with your snapshot; overwritten by the refresh job. |
| `sectors_config.json` | sector → constituents → NSE ticker + weight. The valuable extracted asset. |
| `build_heatmap.py` | pulls prices, computes returns, re-aggregates to sectors, writes `data.json`. |
| `.github/workflows/refresh.yml` | GitHub Actions cron: rebuild after close, commit `data.json`. |
| `requirements.txt` | `yfinance`, `pandas`. |

## Deploy — pick one

**A. One-off / local (fastest to verify)**
```bash
pip install -r requirements.txt
python build_heatmap.py          # writes fresh data.json (takes a few min for 1,473 tickers)
python -m http.server 8000       # open http://localhost:8000  — must be http://, not file://
```

**B. Auto-refresh on GitHub Pages (recommended — matches your existing GH Pages setup)**
1. Push these files to a repo.
2. Settings → Pages → deploy from `main` / root.
3. Settings → Actions → General → Workflow permissions → **Read and write**.
4. The workflow rebuilds `data.json` every weekday ~15:50 IST and commits it; the live
   site picks it up. Hit **Actions → refresh-heatmap → Run workflow** to trigger now.

**C. Google Sheets alternative (only for a small watchlist)**
`GOOGLEFINANCE("NSE:BALRAMCHIN","high52")` etc. runs server-side (no CORS) and lives in
your existing Sheets workflow — but 1,473 symbols × 8 horizons ≈ 12k live cells will hit
recalc quotas and time out. Viable only if you cut to a ~50–80 stock watchlist. For the
full 80-sector board, use path A/B.

## Caveats (read before trusting a number)
- **Coverage:** a handful of symbols may not resolve on Yahoo (renames, thin small-caps,
  recent listings). Unresolved tickers fall back to your snapshot value and are listed in
  `data.json → missing`. Check that list after the first run.
- **Weekends/holidays:** `data.json` is only as fresh as the last successful run.
- **Aggregation:** sector return = weight-weighted mean of constituent returns, weights
  renormalised over whatever has data for that horizon (so a missing 5Y doesn't blank the
  sector). This mirrors Tijori's approach closely but may diverge a few bps.
- **Adjusted vs raw:** uses raw Close. Dividends/splits are handled by Yahoo's split
  adjustment in `period` history; large special dividends can nudge long-horizon returns.

## Real-time upgrade path
If EOD isn't enough: swap the `fetch()` in `build_heatmap.py` for **Zerodha Kite Connect**
(you likely already trade there). Kite gives real quotes but costs ₹2,000/mo for the API
and rate-limits quote calls — batch by instrument token. Everything downstream
(compute → aggregate → data.json → UI) stays identical.
