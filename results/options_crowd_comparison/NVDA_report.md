# NVDA: analyst consensus vs. options-market crowd

Two genuinely independent 'wisdom of the crowd' sources for the same stock, same ~12-month horizon: Wall Street's analyst consensus (expert, credentialed, ~40 firms) vs. the options market's risk-neutral price distribution (anonymous, real money, includes heavy retail flow -- extracted via Breeden-Litzenberger from the live 2027-09-17 options chain, 67 liquid contracts used).

## The headline numbers

- Spot price: **$222.27**

- Analyst consensus target: **$324.00** (38 firms, +45.8% implied return, 21.5% dispersion)

- Options-market risk-neutral mean: **$231.73** (median $227.00)

- Options-implied annualized volatility: **38.8%**

- Options-implied 10th-90th percentile range: **$128 - $343**

- **Options market's own probability that NVDA actually exceeds the $324 analyst target: 13.6%**


## Why the options mean ISN'T a competing forecast

This is the important nuance: the options-implied distribution's MEAN is not the market's real-world prediction of where the stock is headed. By no-arbitrage, it's mechanically pinned close to the risk-free forward price ($230.18 here, vs. a computed mean of $231.73 -- matching almost exactly, which is the correctness check for this calculation, not a finding). Comparing that mean to the analyst target is not a fair fight -- one is a no-arbitrage bookkeeping identity, the other is a genuine subjective forecast.

What the options market DOES encode a genuine, comparable view on is the **shape** of the distribution -- how much uncertainty (volatility) and how the probability mass is spread across outcomes. That's what the P(exceed target) cross-check above uses: given the crowd's own risk-neutral distribution, how likely is the specific outcome the analysts are calling for?

One more caveat in that number's favor for the analysts: risk-neutral probabilities are systematically more conservative than real-world subjective probabilities for large up-moves (investors demand a risk premium to hold that risk, which is baked into option prices) -- so the true crowd view is probably somewhat more optimistic than the raw 13.6% suggests, though 'somewhat more' rarely closes a gap this size on its own.


## Caveats

- Single expiration, single snapshot -- not backtested over time yet (unlike the analyst consensus, which has a real historical backtest in results/analyst_price_target_backtest/).
- IV curve smoothed with a degree-3 polynomial in log-moneyness, weighted by open interest -- reasonable for a liquid large-cap name, not guaranteed to be stable for a thinly-traded options chain.
- Risk-free rate from ^IRX (13-week T-bill), dividend yield from yfinance's dividendRate/spot -- both minor approximations.
