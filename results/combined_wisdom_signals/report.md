# Combining four independent wisdom-of-crowd signals

Smart money (13F), analyst consensus, options-market confirmation, and retail attention (Wikipedia), for the same 23-stock smart-money universe. Each is standardized (z-scored across this universe) and averaged into one combined score. **This is a current snapshot, not a backtest** -- there is no free source of historical options chains, so signals 3 and 4 can't be tested against history the way the smart-money and analyst signals already have been elsewhere in this project.

## Full comparison, ranked by combined score

| Ticker | Smart money wt | Analyst impl. ret | Options P(exceed target) | Retail attention | Combined score | Signal spread |
|---|---|---|---|---|---|---|
| AAPL | 18.8% | -4.1% | 66% | 1.75x | +1.19 | 2.04 |
| AXP | 14.6% | +19.1% | n/a | 0.97x | +0.72 | 1.22 |
| KHC | 2.2% | +0.7% | n/a | 2.03x | +0.44 | 2.28 |
| GOOGL | 12.0% | +14.5% | 36% | 0.99x | +0.26 | 0.89 |
| LRCX | 1.6% | +14.1% | 75% | 0.91x | +0.17 | 1.07 |
| KO | 9.3% | +5.2% | 52% | 0.91x | +0.08 | 0.89 |
| AMAT | 1.5% | +41.1% | 23% | 0.97x | +0.08 | 1.30 |
| DAL | 1.5% | +29.4% | n/a | 0.94x | +0.07 | 0.83 |
| NVDA | 1.0% | +45.8% | 14% | 1.01x | +0.06 | 1.62 |
| BAC | 7.8% | +11.3% | 48% | 0.76x | -0.03 | 0.71 |
| SIRI | 1.0% | +23.0% | n/a | n/a | -0.07 | 0.81 |
| MCO | 3.2% | +18.3% | n/a | 0.95x | -0.10 | 0.20 |
| MSFT | 1.5% | +14.5% | 30% | 1.32x | -0.11 | 0.69 |
| CVX | 4.0% | -3.3% | 66% | 0.97x | -0.13 | 1.19 |
| AMZN | 2.5% | +25.0% | 21% | 1.08x | -0.13 | 0.72 |
| TSM | 2.4% | +17.3% | 31% | 1.03x | -0.20 | 0.28 |
| TFPM | 1.1% | +18.4% | n/a | n/a | -0.24 | 0.54 |
| GEV | 1.1% | +14.8% | 40% | 0.91x | -0.28 | 0.26 |
| DVA | 1.8% | +16.8% | n/a | 0.84x | -0.34 | 0.30 |
| OXY | 3.7% | +9.6% | n/a | n/a | -0.34 | 0.33 |
| MU | 1.0% | +20.5% | 29% | 0.57x | -0.58 | 0.68 |
| IEP | 1.3% | n/a | n/a | 0.81x | -0.62 | 0.04 |
| META | 1.7% | n/a | n/a | 0.76x | -0.65 | 0.19 |

## Strongest agreement across signals

| Ticker | Combined score | Signal spread |
|---|---|---|
| MCO | -0.10 | 0.20 |
| GEV | -0.28 | 0.26 |
| TSM | -0.20 | 0.28 |
| DVA | -0.34 | 0.30 |
| MU | -0.58 | 0.68 |

## Strongest disagreement across signals

| Ticker | Combined score | Signal spread |
|---|---|---|
| KHC | +0.44 | 2.28 |
| AAPL | +1.19 | 2.04 |
| NVDA | +0.06 | 1.62 |
| AMAT | +0.08 | 1.30 |
| AXP | +0.72 | 1.22 |

## Caveats

- Not independent in the pure sense: the options signal is defined relative to the analyst target, so it's a cross-check on the analyst signal, not a fully independent 5th vote.
- Snapshot only -- can't be backtested with free data (no historical options chains). The honest way to validate this is to track it forward from today, which has zero lookahead risk.
- Small universe (23 stocks, all smart-money holdings) -- this isn't a market-wide screen, just a lens on stocks ten funds already like.
