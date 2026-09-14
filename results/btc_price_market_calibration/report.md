# BTC prediction-market calibration backtest

Source: `scripts/run_btc_price_market_calibration.py` / `src/btc_price_market_calibration.py`.
Data: 97 resolved Polymarket "Bitcoin price on `<date>`?" daily range markets,
2026-06-04 to 2026-09-13. Ground truth: BTC-USD hourly spot, independent of
Polymarket's own resolved bucket, so errors are in real dollars.

> **Spot provenance.** This run used Coinbase Exchange BTC-USD hourly
> (yfinance was unreachable; `fetch_spot_series` prefers yfinance and falls
> back, recording which it used in `SPOT_SOURCE_USED` and in the results
> JSON). The headline numbers are within ~1% of the original yfinance run
> at every lead time, so nothing here depends on which series was used.

## Headline numbers

| lead time | n | mean error | MAD | RMSE | naive MAD | beats naive? | 68% CI coverage | Brier | log loss |
|---|---|---|---|---|---|---|---|---|---|
| 144h (6d) | 94 | -$37 | $3,049 | $4,456 | $2,271 | **No** | 74.5% | 0.076 | 0.267 |
| 72h (3d) | 97 | -$213 | $2,070 | $3,088 | $1,772 | **No** | 72.2% | 0.066 | 0.220 |
| 24h (1d) | 97 | -$206 | $1,305 | $1,878 | $991 | **No** | 60.8% | 0.050 | 0.162 |
| 6h | 97 | -$206 | $939 | $1,416 | $658 | **No** | 41.2% | 0.036 | 0.117 |

"naive" = assume price at resolution equals spot price at the lead time
(no forecast at all).

## Four findings

1. **The market's own implied mean never beats "assume nothing changes"** at
   any lead time tested, by 34-43% on MAD. `calibration_plots.png`, left
   panel.

2. **That is not an artifact of how the open tails are reconstructed.** A
   bucketed distribution's *mean* is sensitive to the tail convention; its
   *median* barely is. Scoring both separates "the market is wrong" from
   "our reconstruction is wrong":

   | lead | MAD(mean) | MAD(median) | naive MAD |
   |---|---:|---:|---:|
   | 144h | $3,049 | $3,108 | $2,271 |
   | 72h | $2,070 | $1,995 | $1,772 |
   | 24h | $1,305 | $1,416 | $991 |
   | 6h | $939 | $1,051 | $658 |

   The median is no better -- slightly worse at three of four horizons. The
   central estimate really is worse than spot; the reconstruction is not
   the problem.

3. **The market's whole distribution loses to a trivial random walk**, which
   is the sharper version of finding 1 and the most important result here.
   A point forecast is not what a prediction market is for, so the market's
   distribution is scored with CRPS (in dollars, reducing to |error| for a
   point forecast so all three are comparable) against a baseline that is
   also a distribution: centre on spot at the lead time, width from trailing
   30-day realized volatility scaled to the horizon.

   | lead | CRPS market | CRPS naive point | CRPS random walk | market 68% cov | random-walk 68% cov |
   |---|---:|---:|---:|---:|---:|
   | 144h | $2,366 | $2,271 | **$1,816** | 74.5% | 89.5% |
   | 72h | $1,634 | $1,772 | **$1,353** | 72.2% | 84.8% |
   | 24h | $1,086 | $991 | **$764** | 60.8% | 79.8% |
   | 6h | $864 | $658 | **$481** | 41.2% | 67.4% |

   The random walk is 23-44% better at every horizon, and its interval is
   much better calibrated too -- at the 6h lead it covers 67.4% against a
   nominal 68%, while the market's covers 41.2%. `calibration_plots.png`,
   right panel.

   The market's distribution does beat the naive *point* forecast at 72h
   (as a distribution should when uncertainty is high), but it never beats
   the distributional baseline. On this sample, neither the point estimate
   nor the interval is adding information over free, publicly available
   history.

4. **Low-probability buckets are overpriced (favorite-longshot bias).** The
   ~5%-priced bucket (by far the largest sample, n=667-895 per lead time)
   resolves Yes only 0.8-3.6% of the time:

   | lead | stated | actual | ratio |
   |---|---:|---:|---:|
   | 144h | ~5% | 3.6% | 0.72 |
   | 72h | ~5% | 1.5% | 0.30 |
   | 24h | ~5% | 1.0% | 0.19 |
   | 6h | ~5% | 0.8% | 0.16 |

   These ratios are what `SHRINK_BY_LEAD_HOURS` in the dashboard's
   `aggregation.py` applies, and this re-run reproduces them almost exactly
   on a different spot series. Mid-range bins track the diagonal reasonably;
   the highest bins are too thin (n<25 at some lead times) to trust
   individually.

## What this means for the dashboard

Findings 1-3 say the dashboard's most prominent output -- a point forecast,
with an interval around it -- is on this evidence worse than a baseline
anyone can compute from price history alone. Two honest responses, both
taken:

- The UI leads with what the market is actually good at (finding 4's
  territory: bucket-level probabilities, cross-platform disagreement) and
  de-emphasizes the point estimate, rather than displaying a number this
  backtest says to distrust.
- The calibration corrections derived from finding 4 stay, because that
  finding is about bucket probabilities and is reproducible.

## Caveats (read before building anything on this)

- **One ~3.5-month sample, one regime.** BTC trended over this window. A
  trending market flatters a "no change" baseline less than you might
  think, but a *momentum* baseline was not tested and might do better still.
  Needs a longer or different-regime sample before "the market has no edge
  over history" is treated as stable rather than as one quarter's artifact.
- **The random-walk baseline is fitted on trailing data only** (strictly
  backward-looking 30-day window, no look-ahead), but its volatility
  estimate does benefit from the same low-volatility regime the market was
  operating in.
- **Overlapping/correlated events.** 97 daily events are not 97 independent
  draws -- BTC's path is autocorrelated day to day, so effective sample size
  is smaller than n suggests and standard errors here are optimistic.
- **Polymarket only.** Whether combining Polymarket with Kalshi does better
  than either is a separate question, measured separately in
  `results/aggregate_forecast_backtest/report.md`.
