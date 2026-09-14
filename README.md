# NSE Sectoral Heatmap

Self-refreshing clone of the Tijori sectoral view, extended to the whole listed
market. 139 sectors, ~3,460 constituent rows, weighted sector aggregates across
1D / 1M / 3M / 6M / 1Y / 2Y / 3Y / 5Y plus distance-below-52-week-high and up/down
breadth.

Every stock on every board is identified by its **ISIN**, from the registry in
[`stocks.csv`](stocks.csv) — **1,861 NSE-listed companies**. Tickers get renamed
(MACROTECH → LODHA), so the ticker is only how a price is fetched; the ISIN is
what a row *is*. The filter box on every board searches it.

The registry was cut to NSE-only: 138 rows were dropped, 128 listed on BSE alone
and 10 on neither. They had no NSE ticker, which meant no usable Screener or
TradingView link and thinner coverage on the price feed.

## The four boards

Each has its own script, data file, page and refresh workflow. What they share is
deliberately small: the ISIN registry (`stocks.csv`) that says which company a row
is, and the board switcher in `boards.css`.

| board | path | what it answers | refresh |
|---|---|---|---|
| **Sectoral Heatmap** | [`/`](https://jalanaditya30.github.io/Sector-data/) | which *sectors* are moving, across 8 horizons | `refresh-heatmap` |
| **Trend Scanner** | [`/trend/`](https://jalanaditya30.github.io/Sector-data/trend/) | which stocks are moving *cleanly* — drift × consistency² over 15 / 10 / 5 sessions. Labels each name **trending up/down · turning · cooling · choppy** | `refresh-trend` |
| **Quiet Climbers** | [`/quiet/`](https://jalanaditya30.github.io/Sector-data/quiet/) | which stocks rise *a little, most days, for weeks* — up-day counts and biggest single day over 30 / 15 / 5 sessions, pure counting. Labels each name **quiet climb · quiet slide · one big day · no pattern** | `refresh-quiet` |
| **Shortlist** | [`/screen/`](https://jalanaditya30.github.io/Sector-data/screen/) | which names are worth reading *this week* — the constrained universe narrowed to a ranked decile, opening on the top 25, with what entered, left and moved since last week | `weekly-shortlist` |

A working set, roughly: the heatmap for sector context, the two stock boards for
pattern reads, the Shortlist for the weekly names.

The two stock boards scan the same universe — **every NSE-listed company** in
`stocks.csv` (1,861 names), written out as `trend/universe.txt` and
`quiet/universe.txt` by `python build_universe.py`. The Shortlist starts from its
own constrained universe (`early/screen/constraint_universe.txt`) and ranks the
top decile on 12-1 momentum.

Every board carries a row of buttons switching between the four, and a
**day/night toggle**. The theme is remembered and applied before first paint, so
no page flashes white on the way in; with nothing chosen it follows the operating
system.

## Kept as a record, not in the nav

Three pages are still served but deliberately left out of the switcher. They are
the research trail, not part of the working set — reachable by URL so that what
was tried, and what failed, stays on the record.

| page | path | what it is | refresh |
|---|---|---|---|
| **Early Momentum Radar** | [`/early/`](https://jalanaditya30.github.io/Sector-data/early/) | the original radar page, superseded by the Shortlist | `refresh-early` |
| **V6.2 Historical Research Artifact** | [`/early/v62.html`](https://jalanaditya30.github.io/Sector-data/early/v62.html) | the 8 Sep retirement page. The pre-committed nuisance-seed robustness gate failed 3 of 4 conditions; the outputs are retained as a research record, not a live signal | none |
| **Behavioral Market Lab** | [`/early/behavioral.html`](https://jalanaditya30.github.io/Sector-data/early/behavioral.html) | standing behavioural measurements | `behavioral-lab` |

**Known gap:** `v62.html` carries a dated header saying what it was and that it is
no longer maintained. `/early/` and `/early/behavioral.html` do not — someone
landing on either has no way to tell they are superseded. Worth either giving them
the same header or retiring them outright; not urgent, but it is a real trap.

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
| `stocks.csv` | **the registry.** One row per listed company, keyed on ISIN: name, NSE symbol, BSE code, Yahoo symbol, industry group, market cap. Every board's identity comes from here. |
| `stock_registry.py` | loads `stocks.csv` and resolves any identifier (ISIN, NSE, BSE, Yahoo symbol) to a company. |
| `build_universe.py` | regenerates `trend/universe.txt` and `quiet/universe.txt` from the registry. |
| `sectors_config.json` | sector → constituents → NSE ticker + weight. The curated Tijori export; left untouched. |
| `build_heatmap.py` | pulls prices, computes returns, aggregates both taxonomies, writes `data.json`. |
| `boards.css`, `theme.js` | the shared board switcher and the day/night toggle, used by every page. |
| `.github/workflows/refresh.yml` | GitHub Actions cron: rebuild after close, commit `data.json`. |
| `screen/index.html` | the Shortlist UI. Reads `early/screen/shortlist.json` and `changes.json`. |
| `early/shortlist.py` | builds the ranked decile from the constrained universe. |
| `early/shortlist_history.py` | archives each week's decile and diffs it against the previous one. |
| `requirements.txt` | `yfinance`, `pandas`. |

## Two taxonomies on the heatmap

The heatmap shows both, labelled per sector and switchable with the
**all / curated / all listed** buttons:

| | sectors | constituents | weights |
|---|---|---|---|
| **curated** | the 80 Tijori sectors from `sectors_config.json` | 1,473 | hand-set in the export |
| **all listed** | 59 NSE industry groups built from `stocks.csv` | 1,861 — every listed company | market capitalisation |

They overlap on purpose: a stock sits in its curated sector *and* in its industry
group, and each sector aggregate is correct within its own taxonomy. Adding a
company to `stocks.csv` is all it takes to put it on the board — the all-listed
sectors are assembled at run time, so nothing has to be regenerated by hand.

## Deploy — pick one

**A. One-off / local (fastest to verify)**
```bash
pip install -r requirements.txt
python build_universe.py         # regenerates both scan universes from stocks.csv
python build_heatmap.py          # writes fresh data.json (2,289 tickers — allow ~20-30 min)
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
  `data.json → missing`. Check that list after the first run. Microcaps resolve less
  reliably than large caps — the wider the universe, the longer that list.
- **Identity gaps:** ~320 curated constituents are codes `stocks.csv` has never seen
  (delisted, merged, or Tijori codes that no longer trade). They still show, with their
  snapshot, but carry no ISIN.
- **The bundled feeds predate ISINs.** `data.json` and both `trend.json` files in the repo
  are the last snapshot from before this change: no ISINs, no all-listed sectors, 752-name
  scans. The pages handle that (the taxonomy filter hides itself, rows fall back to the
  ticker) and the next scheduled refresh replaces them.
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
