# WoC (Wisdom of Crowds)

Five pieces, built together: a live dashboard, and four pieces of
measurement that check whether the ideas the dashboard rests on actually
hold. The measurement is the point. Where a check came back negative, the
dashboard changed rather than the finding being buried.

- **[`wisdom-dashboard/`](wisdom-dashboard/)** — a local web app that pulls
  live BTC and crude oil (WTI) price-threshold/range markets from
  Polymarket, Kalshi, and Manifold, combines them into one volume-weighted
  probabilistic forecast per date, and shows it as a dashboard:
  market-implied probabilities, a probability histogram, confidence bands,
  a probability-history "fan chart," and a separate touch-probability
  section for longer horizons. Run it with `wisdom-dashboard/run.sh` — see
  that folder's own README for the aggregation math, the calibration
  corrections, the trading-concentration correction, and how to add a new
  source or asset. Tests: `cd wisdom-dashboard/backend && python3 -m pytest`.

- **[`scripts/run_btc_price_market_calibration.py`](scripts/run_btc_price_market_calibration.py)**
  (backed by [`src/btc_price_market_calibration.py`](src/btc_price_market_calibration.py)) —
  scores 97 resolved Polymarket "Bitcoin price on `<date>`" markets against
  realized BTC price, by lead time. Findings and plots in
  [`results/btc_price_market_calibration/report.md`](results/btc_price_market_calibration/report.md).

  Two results, pulling in opposite directions. **Bucket probabilities are
  usable**: mid-range bins track the diagonal, and the cheap tail shows a
  clean, reproducible favorite-longshot bias (the ~5c bucket resolves Yes
  0.8-3.6% of the time depending on horizon) that `aggregation.py` corrects
  for as documented, tunable constants. **Point and interval forecasts are
  not**: the implied mean loses to "assume nothing changes" by 34-43% at
  every lead time, the median is no better, and the whole distribution
  loses to a random walk widened by trailing realized volatility — 23-44%
  worse on CRPS, and far worse calibrated (at a 6h lead the market's 68%
  interval covers 41%, the random walk's covers 67%). The dashboard's cards
  were restructured to lead with the probabilities and demote the point
  estimate as a result.

- **[`src/aggregate_forecast_backtest.py`](src/aggregate_forecast_backtest.py)** —
  the dashboard's central claim, finally measured: does a volume-weighted
  mixture of two platforms beat either platform alone? Runs real historical
  Polymarket and Kalshi quotes through the shipped aggregation engine
  (imported, not reimplemented) and scores aggregate vs each single source
  vs naive spot vs a random walk. See
  [`results/aggregate_forecast_backtest/report.md`](results/aggregate_forecast_backtest/report.md).

  The test window is the final hour before resolution, because that is the
  only window where both platforms quote the same instant: Polymarket's
  daily events open 7 days out and resolve at 16:00:00Z, while Kalshi's
  `KXBTCD` is a series of one-hour markets. That constraint is itself a
  finding about the live dashboard — see the report.

- **[`src/polymarket_trader_concentration.py`](src/polymarket_trader_concentration.py)** —
  checks whether a market's volume represents a broad, independent set of
  traders or a handful of large wallets (and whether trades show a
  herding/momentum signature), using Polymarket's public wallet-attributed
  trade feed. Findings across 6 live BTC/OIL markets are in
  [`results/polymarket_trader_concentration/report.md`](results/polymarket_trader_concentration/report.md) —
  every market checked showed real concentration and consistent positive
  trade-direction autocorrelation. The dashboard's `backend/concentration.py`
  applies the concentration half of this as a weight discount *and* as an
  adjustment to the confidence tier the reader actually sees (Polymarket
  only — Kalshi/Manifold don't expose the trade-level data this needs).

- **[`src/prediction_market_trader_skill.py`](src/prediction_market_trader_skill.py)** —
  have individual Polymarket traders been good at predicting BTC and oil,
  and is it skill or noise? Scores every trade (219k BTC trades across 97
  resolved daily events; 66k OIL trades across 16 resolved touch events)
  against what happened, then tests whether a wallet's edge in the first
  half of the sample predicts its edge in the second, independent half.
  Full findings in
  [`results/prediction_market_trader_skill/report.md`](results/prediction_market_trader_skill/report.md).

  Because per-wallet edge is extremely heavy-tailed, every correlation is
  reported as raw Pearson, Spearman, and winsorized Pearson, each with its
  own permutation p-value. And because a wallet that only sells overpriced
  longshots would show persistence without forecasting anything, the test
  is re-run with each trade's price-level component removed.

  **BTC: persistence survives the control** (Spearman 0.240 → 0.219,
  p<0.001) — but it is driven mostly by bad traders reliably staying bad;
  the top decile in period 1 underperformed the population in period 2.
  **OIL: it does not survive** (0.202 → 0.078, p=0.13), with 46% of
  population P&L explained by price level alone. Not wired into the live
  dashboard — a standalone finding.

## Layout

```
wisdom-dashboard/    the live app (own README, own tests)
src/                 research modules
scripts/             runners that produce results/
results/             reports, plots, and result JSON, one folder per study
tests/               tests for src/ (wisdom-dashboard has its own)
data/raw/            fetch cache, gitignored
```

## Running the research

```
python3 -m pip install numpy pandas pytest matplotlib
python3 scripts/run_btc_price_market_calibration.py
python3 scripts/run_aggregate_forecast_backtest.py
python3 scripts/run_prediction_market_trader_skill.py
python3 -m pytest tests/
```

Every fetch is cached under `data/raw/`, so re-runs are cheap and a first
run is not. Ground-truth price series are fetched from yfinance where
available and fall back to Coinbase Exchange; which one a run used is
recorded in its results JSON rather than assumed.
