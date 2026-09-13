# Have Polymarket traders actually been good at predicting BTC and oil prices?

Source: `scripts/run_prediction_market_trader_skill.py` /
`src/prediction_market_trader_skill.py`. Method: every individual trade
(wallet-attributed, Polymarket's public trade feed) in every resolved
market is scored against what actually happened --
`pnl_per_share = resolution_value - price` (BUY) or `price - resolution_value`
(SELL) -- and a wallet's "edge" is total P&L divided by total notional
traded (a size-normalized return on capital, comparable across traders).
BTC uses the 96 resolved daily "Bitcoin price on `<date>`" range-bucket
events (218,874 trades, 12,030 wallets, 2026-06-04 to 2026-09-12). OIL has
no point-in-time family on Polymarket, so it uses the 16 most recent
resolved "What will WTI hit ...?" touch events instead, with ground truth
from realized WTI (CL=F) daily high/low, not Polymarket's own resolution
(65,740 trades, 16,170 wallets, 2026-06-19 to 2026-09-11).

## The real test: does skill persist out-of-sample?

A raw "best edge this period" leaderboard isn't evidence of skill --
with thousands of wallets, some will look great in any one sample purely
by chance (the same trap as "best fund manager of the year"). The
standard fix: split events chronologically in half, compute each wallet's
edge in period 1, and check whether that predicts their (independent)
period-2 edge.

| | BTC | OIL |
|---|---:|---:|
| split date | 2026-07-25 | 2026-08-01 |
| wallets active both periods (>=3-5 trades each) | 690 | 368 |
| correlation(P1 edge, P2 edge) | **0.286** | 0.002 |
| permutation p-value | **0.001** | 0.79 |
| top-decile P1 edge -> P2 edge | 0.65 -> 1.06 | 5.08 -> 1.07 |
| bottom-decile P1 edge -> P2 edge | -1.23 -> 0.24 | -24.08 -> 19.04 |
| population P2 edge mean | -0.29 | 2.08 |

**BTC: real, statistically significant persistence.** A wallet's edge in
the first half of the sample predicts its edge in the second half
(correlation 0.29, permutation p=0.001 -- essentially never happens under
the shuffle-null). Top-decile-by-P1-edge traders stayed *above* the
population average in P2 (1.06 vs a population mean of -0.29) and above
the bottom decile's P2 performance too. That's the actual signature of
skill: not "someone did well once," but "doing well once predicts doing
well again, on a different set of events."

**OIL: no detectable persistence** (p=0.79, indistinguishable from the
permutation null). Read this as "not detected," not "proven absent" --
see caveats.

## Leaderboards: a real wrinkle, read them carefully

The raw top-10-by-edge tables (full numbers in `trader_skill_results.json`)
are dominated by extreme-longshot payoffs, not necessarily skill: buying a
$0.001 share that resolves Yes gives an "edge" (return on the dollar
risked) of ~999 -- one lucky/informed cheap longshot swamps the ranking.
That's why the persistence test, not the raw leaderboard, is this report's
actual finding -- a wallet's rank on a single-period leaderboard is close
to meaningless on its own; whether that rank *repeats* on an independent
set of events is what's actually diagnostic.

## Caveats

- **OIL's null result is likely underpowered, not necessarily "no skill."**
  16 events (8/8 split) vs BTC's 96 (48/48) is a much smaller, noisier
  sample -- note the wild P1->P2 swings in OIL's decile numbers (bottom
  decile: -24 -> +19), consistent with a few large trades dominating small
  per-wallet samples. This needs more resolved oil touch events before
  concluding oil markets are less predictable than BTC's, rather than just
  under-measured here.
- **Edge is a per-trade metric, not a reconstructed position.** Each trade
  is scored as its own bet rather than netting a wallet's full position in
  a market (see the module docstring for why -- a capped trade-history
  fetch can't guarantee completeness for high-volume markets). This is
  standard and defensible but means a wallet that bought and sold the same
  contract multiple times is scored per-leg, not on its final net
  exposure.
- **No fees, gas, or execution slippage** in the P&L. Real trader returns
  would be somewhat lower after those.
- **OIL's touch-market ground truth (realized WTI daily high/low) is
  measured independently of Polymarket**, unlike BTC's (Polymarket's own
  resolution) -- a deliberate choice (touch markets need "did the actual
  max/min cross this," which Polymarket's own resolution already encodes
  correctly, so this is a consistency choice, not a discrepancy risk) but
  worth knowing the two assets' ground truth comes from different places.
- **One-time snapshot, not a live-updating leaderboard.** Trades are
  cached to disk (`data/raw/trader_skill/`); re-running captures new
  history but this report reflects one run's cutoff.
