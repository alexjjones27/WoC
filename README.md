# WoC (Wisdom of Crowds)

One idea, worked from several directions: aggregate different, genuinely
independent "crowds" — real-money prediction markets, professional analyst
opinion, options-market pricing, institutional 13F conviction, and retail
attention — and see where they agree, where they disagree, and whether any
of it actually predicts anything.

## The live dashboard

**[`wisdom-dashboard/`](wisdom-dashboard/)** — a local web app, now four
pages. Run it with `wisdom-dashboard/run.sh`.

- **Prediction Markets** — the original piece: live BTC, crude oil (WTI),
  Ethereum, and gold price-threshold/range markets from Polymarket, Kalshi,
  Manifold, Futuur and Limitless, combined into one volume-weighted probabilistic forecast per
  date, shown as a dashboard with confidence bands, a probability-history
  "fan chart," and a separate touch-probability section for longer horizons.
- **Smart Money** — SEC 13F consensus from active hedge funds, expanded into
  a 25-stock S&P 500 portfolio across four signals, plus the backtests
  behind it (2013–2026).
- **Stock Lookup** — live four-signal read on any ticker: smart money
  weight, analyst consensus, options-market confirmation, retail attention,
  computed fresh in a few seconds.
- **Crowds** — every independent crowd for BTC, ETH, gold and oil side by
  side: prediction markets (now including Futuur and Limitless), options
  (Deribit, OKX, Derive), futures and perps (Deribit, OKX, Hyperliquid), the
  EIA's oil forecast, CFTC positioning, and retail sentiment (StockTwits,
  Fear & Greed, MVRV, Wikipedia). All free, no keys.

See the dashboard's own README for the aggregation math, calibration
corrections, the trading-concentration correction, and how to add a new
source or asset.

