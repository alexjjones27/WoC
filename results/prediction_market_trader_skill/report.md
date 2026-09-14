# Have Polymarket traders actually been good at predicting BTC and oil prices?

Source: `scripts/run_prediction_market_trader_skill.py` /
`src/prediction_market_trader_skill.py`.

> **This report replaces an earlier version whose numbers were wrong.** The
> scoring had a bug: Polymarket's trade feed reports trades on *both*
> outcome tokens of each binary market, tagged `outcome`/`outcomeIndex`,
> with `price` being the price of that token -- and every trade was being
> scored against the *Yes* resolution regardless of which token it was on.
> No-token trades are the majority in most of these markets, so the sign
> was inverted for most of the sample. The earlier headline numbers
> (BTC correlation 0.29 p=0.001, OIL "no persistence" p=0.79) came from
> that scoring and should be discarded. Both conclusions change below.
> The bug was found by plotting the market's own price-vs-outcome curve,
> which came out backwards: contracts trading at 0.3c appeared to pay off
> 20x more often than their price, and 99.7c contracts almost never. That
> is not something a market can do. `tests/test_trader_skill_stats.py`
> now pins the correct behaviour.

## Method

Every individual wallet-attributed trade in every resolved market is
scored against what actually happened:

    pnl_per_share = side_sign * (token_pays - price)

where `side_sign` is +1 for BUY and -1 for SELL, and `token_pays` is 1 if
the token that trade was on is the side that won, 0 otherwise. A wallet's
**edge** is total P&L divided by total notional traded -- a size-normalized
return on capital, comparable across traders.

- **BTC**: 219,259 trades, 12,037 wallets, 97 resolved daily
  "Bitcoin price on `<date>`" range-bucket events, 2026-06-04 to 2026-09-13.
- **OIL**: 65,740 trades, 16,170 wallets, 16 resolved
  "What will WTI hit ...?" touch events, 2026-06-19 to 2026-09-11.

Ground truth is each market's own settlement. (The OIL half prefers
realized WTI high/low from an independent price feed; that feed was
unreachable on this run, so both assets here are graded by Polymarket's own
settlement. `oil_ground_truth_used()` records which was used, and the
results JSON carries it.)

## The market's own calibration, by trade price

Before asking whether any trader has an edge, it is worth asking where an
edge could come from. Pooling every trade by price and measuring how often
that price level actually paid off:

| BTC: mean price | realized | ratio | | OIL: mean price | realized | ratio |
|---:|---:|---:|---|---:|---:|---:|
| 0.003 | 0.001 | **0.34** | | 0.005 | 0.000 | **0.01** |
| 0.015 | 0.004 | **0.28** | | 0.046 | 0.000 | **0.00** |
| 0.043 | 0.046 | 1.07 | | 0.126 | 0.009 | **0.07** |
| 0.177 | 0.177 | 1.00 | | 0.245 | 0.115 | **0.47** |
| 0.453 | 0.503 | 1.11 | | 0.455 | 0.508 | 1.12 |
| 0.756 | 0.738 | 0.98 | | 0.757 | 0.935 | **1.23** |
| 0.940 | 0.947 | 1.01 | | 0.940 | 1.000 | 1.06 |
| 0.997 | 1.000 | 1.00 | | 0.997 | 1.000 | 1.00 |

**BTC is well calibrated except at the extreme cheap end**, where the
familiar favorite-longshot bias shows up: sub-2c contracts pay off about a
third as often as their price implies. **OIL is badly mispriced across a
much wider band** -- everything under ~13c is close to worthless, and the
55-76c range pays off 23-33% more often than it costs.

That matters for the next section, because a static mispricing like this is
something a trader can harvest forever without forecasting anything.

## The real test: does skill persist out-of-sample?

A "best edge this period" leaderboard is not evidence of skill -- with
thousands of wallets, some look great in any sample by chance. The standard
fix: split events chronologically, compute each wallet's edge in period 1,
and check whether it predicts their edge in the independent period 2.

Edge is a ratio with a tiny denominator (a 0.1c share that pays off returns
~999x), so per-wallet edge is heavy-tailed and a raw Pearson correlation can
rest on a handful of wallets. The permutation test does **not** protect
against that: it tests whether the *pairing* is non-random, which one
extreme point present in both periods satisfies. So the correlation is
reported three ways.

