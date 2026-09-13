# WoC (Wisdom of Crowds)

Two pieces, built together:

- **[`wisdom-dashboard/`](wisdom-dashboard/)** — a local web app that pulls live BTC
  price-threshold/range markets from Polymarket, Kalshi, and Manifold, combines
  them into one volume-weighted probabilistic forecast per date, and shows it as
  a dashboard: point forecast, confidence bands, a probability-history "fan
  chart," and a separate touch-probability section for longer horizons. Run it
  with `wisdom-dashboard/run.sh` — see that folder's own README for the full
  writeup of the aggregation math, the calibration corrections, and how to add
  a new source or asset.

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
