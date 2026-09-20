# Expanded wisdom-of-crowds portfolio (S&P 500)

Four signals -- smart money (13F, N=50 panel), analyst consensus, options-market confirmation, retail attention -- computed across all 503 S&P 500 constituents, standardized, and averaged. Top 25 by combined score, capped at 5 per GICS sector, requiring at least 2 of 4 signals available. **This is a current snapshot, not a backtest** -- no free source of historical options chains means this can't be tested against history the way the smart-money and analyst signals alone have been elsewhere in this project.

## Data coverage

- Smart money (>0 weight): 80/503

- Analyst consensus: 492/503

- Analyst revision momentum (drives ranking): 490/503

- Options confirmation: 139/503

- Retail attention: 495/503


## The portfolio

| Ticker | Sector | Weight | Smart money | Analyst impl. ret | Revision momentum | Options P(hit) | Retail attn | Combined score |
|---|---|---|---|---|---|---|---|---|
| MRSH | Financials | 13.4% | 0.4% | +16.2% | +2.3% | 31% | 6.88x | +3.63 |
| AAPL | Information Technology | 10.2% | 4.5% | -4.1% | +5.0% | 66% | 1.75x | +2.94 |
| AKAM | Information Technology | 9.4% | 0% | +35.1% | -5.1% | n/a | 4.90x | +2.77 |
| MCK | Health Care | 8.3% | 0% | +8.3% | +5.9% | n/a | 4.11x | +2.53 |
| META | Communication Services | 5.4% | 2.5% | n/a | n/a | n/a | 0.76x | +1.89 |
| MRNA | Health Care | 5.3% | 0% | -40.9% | +75.5% | 57% | 0.53x | +1.87 |
| MSFT | Information Technology | 4.9% | 3.7% | +14.5% | +0.7% | 30% | 1.32x | +1.78 |
| NVDA | Information Technology | 4.8% | 4.0% | +45.8% | +7.8% | 14% | 1.01x | +1.77 |
| GOOGL | Communication Services | 4.7% | 3.9% | +14.5% | +0.7% | 36% | 0.99x | +1.74 |
| AVGO | Information Technology | 3.5% | 3.8% | +42.7% | +3.3% | 15% | 0.94x | +1.48 |
| HUM | Health Care | 3.4% | 0.4% | -5.4% | +40.1% | n/a | 0.95x | +1.45 |
| AXP | Financials | 3.1% | 2.3% | +19.1% | +5.8% | n/a | 0.97x | +1.40 |
| AMZN | Consumer Discretionary | 2.9% | 3.4% | +25.0% | +2.6% | 21% | 0.94x | +1.35 |
| GE | Industrials | 2.5% | 1.7% | +23.0% | +12.6% | n/a | 0.97x | +1.27 |
| DD | Industrials | 2.5% | 0% | +49.2% | +40.3% | n/a | 1.03x | +1.27 |
| AMGN | Health Care | 2.1% | 0.3% | +2.4% | +6.5% | 96% | 1.51x | +1.17 |
| SBAC | Real Estate | 2.1% | 0% | +28.5% | -7.8% | n/a | 3.04x | +1.17 |
| PM | Consumer Staples | 1.8% | 1.6% | +8.2% | +7.6% | n/a | 1.02x | +1.11 |
| GOOG | Communication Services | 1.7% | 3.0% | +12.9% | -6.3% | 35% | 0.99x | +1.10 |
| BG | Consumer Staples | 1.7% | 0% | +16.3% | +6.9% | 33% | 2.73x | +1.09 |
| MS | Financials | 1.6% | 0% | +15.2% | +10.3% | 82% | 1.65x | +1.07 |
| LLY | Health Care | 1.5% | 2.0% | +13.4% | +7.0% | 44% | 0.87x | +1.06 |
| KHC | Consumer Staples | 1.4% | 0.3% | +0.7% | +4.8% | n/a | 2.03x | +1.03 |
| KO | Consumer Staples | 1.1% | 1.6% | +5.2% | +6.7% | 52% | 0.91x | +0.95 |
| BAC | Financials | 0.5% | 1.5% | +11.3% | +5.6% | 48% | 0.87x | +0.82 |

## Sector breakdown

- Information Technology: 5

- Health Care: 5

- Financials: 4

- Consumer Staples: 4

- Communication Services: 3

- Industrials: 2

- Consumer Discretionary: 1

- Real Estate: 1


## Caveats

- Snapshot only, not backtested -- validate by watching forward, not by trusting the combined score as a proven predictor.
- Zero smart-money weight for most of the universe is expected, not a gap -- most S&P 500 stocks aren't held by any of the 50 funds in the panel.
- Options and analyst signals are not fully independent (options confirmation is defined relative to the analyst target).
- The analyst signal ranks by revision momentum, not the static implied-return level -- backed by results/analyst_factor_backtest, which found the static level had no real predictive power (IC t-stat 0.58) while revision momentum did (IC t-stat 1.64). Smart money, options, and retail attention haven't had the same standalone predictive-power test run on them -- they're included on the strength of the argument for genuinely independent crowds, not (yet) their own proven track record.
- Sector cap is a simple diversification heuristic, not a real risk model -- no factor/beta neutrality, no correlation-aware position sizing.
- Gross, hypothetical construction -- no transaction costs, no consideration of trade size vs. liquidity.
