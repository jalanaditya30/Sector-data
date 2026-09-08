# R2 Decision Outcome

**Date:** 2026-09-08  
**Outcome:** **RETIRED**

Frozen gate result: failed 3 of 4 conditions.

| Condition | Required | Actual | Result |
|---|---:|---:|---|
| N=20 `r2_median_percentile_in_control` | >= 95 | 19.2 | FAIL |
| N=10 `r2_median_percentile_in_control` | >= 50 | 8.9 | FAIL |
| N=40 `r2_median_percentile_in_control` | >= 50 | 75.3 | PASS |
| N=20 `prob_r2_seed_beats_random_control` | >= 0.60 | 0.217 | FAIL |

Per `early/decision_rule.json`, anything other than all four conditions passing retires R2 as a stock-selection rule. R5 is retired with the V6.2 live candidate path. Historical results and `early/v62_history/` are retained as the research record; no new R2/R5 live signals should be generated.
