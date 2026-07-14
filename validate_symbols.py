#!/usr/bin/env python3
"""
Symbol resolvability check.

Reports which config tickers do NOT resolve on Yahoo (after SYMBOL_OVERRIDES).
Those are stuck on stale snapshot data — each needs an entry in
SYMBOL_OVERRIDES in build_heatmap.py (config-ticker -> Yahoo NSE symbol), or
is genuinely delisted.

yfinance silently drops random tickers when a large batch is rate-limited, so
a single pass produces false positives (liquid names like TATAMOTORS showing
as "unresolved"). To avoid that, every ticker that fails the batch pass is
re-checked individually with retries; only tickers that fail BOTH are reported.

Run where there is real internet (your machine, or a GitHub Action):
    python validate_symbols.py
Out: prints a report to stdout and writes unresolved list to
     unresolved_symbols.json
"""
import json, sys, time
import yfinance as yf
from build_heatmap import load_config, all_tickers, ybase, SUFFIXES, CHUNK, DELISTED

def resolved_in_batch(tickers, suffix):
    """return the subset of `tickers` that returned at least one close on `suffix`."""
    ok = set()
    batch = [ybase(t) + suffix for t in tickers]
    try:
        df = yf.download(batch, period="1mo", interval="1d", group_by="ticker",
                         auto_adjust=False, progress=False, threads=True)
    except Exception as e:
        print(f"  batch error: {e}", file=sys.stderr)
        return ok
    import pandas as pd
    for t in tickers:
        yn = ybase(t) + suffix
        try:
            col = (df[yn]["Close"] if isinstance(df.columns, pd.MultiIndex)
                   else df["Close"]).dropna()
            if len(col): ok.add(t)
        except Exception:
            pass
    return ok

def main():
    cfg = load_config()
    tickers = all_tickers(cfg)
    print(f"checking {len(tickers)} unique tickers…", file=sys.stderr)

    exch = {}                       # ticker -> suffix that resolved it
    remaining = list(tickers)

    # pass 1 — batched, per exchange (NSE then BSE)
    for suffix in SUFFIXES:
        for i in range(0, len(remaining), CHUNK):
            for t in resolved_in_batch(remaining[i:i+CHUNK], suffix):
                exch[t] = suffix
            print(f"  pass1 {suffix} {min(i+CHUNK,len(remaining))}/{len(remaining)}", file=sys.stderr)
            time.sleep(1)
        remaining = [t for t in remaining if t not in exch]

    # pass 2 — re-check each survivor alone on both exchanges, with retries,
    # to kill transient rate-limit drops
    print(f"pass1 unresolved: {len(remaining)} — re-checking individually…", file=sys.stderr)
    still = []
    for t in remaining:
        hit = None
        for suffix in SUFFIXES:
            for attempt in range(3):
                if resolved_in_batch([t], suffix):
                    hit = suffix; break
                time.sleep(2)
            if hit: break
        if hit: exch[t] = hit
        else:   still.append(t)
        print(f"  pass2 {t}: {hit or 'UNRESOLVED'}", file=sys.stderr)

    report = [{"ticker": t, "tried": [ybase(t) + s for s in SUFFIXES],
               "delisted": t in DELISTED} for t in still]
    json.dump(report, open("unresolved_symbols.json", "w"), indent=2)

    delisted = [r for r in report if r["delisted"]]
    fixable  = [r for r in report if not r["delisted"]]
    print(f"\nresolved {len(tickers)-len(still)}/{len(tickers)}  "
          f"unresolved:{len(still)}  (known-delisted:{len(delisted)}, "
          f"needs-override:{len(fixable)})")
    for r in fixable:
        print(f"  NEEDS OVERRIDE  {r['ticker']:<14} (tried {', '.join(r['tried'])})")
    for r in delisted:
        print(f"  delisted/merged {r['ticker']:<14}")
    if fixable:
        print("\nFind each NEEDS-OVERRIDE symbol at finance.yahoo.com and add it to "
              "SYMBOL_OVERRIDES in build_heatmap.py.")

if __name__ == "__main__":
    main()