Two published write-ups sit alongside the dashboard:
- **[Smart Money Consensus](https://claude.ai/code/artifact/4c8688ea-0756-4660-80bd-fbbbc847867c)** —
  the SEC 13F work end to end: backtest, significance testing, and the
  expanded portfolio.
- **[Three Crowds, One Price](https://claude.ai/code/artifact/009a7b09-a21f-4120-a0b5-02300d8e03c2)** —
  the BTC/oil capstone comparing prediction markets against options pricing.

## Prediction-market research (the original three pieces)

- **[`scripts/run_btc_price_market_calibration.py`](scripts/run_btc_price_market_calibration.py)**
  (backed by [`src/btc_price_market_calibration.py`](src/btc_price_market_calibration.py)) —
  the empirical backtest behind the dashboard's calibration correction: scores
  96 resolved Polymarket "Bitcoin price on `<date>`" markets against realized
  BTC price, by lead time before resolution, to check whether the market's
  stated probabilities and confidence intervals were actually well-calibrated.
  Findings and the two plots are in
  [`results/btc_price_market_calibration/report.md`](results/btc_price_market_calibration/report.md).
  The dashboard's `aggregation.py` applies what this found (longshot-bucket
  shrinkage, confidence-interval width correction) as documented, tunable
  constants.

- **[`src/polymarket_trader_concentration.py`](src/polymarket_trader_concentration.py)** —
  checks whether a market's volume represents a broad, independent set of
  traders or a handful of large wallets (and whether trades show a
  herding/momentum signature), using Polymarket's public wallet-attributed
  trade feed. Findings across 6 live BTC/OIL markets are in
  [`results/polymarket_trader_concentration/report.md`](results/polymarket_trader_concentration/report.md) —
  every market checked showed real concentration and consistent positive
  trade-direction autocorrelation. The dashboard's `backend/concentration.py`
  applies the concentration half of this as a weight discount (Polymarket
  only — Kalshi/Manifold don't expose the trade-level data this needs).

- **[`src/prediction_market_trader_skill.py`](src/prediction_market_trader_skill.py)** —
  have individual Polymarket traders actually been good at predicting BTC
  and oil prices, and is it real skill or noise? Scores every trade
  (218,874 BTC trades across 96 resolved daily events; 65,740 OIL trades
  across 16 resolved touch events) against what actually happened, then
  runs the standard test for this — does a trader's edge in the first half
  of the sample predict their edge in the second, independent half.
  Finding, in [`results/prediction_market_trader_skill/report.md`](results/prediction_market_trader_skill/report.md):
  **BTC shows real, statistically significant skill persistence**
  (correlation 0.29, permutation p=0.001); **OIL shows none detected**
  (p=0.79, but on a much smaller sample — read as underpowered, not as
  proof oil is unpredictable). Not wired into the live dashboard — a
  standalone finding, not (yet) a correction.

## Analyst price targets: free data, real history

- **[`src/analyst_price_target_backtest.py`](src/analyst_price_target_backtest.py)**
  / [`scripts/run_analyst_price_target_backtest.py`](scripts/run_analyst_price_target_backtest.py) —
  `yfinance`'s `upgrades_downgrades` turns out to hold real per-firm price-
  target history back to 2012–2019 depending on the stock, free, no API key.
  Reconstructs the analyst consensus at each historical date with no
  lookahead and backtests it against realized price 12 months later, across
  15 large-cap stocks. Found and fixed a real bug along the way: price
  targets aren't retroactively split-adjusted, so a stock split (NVDA 10:1,
  NFLX 10:1, Booking Holdings 25:1) makes an old target look like a
  ~10–40x return unless corrected. Report:
  [`results/analyst_price_target_backtest/report.md`](results/analyst_price_target_backtest/report.md).

- **[`src/analyst_factor_backtest.py`](src/analyst_factor_backtest.py)**
  / [`scripts/run_analyst_factor_backtest.py`](scripts/run_analyst_factor_backtest.py) —
  the same idea, but as a cross-sectional stock-picking signal across the
  full S&P 500 (502 tickers, 2013–2026): does ranking stocks by analyst
  data actually predict which ones outperform, not just "is the consensus
  price accurate"? The static implied-return level had no real predictive
  power (Information Coefficient t-stat 0.58); **revision momentum** —
  recent target raises/cuts — did (t-stat 1.64, 66% win rate). That finding
  is what drives the ranking signal everywhere else in this repo that uses
  analyst data. Report: [`results/analyst_factor_backtest/report.md`](results/analyst_factor_backtest/report.md).

## Options-implied distributions and retail attention

- **[`src/options_implied_distribution.py`](src/options_implied_distribution.py)** —
  a full risk-neutral probability distribution from a live options chain via
  Breeden-Litzenberger (1978): smooth the implied-volatility curve, price a
  fine strike grid off the smoothed curve, differentiate twice. Works for
  any liquid US options chain — individual stocks and ETF proxies alike
  (IBIT for Bitcoin, USO for oil, ETHA for Ethereum, GLD for gold).

- **[`src/wikipedia_attention.py`](src/wikipedia_attention.py)** — genuine
  retail attention, not sentiment: Wikipedia pageviews for a company/asset
  relative to its own 90-day baseline, free, no auth, real academic
  precedent (Moat et al.). Every other free retail-crowd source checked live
  this project (StockTwits, Reddit, Estimize, TradingView ideas) turned out
  to be gated or ToS-risky; this is the one that works.

- **[`src/sec_13f_wisdom.py`](src/sec_13f_wisdom.py)** — aggregates SEC Form
  13F filings from a curated panel of active, concentrated hedge funds
  (deliberately excluding passive giants like BlackRock/Vanguard and
  quant/market-making shops like Citadel/AQR, which would just reproduce
  the S&P 500) into a consensus portfolio, live and historically. Backtest
  scripts: [`run_sec_13f_backtest.py`](scripts/run_sec_13f_backtest.py)
  (fixed 10-fund panel, 2021–2026) and
  [`run_sec_13f_scaling_backtest.py`](scripts/run_sec_13f_scaling_backtest.py)
  (objective rolling N=10/20/50 panels, 2013–2026 — no hand-picked names).
  Headline finding, after proper significance testing
  ([`sec_13f_significance.py`](src/sec_13f_significance.py)): these panels
  numerically beat SPY, but the difference isn't statistically significant
  (p≈0.27–0.51), and a factor regression shows why — beta 1.11–1.16, alpha
  statistically zero. They behave like SPY with extra leverage, not like
  genuine stock-picking skill.

- **[`src/combined_signals_service.py`](src/combined_signals_service.py)**,
  [`src/btc_oil_wisdom_combination.py`](src/btc_oil_wisdom_combination.py) —
  where all of the above actually get combined. The stock side
  ([`run_expanded_wisdom_portfolio.py`](scripts/run_expanded_wisdom_portfolio.py))
  standardizes and averages smart money, analyst revision momentum, options
  confirmation, and retail attention across all 503 S&P 500 stocks into a
  25-stock portfolio. The BTC/oil/ETH/gold side
  ([`run_btc_oil_unification.py`](scripts/run_btc_oil_unification.py))
  compares the prediction-market consensus against the options market's own
  distribution directly, since both are full probability distributions, not
  just point estimates — including an honest horizon-mismatch check rather
  than forcing a comparison when the two don't cover the same time frame
  (real for oil and, intermittently, ETH). None of this can be backtested —
  there's no free source of historical options chains — so it's presented
  as a live snapshot, meant to be watched forward, not trusted as a proven
  predictor.
