# Turning a prediction market into an actual price forecast

Source: `scripts/run_forecast_model.py` / `src/forecast_model.py`.
Shipped as `wisdom-dashboard/backend/model.py`.

Every other study in this repo measures what the market *says*. This one
asks what we should *predict*, which turned out to be a different question
with a much better answer.

The starting position was bad. The calibration backtest found the market's
raw distribution loses to a trivial random walk at every horizon, by
23-44% on CRPS. The aggregate backtest found that blending platforms makes
it worse. Neither result says the market knows nothing. They say the raw
distribution is not a forecast.

**Result: two corrections, no new data source, and the forecast improves
14-35% out of sample and overtakes the random walk at three of four
horizons.**

## Method

97 resolved Polymarket "Bitcoin price on `<date>`" events, four lead times
each (6h / 24h / 72h / 144h), 385 scored cases. Split **chronologically by
date**, so every lead time of a given event lands on the same side and a
test case is never another view of an event the fit has seen. Parameters
are fitted on train; every number below is from held-out test.

The objective is CRPS, the same proper scoring rule the baselines are
judged by. Not coverage, which is trivially gamed by widening, and not MAD,
which ignores the distribution entirely.

Three candidate parameters, applied to a market distribution with median
`m`, given spot `S`:

| | |
|---|---|
| **recentre** `λ` | move the curve so its centre goes from `m` to `S + λ(m − S)` |
| **rescale** `s` | multiply spread about the new centre by `s` |
| **pool** `w` | quantile-average the result with a random walk `N(S, σ_h)` |

All three are shape-preserving: skew and fat tails survive, so if the
market carries information a normal distribution structurally cannot
express, the fit can keep it.

## Finding 1: the market's view of *where* price will be is worth nothing

The fitted `λ` came out at **zero**. Not small: at the boundary of the
grid, on pooled training data, and independently at three of four horizons.

Discarding the market's location and centring on the current spot price is
the single largest improvement in this report:

| lead | market raw | recentred on spot | change |
|---|---:|---:|---:|
| 144h | 2,376 | 2,130 | −10% |
| 72h | 1,650 | 1,518 | −8% |
| 24h | 1,014 | 761 | −25% |
| 6h | 734 | 511 | −30% |

This should not be shocking. For an approximately efficient market spot
*is* the optimal point forecast, and whatever deviation the reconstruction
shows is dominated by stale quotes, bid-ask noise and bucket
discretization. What was shocking is where the fix came from: **the
dashboard already fetched spot on every refresh and used it only to draw
the grey history line behind the fan chart.** The best predictor in the
entire backtest was sitting in the same process, unused.

## Finding 2: a published coverage number was an artifact, and a shipped correction was pushing the wrong way

The calibration backtest reported the market's 68% interval covering the
true price only **41%** of the time at a 6h lead, and
`CI_WIDTH_MULT_BY_LEAD_HOURS` was built on it: intervals widened by up to
**1.79×**.

That measurement computed the interval as the gap between two bucket
**midpoints**. At short horizons the whole distribution collapses into one
or two $2,000 buckets, so that gap is far narrower than anything the
market actually expressed:

| lead | interval width, midpoint convention | actual implied width | coverage, midpoint | coverage, faithful |
|---|---:|---:|---:|---:|
| 144h | $6,000 | $6,756 | 74.5% | 77.7% |
| 72h | $4,000 | $4,788 | 72.2% | 79.4% |
| 24h | $2,000 | $3,068 | 60.8% | 71.1% |
| 6h | $2,000 | $2,365 | **40.2%** | **72.2%** |

The market was never badly overconfident at short horizons. It was mildly
*over*-covered. And once a distribution is correctly centred, the fitted
width goes the other way at every horizon: `s ≈ 0.6–0.65`, a **narrowing**.
The old correction was widening a curve that was already too wide, on the
strength of a number that measured the reconstruction rather than the
market.

Both are fixed: `reconstruct_forecast` now spreads probability across each
bucket's real width, and the width correction is retired in favour of the
fitted `s`.

## Finding 3: pooling with the random walk adds almost nothing

