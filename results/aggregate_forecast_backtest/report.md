# Does combining platforms actually help?

Source: `scripts/run_aggregate_forecast_backtest.py` /
`src/aggregate_forecast_backtest.py`.

This is the backtest for the claim the dashboard is built on. Every other
piece of measurement in this repo scores one source at a time. The thesis
that a volume-weighted mixture of platforms beats any single platform had
never been tested. It is tested here, and on this evidence **it does not
hold**.

## Setup

63 resolved BTC events, 2026-07-09 to 2026-09-13, where Polymarket's daily
"Bitcoin price on `<date>`" event and a Kalshi `KXBTCD` ladder both settle
at the **same instant** (16:00:00Z). Both platforms' quotes are
reconstructed at 45/30/15/5 minutes before that instant, run through the
shipped aggregation engine (`wisdom-dashboard/backend/aggregation.py`,
imported rather than reimplemented), and scored against realized BTC price.

Forecasts compared, all of the same 16:00:00Z price:

| | |
|---|---|
| `polymarket` | the Polymarket event's buckets alone |
| `kalshi` | the `KXBTCD` ladder alone, isotonic-fitted and differenced |
| `aggregate` | both, equal weight, through the real engine |
| `naive` | a point mass at spot when the forecast was made |
| `random_walk` | spot, widened by trailing realized volatility |

Scored by CRPS (in dollars; it reduces to |error| for a point forecast, so
all five are comparable), MAD, and 68%-interval coverage.

### Why the window is one hour, and why that is itself a finding

Two sources can only be compared where both quote the same thing at the
same time. For BTC that window is one hour and no wider:

- Polymarket's daily events open exactly 7 days before resolution and all
  resolve at 16:00:00Z.
- Kalshi's `KXBTCD` is a series of **one-hour** markets. The ladder closing
  at 16:00:00Z opens at 15:00:00Z.

At 6h, 24h, 72h and 144h -- the lead times the calibration backtest uses
and where most of the dashboard's cards live -- Polymarket quotes and
Kalshi does not. There is no historical data in which those two platforms
forecast the same multi-day horizon, because Kalshi does not sell that
product for BTC.

That matters for the live dashboard, because `aggregation.py` groups by
**calendar date**. A live two-source card blends Polymarket's 16:00Z
distribution with whichever Kalshi ladder closed latest that day, which can
be a different instant up to eight hours away. This backtest deliberately
pairs the ladder that closes at Polymarket's own resolution instant, so
that it measures aggregation rather than that mismatch. The mismatch is
documented under "Known scope decisions" in the dashboard README.

