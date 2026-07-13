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
from build_heatmap import load_config, all_tickers, ysymbol, CHUNK, DELISTED

def resolved_in_batch(tickers):
    """return the subset of `tickers` that returned at least one close."""
    ok = set()
    batch = [ysymbol(t) for t in tickers]
    try:
        df = yf.download(batch, period="1mo", interval="1d", group_by="ticker",
                         auto_adjust=False, progress=False, threads=True)
    except Exception as e:
        print(f"  batch error: {e}", file=sys.stderr)
        return ok
    for t in tickers:
        yn = ysymbol(t)
        try:
            col = (df[yn]["Close"] if len(batch) > 1 else df["Close"]).dropna()
            if len(col): ok.add(t)
        except Exception:
            pass
    return ok

def main():
    cfg = load_config()
    tickers = all_tickers(cfg)
    print(f"checking {len(tickers)} unique tickers…", file=sys.stderr)

    # pass 1 — batched
    resolved = set()
    for i in range(0, len(tickers), CHUNK):
        resolved |= resolved_in_batch(tickers[i:i+CHUNK])
        print(f"  pass1 {min(i+CHUNK,len(tickers))}/{len(tickers)}", file=sys.stderr)
        time.sleep(1)
    suspects = [t for t in tickers if t not in resolved]
    print(f"pass1 unresolved: {len(suspects)} — re-checking individually…", file=sys.stderr)

    # pass 2 — re-check each suspect alone, with retries, to kill transient drops
    still = []
    for t in suspects:
        ok = False
        for attempt in range(3):
            if resolved_in_batch([t]):
                ok = True; break
            time.sleep(2)
        if not ok:
            still.append(t)
        print(f"  pass2 {t}: {'ok' if ok else 'UNRESOLVED'}", file=sys.stderr)

    report = [{"ticker": t, "tried": ysymbol(t),
               "delisted": t in DELISTED} for t in still]
    json.dump(report, open("unresolved_symbols.json", "w"), indent=2)

    delisted = [r for r in report if r["delisted"]]
    fixable  = [r for r in report if not r["delisted"]]
    print(f"\nresolved {len(tickers)-len(still)}/{len(tickers)}  "
          f"unresolved:{len(still)}  (known-delisted:{len(delisted)}, "
          f"needs-override:{len(fixable)})")
    for r in fixable:
        print(f"  NEEDS OVERRIDE  {r['ticker']:<14} (tried {r['tried']})")
    for r in delisted:
        print(f"  delisted/merged {r['ticker']:<14}")
    if fixable:
        print("\nFind each NEEDS-OVERRIDE symbol at finance.yahoo.com and add it to "
              "SYMBOL_OVERRIDES in build_heatmap.py.")

if __name__ == "__main__":
    main()
