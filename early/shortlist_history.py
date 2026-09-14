#!/usr/bin/env python3
"""
shortlist_history.py — archive each weekly shortlist and diff it against the last.

Run immediately after shortlist.py. Reads early/screen/shortlist.json, writes:

    early/screen/history/<generated-date>.json   this week's ranked decile
    early/screen/changes.json                    what entered, left, and moved

The diff is against the most recent *earlier* snapshot, so re-running on the
same day overwrites rather than diffing a list against itself.

Nothing here screens, ranks or decides. It only records what the rule produced
so that next week's list can be compared to this one.
"""

import json
import os
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
SCREEN = os.path.join(HERE, "screen")
HIST = os.path.join(SCREEN, "history")
BIG_MOVE = 15          # rank change worth surfacing


def _load(path):
    with open(path) as f:
        return json.load(f)


def _slim(row):
    return {k: row.get(k) for k in ("nse", "name", "sector", "rank",
                                    "momentum_12_1_pct")}


def main():
    current = _load(os.path.join(SCREEN, "shortlist.json"))
    decile = current.get("decile", [])
    if not decile:
        raise SystemExit("shortlist.json has an empty decile — nothing to archive.")

    stamp = (current.get("generated") or date.today().isoformat())[:10]
    os.makedirs(HIST, exist_ok=True)

    snapshot = {
        "generated": current.get("generated"),
        "rule": current.get("rule"),
        "universe_size": current.get("universe_size"),
        "decile": [_slim(r) for r in decile],
    }
    with open(os.path.join(HIST, "%s.json" % stamp), "w") as f:
        json.dump(snapshot, f, indent=1)

    # most recent snapshot strictly before today's
    earlier = sorted(p for p in os.listdir(HIST)
                     if p.endswith(".json") and p[:-5] < stamp)

    if not earlier:
        changes = {
            "current": stamp, "previous": None, "first_run": True,
            "entered": [], "exited": [], "moved": [],
            "note": "First archived list. Changes appear from the next run.",
        }
    else:
        prev_stamp = earlier[-1][:-5]
        prev = _load(os.path.join(HIST, earlier[-1]))
        prev_rows = {r["nse"]: r for r in prev.get("decile", [])}
        cur_rows = {r["nse"]: _slim(r) for r in decile}

        entered = [cur_rows[k] for k in cur_rows if k not in prev_rows]
        exited = [prev_rows[k] for k in prev_rows if k not in cur_rows]

        moved = []
        for k, row in cur_rows.items():
            if k in prev_rows:
                delta = prev_rows[k]["rank"] - row["rank"]     # +ve = climbed
                if abs(delta) >= BIG_MOVE:
                    m = dict(row)
                    m["prev_rank"] = prev_rows[k]["rank"]
                    m["delta"] = delta
                    moved.append(m)

        changes = {
            "current": stamp,
            "previous": prev_stamp,
            "first_run": False,
            "entered": sorted(entered, key=lambda r: r["rank"]),
            "exited": sorted(exited, key=lambda r: r["rank"]),
            "moved": sorted(moved, key=lambda r: -abs(r["delta"])),
            "big_move_threshold": BIG_MOVE,
        }

    with open(os.path.join(SCREEN, "changes.json"), "w") as f:
        json.dump(changes, f, indent=1)

    print("archived %s (%d names); %d in, %d out vs %s"
          % (stamp, len(decile), len(changes["entered"]),
             len(changes["exited"]), changes["previous"] or "nothing"))


if __name__ == "__main__":
    main()