| | BTC | OIL |
|---|---:|---:|
| split date | 2026-07-25 | 2026-08-01 |
| wallets active in both periods | 690 | 368 |
| **Pearson** | 0.105 (p=0.041) | 0.281 (p<0.001) |
| **Spearman (rank)** | **0.240 (p<0.001)** | 0.202 (p<0.001) |
| **Winsorized Pearson** | 0.222 (p<0.001) | 0.289 (p<0.001) |
| Pearson, dropping most influential wallet | 0.143 | 0.296 |
| Pearson, dropping top 1% most influential | 0.219 | 0.328 |

Both assets show persistence on every measure. Note that for BTC the
outliers *suppress* the raw Pearson rather than inflating it -- the rank
correlation (0.240) is the more representative number, and dropping the
most influential 1% of wallets moves Pearson to almost exactly that.

## But is it skill, or harvesting the mispricing?

This is the question that decides what the result means, and the one the
previous version of this report did not ask. A wallet that simply sells
cheap contracts harvests the bias in the table above in every period,
forever, without forecasting anything -- and that shows up as exactly the
persistence signature tested for.

The decomposition uses only the sample itself: estimate q(p), the empirical
probability a token trading at price p actually pays off (a market-level
property, fitted across all trades, not per wallet), attribute
`side_sign * (q(price) - price)` of each trade's P&L to price level alone,
and re-run the persistence test on what is left.

| | BTC | OIL |
|---|---:|---:|
| share of population P&L explained by price level | 28.6% | **45.9%** |
| corr(P1 edge, P1 mean trade price) | 0.139 | 0.168 |
| **residual Spearman** | **0.219 (p<0.001)** | **0.078 (p=0.13)** |
| residual Pearson | 0.092 (p=0.043) | -0.098 (p=0.074) |
| residual winsorized Pearson | 0.109 (p=0.005) | -0.106 (p=0.049) |

**BTC: the persistence survives.** Rank correlation barely moves (0.240 ->
0.219) once price-level mispricing is stripped out. Something real and
repeatable is going on beyond harvesting a static bias.

**OIL: the persistence does not survive.** It collapses to 0.078 (p=0.13),
and the winsorized Pearson actually flips negative. Nearly half of all P&L
in the OIL sample is explained by price level alone. OIL's apparent skill
is the mispricing, not forecasting.

Note this reverses *both* of the earlier report's conclusions: OIL now
shows raw persistence where it previously showed none, and that persistence
turns out to be a market artifact rather than trader ability.

## A caveat on what "persistence" means for BTC

Persistence is not the same as "good traders stay good." The decile table:

| | BTC | OIL |
|---|---:|---:|
| top decile by P1 edge: P1 -> P2 | +0.381 -> **-0.184** | +0.781 -> +0.177 |
| bottom decile by P1 edge: P1 -> P2 | -0.453 -> -0.579 | -0.951 -> -0.282 |
| population P2 edge mean | -0.078 | -0.009 |

BTC's top decile in period 1 went on to *underperform* the population in
period 2 (-0.184 vs -0.078). The bottom decile stayed firmly worst
(-0.579). So BTC's rank correlation is driven substantially by **bad
traders reliably staying bad**, not by good traders staying good. That is a
genuine, repeatable signal, and it is a far weaker claim than "some traders
can predict Bitcoin." Anyone reading this as a strategy should note that
the identifiable, persistent group is the losing one.

## Caveats

- **The population loses money on average.** BTC population edge mean is
  -0.067, OIL -0.059 (medians near zero). This is a negative-sum game after
  the spread; persistence is measured against that backdrop.
- **Edge is a per-trade metric, not a reconstructed position.** Each trade
  is scored as its own bet rather than netting a wallet's full position
  (see the module docstring: a capped trade-history fetch cannot guarantee
  completeness for high-volume markets). A wallet that bought and sold the
  same contract repeatedly is scored per leg.
- **No fees, gas, or slippage** in the P&L. Real returns would be lower.
- **OIL is small and its events overlap.** 16 events (8/8 split) against
  BTC's 97, and touch events on overlapping windows are not independent
  draws. The OIL null is consistent with underpowering as well as with
  genuine absence.
- **BTC's 97 daily events are not 97 independent draws** either -- BTC's
  path is autocorrelated day to day, so standard errors are optimistic.
- **q(p) is fitted on the whole sample**, including the periods being
  tested. It is a market-level curve over ~220k trades rather than a
  per-wallet fit, so the in-sample leakage into any individual wallet's
  residual is small, but it is not zero.
- **One-time snapshot.** Trades are cached to `data/raw/trader_skill/`;
  this report reflects one run's cutoff.
