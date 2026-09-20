# Analyst price-target consensus backtest

How accurate has Wall Street's aggregate 12-month analyst price-target consensus actually been, historically, at predicting where a stock's price would actually be a year later -- and does it beat assuming the price doesn't move at all?

## Method

- Data: `yfinance`'s `Ticker.upgrades_downgrades` -- free, no API key, real per-firm price-target history (not just a current snapshot).
- For each historical date T (quarterly steps), the consensus is reconstructed using only data available as of T: each covering firm's most recently set price target (targets older than 15 months are dropped as stale/likely-uncovered), averaged across at least 3 firms.
- Scored as *implied return* (`target/price_T - 1`) vs. *realized return* 12 months later, to normalize across stocks and time.
- Compared against a naive baseline that assumes 0% return (flat price).

## Headline result

- **575 backtest snapshots** across 15 stocks (avg 21.4 covering firms per snapshot)

- Analyst consensus mean absolute error: **30.5 percentage points** of return (RMSE 47.6pp)

- Naive "flat price" baseline mean absolute error: **32.4 percentage points** (RMSE 51.0pp)

- Consensus BEATS the naive baseline on mean absolute error.

- Directional hit rate (correctly called up vs. down): **61.6%** (50% = coin flip)

- Mean analyst-implied return: 9.1%  |  Mean realized return: 22.4%


## Per-stock breakdown

| Symbol | N | Avg firms | MAE | Naive MAE | Direction hit rate | Mean implied ret | Mean realized ret |
|---|---|---|---|---|---|---|---|
| AAPL | 27 | 29.4 | 34.5pp | 36.0pp | 63% | 3.6% | 33.1% |
| AMZN | 18 | 41.8 | 29.1pp | 30.9pp | 67% | 26.4% | 15.4% |
| BA | 50 | 13.7 | 31.1pp | 30.5pp | 46% | 13.6% | 12.2% |
| DIS | 50 | 17.6 | 23.0pp | 19.1pp | 56% | 12.4% | 6.1% |
| GOOGL | 32 | 29.9 | 31.0pp | 37.7pp | 66% | 14.9% | 30.1% |
| INTC | 49 | 23.2 | 38.6pp | 39.7pp | 67% | 11.2% | 21.9% |
| JNJ | 48 | 9.7 | 11.4pp | 11.2pp | 58% | 6.1% | 8.3% |
| JPM | 50 | 13.2 | 21.1pp | 21.7pp | 62% | 3.2% | 17.0% |
| META | 13 | 39.5 | 54.3pp | 63.0pp | 85% | 4.4% | 58.1% |
| MSFT | 49 | 22.9 | 26.0pp | 28.5pp | 57% | 5.5% | 24.4% |
| NFLX | 43 | 30.3 | 48.9pp | 48.5pp | 49% | 5.2% | 33.7% |
| NVDA | 28 | 31.5 | 82.9pp | 100.0pp | 82% | 18.1% | 93.8% |
| TSLA | 19 | 30.5 | 37.6pp | 37.8pp | 37% | -0.9% | 22.1% |
| WMT | 50 | 19.2 | 15.6pp | 18.8pp | 78% | 8.3% | 14.5% |
| XOM | 49 | 12.4 | 20.1pp | 22.1pp | 65% | 9.1% | 6.9% |

## Live consensus (today's aggregate 12-month forecast per stock)

This is the 'forward test' half of the ask: today's active analyst price targets, aggregated into one consensus forecast per stock the same way the backtest reconstructs history. Nobody can score this for ~12 months -- that's the nature of a forward test.

| Symbol | Spot | Consensus target (mean) | Consensus target (median) | Dispersion | N firms | Implied return |
|---|---|---|---|---|---|---|
| AAPL | $336.13 | $322.30 | $330.00 | 16.9% | 32 | -4.1% |
| AMZN | $253.71 | $317.07 | $320.00 | 10.8% | 40 | +25.0% |
| BA | $198.20 | $275.18 | $270.00 | 6.8% | 17 | +38.8% |
| DIS | $102.67 | $128.31 | $131.00 | 7.4% | 16 | +25.0% |
| GOOGL | $349.54 | $400.05 | $411.00 | 15.9% | 41 | +14.5% |
| INTC | $108.60 | $99.57 | $100.00 | 33.1% | 35 | -8.3% |
| JNJ | $269.99 | $267.84 | $265.00 | 12.1% | 19 | -0.8% |
| JPM | $349.67 | $364.73 | $362.00 | 9.5% | 15 | +4.3% |
| MSFT | $493.78 | $565.60 | $550.00 | 10.3% | 35 | +14.5% |
| NFLX | $71.79 | $101.94 | $100.00 | 22.1% | 33 | +42.0% |
| NVDA | $222.27 | $324.00 | $317.00 | 21.5% | 38 | +45.8% |
| TSLA | $364.27 | $399.27 | $445.00 | 33.7% | 29 | +9.6% |
| WMT | $106.73 | $131.48 | $131.00 | 7.3% | 29 | +23.2% |
| XOM | $163.54 | $164.70 | $170.00 | 12.6% | 20 | +0.7% |

## Caveats

- 12 months is an approximation -- not every firm explicitly states a 12-month horizon, and horizons vary.
- `upgrades_downgrades` is Yahoo Finance's scrape, not an official stable API -- treat it as "best available free source," not institutional-grade (e.g. Refinitiv/FactSet IBES).
- No survivorship-bias correction beyond what yfinance itself returns -- delisted/failed companies are not represented in this symbol set.
- The 15-month staleness cutoff for "still covering" is a judgment call, not a measured fact (yfinance has no explicit coverage-dropped signal).