Out of sample the pool weight bought between $0 and $24 of CRPS, inside the
noise. Once the market's curve is centred on spot and narrowed, it is
already close to the random walk, and averaging them again is close to a
no-op. The shipped model therefore has **no random-walk arm at all**, which
means it needs no volatility estimate and no second data source: only the
current spot price.

## The shipped model

    λ = 0.00    centre the distribution on current spot
    s = 0.65    multiply its width by 0.65
                (applied after the longshot shrink, which it composes with)

Held-out test, CRPS in dollars:

| lead | market raw | **model** | random walk | naive spot | vs raw | vs random walk |
|---|---:|---:|---:|---:|---:|---:|
| 144h | 2,376 | **2,039** | 2,028 | 2,473 | −14.2% | +0.5% |
| 72h | 1,650 | **1,404** | 1,440 | 1,786 | −14.9% | **−2.5%** |
| 24h | 1,014 | **717** | 752 | 916 | −29.3% | **−4.6%** |
| 6h | 734 | **479** | 485 | 644 | −34.8% | **−1.3%** |

68% interval coverage against a nominal 68%:

| lead | market raw | model |
|---|---:|---:|
| 144h | 87% | 74% |
| 72h | 86% | 67% |
| 24h | 71% | 73% |
| 6h | 73% | 82% |

The model beats the random walk at three horizons and ties at the longest.
These are narrow wins, 1-5%, and are reported as narrow. But the direction
matters: the raw market **lost** to that baseline by 15-45%, so this is the
difference between a forecast that adds information over free public data
and one that does not.

### Why `s = 0.65` and not the fitted `0.55`

CRPS rewards sharpness, so the CRPS optimum under-covers at long horizons
(61% at 144h against a nominal 68%). The shipped value costs under 0.6% of
CRPS at every horizon and is materially better calibrated:

| scale | 144h CRPS | 72h | 24h | 6h | 144h cov | 72h cov | 24h cov | 6h cov |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.55 | 2,027 | 1,401 | 720 | 478 | 61% | 65% | 69% | 69% |
| **0.65** | **2,039** | **1,404** | **717** | **479** | **74%** | **67%** | **73%** | **82%** |
| 0.75 | 2,063 | 1,417 | 720 | 484 | 80% | 78% | 78% | 84% |
| 1.00 | 2,161 | 1,475 | 742 | 511 | 87% | 90% | 82% | 90% |

The dashboard displays intervals, so interval calibration is worth that
much CRPS.

### What the market actually contributes

The model discards the market's location and rescales its width. What it
keeps is **shape**: skew, fat tails, the lumps a real threshold ladder has
and a normal distribution cannot express. That residual is the whole of the
market's measured value here, and it is worth 1-5% over a random walk.

Worth knowing how fragile that conclusion is to reconstruction. Collapsing
each bucket to its midpoint, the obvious shortcut, makes the market's shape
look *worse* than a normal at every horizon. Reconstructed faithfully it is
better at the short ones. The verdict on whether a market's distribution
carries information is partly a verdict on how carefully you read it.

## Caveats

- **One asset, one platform, one regime.** BTC on Polymarket over ~3.5
  trending months. Both constants are fitted values from that window, not
  laws. They sit at the top of `model.py` to be refitted.
- **97 daily events are nowhere near 97 independent draws.** Overlapping
  horizons on an autocorrelated price path; standard errors are optimistic
  and the 1-5% margins over the random walk are inside what this sample can
  really resolve.
- **Fitted on Polymarket's ~$2,000 buckets, applied to Kalshi's $100
  ladder.** Kalshi's raw distribution is genuinely sharper, and narrowing it
  by a further 35% may over-sharpen. The live dashboard applies the same
  scale to both. Refitting per source needs Kalshi history at these
  horizons, which does not exist (see the aggregate backtest report).
- **`λ = 0` is a statement about this market, not about prediction markets.**
  A market on an event with no continuously-traded underlying has no spot
  price to anchor to, and this whole approach is unavailable there.
- **The model cannot rescue a stale quote.** It inherits whatever the
  adapters deliver.
