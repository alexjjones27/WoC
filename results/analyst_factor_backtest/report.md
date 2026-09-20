# Analyst wisdom-of-the-crowd: cross-sectional factor backtest

Does ranking S&P 500 stocks by analyst wisdom-of-the-crowd signals actually help pick stocks that outperform -- not just "is the consensus price target accurate," but "if I'd gone long the stocks the crowd was most bullish on, would I have beaten the stocks it was least bullish on?"

## Method

- Universe: current S&P 500 constituents (503 tickers, scraped from Wikipedia). **Not point-in-time correct** -- this is today's membership, so companies removed from the index since the backtest's start (bankruptcy, steep decline, acquisition) are invisible here. Real, unresolved survivorship bias; flagged, not fixed.
- Quarterly rebalance, 3-month forward return (a shorter, more tradeable horizon than the 12-month single-stock backtest).
- Three signals, all computed with no lookahead:
  - **upside**: consensus 12-month target vs. current price
  - **revision**: mean %-change of price targets revised in the trailing 3 months (recent re-rating direction/magnitude)
  - **combined**: average of each period's cross-sectional percentile rank on upside and revision
  - **upside_lowdisp**: average rank of upside and (inverted) analyst dispersion -- prefers high upside where analysts agree
- Scored two ways: Information Coefficient (Spearman rank correlation between signal and the forward return that followed it, per period, then averaged) and quintile spread (equal-weight top 20% by signal minus bottom 20%, per period, then averaged).

## Results

| Signal | Periods | Avg universe size | Mean IC | IC t-stat | % periods IC>0 | Quintile spread (per 3mo) | Spread win rate |
|---|---|---|---|---|---|---|---|
| implied_return | 53 | 419 | 0.0133 | 0.58 | 43% | +0.51% | 43% |
| revision | 53 | 372 | 0.0240 | 1.64 | 66% | +1.43% | 66% |
| combined | 53 | 372 | 0.0190 | 0.86 | 55% | +1.23% | 60% |
| upside_lowdisp | 53 | 419 | -0.0302 | -2.20 | 40% | -1.96% | 36% |

For reference, an IC around 0.02-0.05 with a t-stat above ~2 is considered a real, usable factor in equity quant research -- most single factors are weak in isolation. A t-stat below ~2 means the average edge isn't reliably distinguishable from noise at this sample size.

## Caveats

- Survivorship bias from using today's S&P 500 membership (see above) -- the single biggest unresolved issue.
- No transaction costs, slippage, or position limits modeled -- these are gross, not net, returns.
- Quarterly non-overlapping windows keep the period-to-period IC values closer to independent, but ~50 periods is still a modest sample for a t-stat.
- `upgrades_downgrades` occasionally contains corrupt rows (a confirmed ACN row claimed a prior target of $4 against an ~$80 stock) -- filtered out at the row level where detectable, but there is no guarantee every bad row was caught.
