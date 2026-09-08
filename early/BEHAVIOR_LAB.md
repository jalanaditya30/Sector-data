# Behavioral Market Lab

## Question
Can observable changes in collective market behavior identify stocks before broad recognition/crowding?

This is **not sentiment scoring**. Price/volume are treated as behavioral traces. We do not claim to know who is buying or why.

## Lifecycle
`IGNORED -> BEHAVIOR CHANGE -> ACCUMULATION -> ACCEPTANCE -> RECOGNITION -> FOMO/CROWDING -> EXHAUSTION`

The target is **Behavior Change / Accumulation / early Acceptance**.

## Six independent behavioral families

1. **Attention / Participation** — turnover change, persistence, unusual capital participation.
2. **Acceptance / Anchoring** — repeated higher closes, close location, ability to hold above a prior 120D anchor/high.
3. **Buyer–Seller Control** — where the stock closes on high-participation days; shallow vs deep pullbacks.
4. **Absorption / Supply Exhaustion** — high capital turnover with restrained price response, followed by stronger acceptance.
5. **Commitment / Social Proof** — investors repeatedly transact at progressively higher accepted prices while participation persists; sector breadth provides group confirmation.
6. **Crowding / Exhaustion** — rapid short-term price acceleration, extreme turnover/range expansion, event days. Eventually add external attention acceleration (X/news/search) as a separate dataset.

## Research rules

- No composite Behavioral Score until individual families and interactions are prospectively/historically validated.
- Primary model-selection window: most recent 1–3 years; older observations only for feature warm-up.
- Benchmarks: Nifty Midcap 150 and Nifty Smallcap 250 proxies.
- Evaluate 20/40/60D return, alpha, beat rates, +10/+20/+30% excursions, and drawdown.
- Chronological development/validation/final-holdout splits plus prospective archive.
- Never infer institutional accumulation from OHLCV alone.
- Never tune thresholds from a few attractive charts.
- Keep V2 and V6.2 live systems unchanged while this remains research.

## First hypotheses to test

**H1 Persistent Acceptance:** persistent above-normal turnover + rising accepted prices + strong closes.

**H2 Anchor Release:** capital participation rises as price crosses/holds the prior 120D high; old anchored supply may be absorbed.

**H3 Controlled Accumulation:** persistent turnover + buyer-controlled closes + shallow pullbacks, without event-level price jumps.

**H4 Absorption -> Release:** high turnover / restrained movement first, then price acceptance improves. This requires a sequential/state-transition test, not a same-day score.

**H5 Group Social Proof:** H1/H2/H3 become more effective when sector breadth/median return is strengthening.

**H6 Early vs Crowded:** behavioral strength works best before short-term price/turnover/range acceleration becomes extreme.

## Important next data layers

After OHLCV hypotheses are tested, add independent datasets where reliable: delivery/market participation data, earnings/corporate events, shareholding changes, sector breadth/flows, and external attention acceleration. Social attention should measure *change and crowding*, not generic positive/negative sentiment.
