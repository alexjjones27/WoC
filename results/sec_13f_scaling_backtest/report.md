# Does a bigger smart-money panel help? (N=10/20/50/100)

Same consensus-portfolio backtest as the curated 10-fund version, but the panel is now selected objectively every quarter (no hand-picked names): rank all 13F filers by their own reported value, restricted to a plausible 'concentrated active manager' position-count range, with a best-effort exclusion of insurers, pension funds, banks, and corporate treasury self-filers (see src/sec_13f_wisdom.py's NON_FUND_KEYWORDS). Unlike the curated backtest, the panel membership is NOT tracked as fixed identities across time -- each quarter independently re-selects 'whoever qualifies as top-N right now.'

## Results

| Panel | Total return | CAGR | Annualized vol | Max drawdown |
|---|---|---|---|---|
| N10 | +494.3% | 14.4% | 15.0% | -35.4% |
| N20 | +503.8% | 14.5% | 14.8% | -26.1% |
| N50 | +431.1% | 13.4% | 14.3% | -22.1% |
| SPY | +369.9% | 12.4% | 11.0% | -17.1% |

## Caveats

- The exclusion filter is best-effort keyword/name matching, not a guarantee -- some non-stock-picker institutions (e.g. market-making/prop-trading shops with moderate position counts, like CTC LLC) can still pass through.
- No identity tracking across quarters at this scale -- a fund can drop in and out of the panel from quarter to quarter as its ranking changes, which is different from (and arguably more realistic than) the curated backtest's fixed 10-fund panel.
- Same gross-returns caveat as the curated backtest: no fees, no transaction costs, no shorts, no non-13F assets.
