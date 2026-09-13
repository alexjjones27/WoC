# BTC prediction-market calibration backtest

Source: `scripts/run_btc_price_market_calibration.py` / `src/btc_price_market_calibration.py`.
Data: 96 resolved Polymarket "Bitcoin price on `<date>`?" daily range markets,
2026-06-04 to 2026-09-12. Ground truth: BTC-USD hourly spot (yfinance),
not Polymarket's own resolved bucket, so errors are in real dollars.

## Headline numbers

| lead time | n | mean error | MAD | RMSE | naive MAD | beats naive? | 68% CI coverage | Brier | log loss |
|---|---|---|---|---|---|---|---|---|---|
| 144h (6d) | 92 | -$73 | $3,080 | $4,497 | $2,283 | **No** | 73.9% | 0.076 | 0.268 |
| 72h (3d) | 95 | -$236 | $2,095 | $3,116 | $1,784 | **No** | 72.6% | 0.066 | 0.221 |
| 24h (1d) | 95 | -$232 | $1,310 | $1,887 | $974 | **No** | 61.1% | 0.051 | 0.163 |
| 6h | 95 | -$217 | $946 | $1,428 | $659 | **No** | 42.1% | 0.037 | 0.119 |

(Log loss penalizes a confident-and-wrong bucket much harder than Brier
does -- included per the standard pair of calibration metrics; it tracks
Brier's shape here, no new finding on its own.)

"naive" = assume price at resolution equals spot price at the lead time
(no forecast at all). "beats naive" = is the market's implied mean closer
to the realized price than that trivial baseline.

## Three findings

1. **The market's own implied mean never beats "assume nothing changes"** at
   any lead time tested, by a wide margin (36-45% worse MAD). See
   `calibration_plots.png`, left panel. This is the single most important
   finding: naively taking the aggregate distribution's mean as a price
   forecast adds no value over spot over this period -- if anything it adds
   noise. (Caveat below on why this isn't necessarily permanent.)

2. **CI coverage is asymmetric by horizon.** The stated 68% interval covers
   the true outcome ~74% of the time at 6 days out (too *wide* / underconfident)
   but only ~42% of the time at 6 hours out (badly too *narrow* /
   overconfident). This is a clean, actionable shape: widen near-dated CIs,
   narrow far-dated ones.

3. **Low-probability buckets are overpriced (favorite-longshot bias, same
   shape as this repo's football/tennis work).** The ~5%-priced bucket (by
   far the largest sample, n=650-880 per lead time) actually resolves Yes
   only 0.8-3.7% of the time -- consistently below the diagonal in
   `calibration_plots.png`, right panel, at every lead time. Mid-range bins
   track the diagonal reasonably; the highest bins are too thin (n<20 at
   some lead times) to trust individually.

## Caveats (read before building anything on this)

- **One ~3.5-month sample, one regime.** BTC trended up over this window,
  which mechanically produces a negative mean-error (market underpredicted
  the rally) and could itself explain why a static "no forecast" baseline
  did well -- a trending market rewards a naive extrapolation-adjacent
  baseline less than you'd think, but a *momentum* baseline might do even
  better than pure "flat," which this test didn't check. This needs a
  longer/different-regime sample (or a down/choppy period) before treating
  "the market has no edge over spot" as a stable property rather than an
  artifact of one trending quarter.
- **Overlapping/correlated events.** 96 daily events aren't 96 independent
  draws -- BTC's path is autocorrelated day to day, so the effective sample
  size for the error/coverage stats is smaller than n=95 suggests. Standard
  errors here are optimistic.
- **Kalshi not included yet.** This is Polymarket only; Kalshi's KXBTCD
  (hourly ladder) would need daily-sampling to stay tractable (see
  wisdom-dashboard/README.md's note on Kalshi's granularity) before it can
  be added to this same backtest.
