# SEC 13F smart-money consensus backtest

Tracking 10 well-known active hedge funds (Berkshire Hathaway, Coatue Management, Viking Global, Tiger Global, Elliott Management, Farallon Capital, Pershing Square, Lone Pine Capital, Icahn, Soros Fund Management) through SEC 13F filings from 31-DEC-2020 to 30-JUN-2026 (22 quarterly rebalances). Each quarter, a consensus portfolio is built from the top holdings across all funds that filed that quarter (dollar-value weighted), 'bought' only from the date the LAST of that quarter's filings became public (the real 45-day disclosure lag, not the quarter-end date), and held until the next quarter's basket is revealed.

## Performance summary

| Portfolio | Total return | CAGR | Annualized vol | Max drawdown | Final NAV (start=1.0) |
|---|---|---|---|---|---|
| **Consensus (all 10 funds)** | +84.2% | 11.7% | 10.7% | -13.4% | 1.84 |
| SPY (benchmark) | +97.9% | 13.2% | 10.9% | -15.5% | 1.98 |
| Berkshire Hathaway | +90.8% | 12.5% | 10.5% | -12.1% | 1.91 |
| Coatue Management | +17.7% | 3.0% | 33.7% | -61.9% | 1.18 |
| Viking Global | +55.6% | 8.4% | 17.4% | -32.8% | 1.56 |
| Tiger Global | +15.2% | 2.6% | 32.2% | -61.0% | 1.15 |
| Elliott Management | +311.9% | 29.4% | 14.0% | -5.1% | 4.12 |
| Farallon Capital | +83.4% | 11.7% | 15.4% | -16.3% | 1.83 |
| Pershing Square | +116.7% | 15.1% | 11.3% | -5.8% | 2.17 |
| Lone Pine Capital | +16.1% | 2.8% | 29.6% | -56.5% | 1.16 |
| Icahn | -65.5% | -17.6% | 22.0% | -69.4% | 0.34 |
| Soros Fund Management | -30.4% | -6.4% | 31.4% | -60.2% | 0.70 |

Note: these are GROSS returns from 13F-reconstructed top-holdings portfolios only -- no transaction costs, no fees (real hedge funds charge management + performance fees that eat heavily into net investor returns), no short positions, no non-13F assets, and no reflection of each fund's actual trade timing/sizing within the quarter. A fund's TRUE net-of-fee return will differ, often substantially, from its 13F-reconstructed gross return shown here.

## Current (latest quarter) consensus portfolio

Report period: 30-JUN-2026, funds filing: BERKSHIRE, COATUE, ELLIOTT, FARALLON, ICAHN, LONE_PINE, PERSHING_SQUARE, SOROS, TIGER_GLOBAL, VIKING

## Caveats

- 13F only covers long US-listed equity + listed options positions over the reporting threshold -- no shorts, no international holdings, no bonds/cash/private investments. Some funds' real portfolios (and real risk) look very different from their 13F.
- Fund identity tracked by CIK, with one known manual correction (Pershing Square re-filed under a new CIK due to a real corporate restructuring) -- other undiscovered entity renames/CIK changes among the panel over 2021-2026 could silently break a fund's continuity in this series.
- "Visible date" uses the actual filing date of the last fund in the basket to file that quarter -- real, not a fixed 45-day assumption -- but a fund's true trade could have happened weeks to months before its filing date.
- Equal treatment of a position whether it's a core long-term holding or a position about to be exited -- no attempt to weight by conviction beyond dollar size.
