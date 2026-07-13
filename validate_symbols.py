#!/usr/bin/env python3
"""
Symbol resolvability check.

Downloads a few recent days for every constituent and reports which
config tickers do NOT resolve on Yahoo (after SYMBOL_OVERRIDES). Those
are the ones stuck on stale snapshot data — each needs an entry added to
SYMBOL_OVERRIDES in build_heatmap.py (config-ticker -> Yahoo NSE symbol).

Run where there is real internet (your machine, or a GitHub Action):
    python validate_symbols.py
Out: prints a report to stdout and writes unresolved list to
     unresolved_symbols.json
"""
import json, sys, time
import yfinance as yf
from build_heatmap import load_config, all_tickers, ysymbol, CHUNK

def main():
    cfg = load_config()
    tickers = all_tickers(cfg)
    print(f"checking {len(tickers)} unique tickers…", file=sys.stderr)
    unresolved = []
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i+CHUNK]
        batch = [ysymbol(t) for t in chunk]
        try:
            df = yf.download(batch, period="5d", interval="1d",
                             group_by="ticker", auto_adjust=False,
                             progress=False, threads=True)
        except Exception as e:
            print(f"  batch {i} error: {e}", file=sys.stderr)
            continue
        for t in chunk:
            yn = ysymbol(t)
            try:
                col = (df[yn]["Close"] if len(batch) > 1 else df["Close"]).dropna()
                ok = len(col) > 0
            except Exception:
                ok = False
            if not ok:
                unresolved.append({"ticker": t, "tried": yn})
        print(f"  {min(i+CHUNK,len(tickers))}/{len(tickers)}", file=sys.stderr)
        time.sleep(1)

    json.dump(unresolved, open("unresolved_symbols.json", "w"), indent=2)
    print(f"\nresolved {len(tickers)-len(unresolved)}/{len(tickers)}  "
          f"unresolved:{len(unresolved)}")
    for u in unresolved:
        print(f"  UNRESOLVED  {u['ticker']:<14} (tried {u['tried']})")
    if unresolved:
        print("\nAdd each of these to SYMBOL_OVERRIDES in build_heatmap.py once you "
              "find the correct Yahoo symbol at finance.yahoo.com.")

if __name__ == "__main__":
    main()
