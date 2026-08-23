#!/usr/bin/env python3
"""
stock_registry.py — the one place a stock's identity is defined.

Every board in this repo (heatmap, trend scanner, quiet climbers) used to
identify a stock by its NSE ticker. Tickers are not stable identities: they get
renamed (MACROTECH -> LODHA), reused, and a BSE-only company has none at all.
ISIN is: twelve characters, allotted once per security, never reissued.

So `stocks.csv` is keyed on ISIN and everything else — NSE code, BSE code, the
Yahoo symbol used to fetch prices, the industry group used to build sectors —
hangs off it. Tickers are still what Yahoo speaks, so they stay in the file;
they are just no longer the identity.

    stocks.csv columns
      isin            IN########### — primary key, unique across the file
      name            company name
      nse             NSE symbol, blank for BSE-only listings
      bse             BSE scrip code, blank if not listed there
      yahoo           symbol to fetch: "<nse>.NS", else "<bse>.BO", else blank
      industry_group  broad group (used to build the all-listed sectors)
      industry        finer classification
      price           price at extract time, rupees (reference only)
      mcap_cr         market capitalisation, Rs crore (sets sector weights)

Source: the full listed-company extract (1,999 companies, 1,989 of them with a
tradable NSE or BSE listing). Regenerate the boards' universes from it with
`python build_universe.py`.
"""

import csv
import os

REGISTRY_FILE = "stocks.csv"


def registry_path(path: str = REGISTRY_FILE) -> str:
    """Find stocks.csv from the caller's working directory.

    The scanners run inside trend/ and quiet/ (that is how the workflows invoke
    them) while the registry lives at the repo root, so look upwards as well as
    in the current directory rather than making every caller know the layout.
    """
    if os.path.isabs(path):
        return path
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (path, os.path.join(here, path), os.path.join(here, "..", path)):
        if os.path.exists(cand):
            return os.path.normpath(cand)
    return path


def load(path: str = REGISTRY_FILE) -> list:
    """Read stocks.csv into a list of dicts, newest-largest first.

    Rows without an ISIN are dropped: an identity file with no identity in it is
    worse than a short one — it would silently reintroduce ticker-keyed rows.
    """
    with open(registry_path(path), newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("isin") or "").strip()]
    for r in rows:
        for k in list(r):
            r[k] = (r[k] or "").strip()
        r["mcap"] = float(r["mcap_cr"]) if r.get("mcap_cr") else 0.0
    return rows


def by_isin(rows=None) -> dict:
    return {r["isin"]: r for r in (rows if rows is not None else load())}


def by_yahoo(rows=None) -> dict:
    """Yahoo symbol -> row. Only rows that can actually be fetched."""
    return {r["yahoo"]: r for r in (rows if rows is not None else load()) if r["yahoo"]}


def by_nse(rows=None) -> dict:
    """Bare NSE symbol -> row. Only rows that carry one."""
    return {r["nse"]: r for r in (rows if rows is not None else load()) if r["nse"]}


def by_bse(rows=None) -> dict:
    return {r["bse"]: r for r in (rows if rows is not None else load()) if r["bse"]}


def lookup(code: str, rows=None) -> dict | None:
    """Resolve any of the identifiers we hold — ISIN, NSE symbol, BSE code, or a
    Yahoo symbol with its suffix — to a registry row. ISIN wins, then NSE."""
    if not code:
        return None
    rows = rows if rows is not None else load()
    code = code.strip()
    bare = code[:-3] if code.endswith((".NS", ".BO")) else code
    return (by_isin(rows).get(code)
            or by_nse(rows).get(bare)
            or by_bse(rows).get(bare)
            or by_yahoo(rows).get(code))


def scannable(rows=None) -> list:
    """Rows with a Yahoo symbol, i.e. the ones a price scan can cover.

    Ten companies in the extract are unlisted on both exchanges (no NSE symbol,
    no BSE code). They stay in the registry — they are real companies with real
    ISINs — but no price feed can reach them, so they are not scanned.
    """
    return [r for r in (rows if rows is not None else load()) if r["yahoo"]]


def read_universe(path: str) -> list:
    """Parse a universe file into Yahoo symbols.

    Accepts both the ISIN-keyed form written by build_universe.py

        INE002A01018,RELIANCE.NS

    and the bare-ticker form the universes used before ISINs existed

        RELIANCE.NS

    so an older or hand-edited universe file still runs. Blank lines and
    '#' comments are ignored.
    """
    out, seen = [], set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sym = line.split(",")[-1].strip() if "," in line else line
            if sym and sym not in seen:
                seen.add(sym)
                out.append(sym)
    return out
