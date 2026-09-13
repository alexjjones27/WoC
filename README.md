# WoC (Wisdom of Crowds)

Three pieces, built together:

- **[`wisdom-dashboard/`](wisdom-dashboard/)** — a local web app that pulls live BTC
  and crude oil (WTI) price-threshold/range markets from Polymarket, Kalshi, and
  Manifold, combines them into one volume-weighted probabilistic forecast per
  date, and shows it as a dashboard: point forecast, confidence bands, a
  probability-history "fan chart," and a separate touch-probability section for
  longer horizons. Run it with `wisdom-dashboard/run.sh` — see that folder's own
  README for the full writeup of the aggregation math, the calibration
  corrections, the trading-concentration correction, and how to add a new
  source or asset.

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