(No rows at a 60-minute lead: the Kalshi market has only just opened and
has no quote at or before that instant. 34 of 97 Polymarket dates had no
matching Kalshi ladder in the API's history, all before 2026-07-09.)

## Result: the mixture loses to its better input

| lead | n | polymarket | kalshi | **aggregate** | naive | random walk |
|---|---:|---:|---:|---:|---:|---:|
| | | CRPS ($) | CRPS ($) | CRPS ($) | CRPS ($) | CRPS ($) |
| 45m | 63 | 489 | **194** | 300 | 250 | 193 |
| 30m | 63 | 475 | **142** | 259 | 180 | 137 |
| 15m | 63 | 478 | **82** | 229 | 109 | 86 |
| 5m | 63 | 478 | **40** | 201 | 54 | 43 |

The aggregate sits between its two inputs, which is what a mixture does.
The problem is that "between" is the wrong place to be when one input is
several times better than the other:

| lead | n | aggregate CRPS | best single CRPS | aggregate beats best single | beats Polymarket | beats Kalshi |
|---|---:|---:|---:|---:|---:|---:|
| 45m | 63 | 300 | 154 | **9.5%** | 88.9% | 20.6% |
| 30m | 63 | 259 | 120 | **12.7%** | 93.7% | 19.0% |
| 15m | 63 | 229 | 77 | **3.2%** | 95.2% | 6.3% |
| 5m | 63 | 201 | 40 | **3.2%** | 100.0% | 3.2% |

Combining reliably improves on the *worse* source and reliably damages the
*better* one.

## And no fixed blend weight rescues it

The table above uses equal weights. Sweeping the blend across the whole
range -- 0.0 is Kalshi alone, 1.0 is Polymarket alone:

| lead | w=0.00 | w=0.25 | w=0.50 | w=0.75 | w=1.00 |
|---|---:|---:|---:|---:|---:|
| 45m | **196** | 213 | 300 | 458 | 687 |
| 30m | **146** | 165 | 259 | 427 | 668 |
| 15m | **86** | 120 | 229 | 412 | 670 |
| 5m | **43** | 83 | 201 | 396 | 668 |

CRPS is **monotone increasing in Polymarket's share at every lead time**.
The optimum is always the corner: take Kalshi, add nothing. There is no
interior blend that beats the better source, so this is not a statement
about one weighting choice -- it is a statement about mixing these two
sources at this horizon at all.

This is the part that bears directly on the dashboard. Weighting by volume
does not escape the table above; it just picks some point on the same
monotone curve, and **volume carries no information about which end of the
curve is better**. The mixture's premise is that the sources are comparable
in quality and that their errors are partly independent, so averaging
cancels noise. Here they are not comparable, and averaging imports error.

## Why Polymarket does so badly here (and what it does not mean)

Polymarket's CRPS is essentially flat across the whole final hour -- on a
representative date, 180 at 45 minutes out and 179 at 5 minutes out, while
Kalshi's goes 144 → 44. Polymarket's quote barely moves; Kalshi's sharpens
into the close.

That is structural, not a verdict on the platform. A 7-day market with 5
minutes left has most of its price discovery behind it and little reason
for anyone to re-trade it; a 1-hour market with 5 minutes left is almost a
spot quote, which is why Kalshi's numbers converge on the naive and
random-walk baselines (40 vs 54 vs 43 at a 5-minute lead). **This backtest
does not say Polymarket forecasts worse than Kalshi at Polymarket's own
horizon.** It says that at the only horizon where the two overlap, one is
sharp and one is stale, and averaging them is worse than using the sharp
one.

## A second finding, now fixed: the CI correction over-widened at short leads

68% interval coverage (nominal: 68%), **as originally measured**:

| lead | polymarket | kalshi | aggregate | random walk |
|---|---:|---:|---:|---:|
| 45m | 95.2% | 73.0% | 93.7% | 63.5% |
| 30m | 96.8% | 79.4% | 95.2% | 68.3% |
| 15m | 96.8% | 88.9% | 96.8% | 76.2% |
| 5m | 98.4% | 92.1% | 100.0% | 79.4% |

Polymarket's and the aggregate's intervals were far too wide -- 94-100%
coverage where 68% is intended -- and the calibration correction was
causing it. `CI_WIDTH_MULT_BY_LEAD_HOURS` was measured at 6h-144h, and
`_log_interp` clamps below its lowest anchor, so every lead under 6 hours
was getting the 6-hour multiplier of **1.79x**. A forecast 5 minutes from
resolution is nearly a spot quote and has no established need to be
widened at all, let alone by 79%.

`_ci_width_multiplier` now tapers toward 1.0 (no correction) below the
lowest measured anchor instead of clamping. After that change:

| lead | polymarket | kalshi | aggregate | random walk |
|---|---:|---:|---:|---:|
| 45m | 77.8% | 49.2% | 79.4% | 63.5% |
| 30m | 81.0% | 61.9% | 84.1% | 68.3% |
| 15m | 77.8% | 73.0% | 84.1% | 76.2% |
| 5m | 74.6% | 79.4% | 92.1% | 79.4% |

Polymarket and the aggregate improve a lot. Kalshi gets *worse* at the
longer leads (73.0% → 49.2% at 45m): its raw ladder was slightly narrow,
and the erroneous widening had been masking that. So the taper is an
improvement on net and not a fix -- **nothing is properly calibrated below
6 hours, because nothing was measured below 6 hours.** The honest
resolution is to measure sub-6h anchors and interpolate between real
numbers, rather than to pick between two kinds of extrapolation. Until
then the taper is the more defensible default: outside the measured range,
revert toward no correction rather than assert a correction measured on a
different object.

Note this taper is applied to the interval width and deliberately **not**
to the longshot shrink, which keeps its clamp. The two behave differently
as lead time goes to zero: an interval's required widening plausibly
vanishes as the forecast collapses onto spot, whereas a 5c contract
minutes from expiry is if anything *less* likely to pay off than the 6h
measurement says. Tapering that toward "no correction" would be the
unjustified extrapolation.

CRPS is unaffected by any of this -- the multiplier rescales the reported
intervals, not the density the CRPS is computed from -- so the headline
result above stands unchanged either way.

## What this changes

- The dashboard still shows per-platform breakdowns and a disagreement
  score, which is the part of multi-source coverage this backtest does not
  undermine: seeing that two platforms disagree is useful even when
  averaging them is not.
- It should not be inferred from a combined card that combining improved
  the forecast. On this evidence, at this horizon, it did not.
- Before weighting is changed in response to this, note what the fix would
  require: a per-source, per-horizon estimate of forecast *quality*, not
  liquidity. That is a real piece of work, and it needs a wider overlap
  window than this data provides.

## Caveats

- **One hour, one asset, one pair of platforms.** Everything here is
  measured between 5 and 45 minutes before resolution on BTC. It says
  nothing directly about the multi-day cards, which is most of the
  dashboard, because no data exists in which both platforms quote those
  horizons.
- **63 events over ~9 weeks**, and consecutive daily events on an
  autocorrelated price path are not independent draws.
- **Equal weights are deliberate** in the headline table, to isolate the
  aggregation math from a volume figure the historical APIs do not expose
  per minute. The blend sweep is what generalizes past that choice.
- **Polymarket minute data is refetched at `fidelity=1`** for the final
  hour rather than reusing the calibration cache's hourly series. Comparing
  an hourly-sampled source against Kalshi's per-minute candlesticks would
  have measured sampling resolution and flattered Kalshi for no real reason.
- **Spot is Coinbase Exchange BTC-USD**, per-minute inside the final hour
  and strictly backward-looking. An earlier version of this backtest used
  the hourly series, which silently let the naive baseline read the 16:00Z
  print as "spot 5 minutes before 16:00" and score a perfect zero.
