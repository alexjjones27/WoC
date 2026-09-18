# WoC (Wisdom of Crowds)

Four pieces, built together:

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

- **[`equity-forecaster/`](equity-forecaster/)** — the same wisdom-of-crowds
  question asked of the sell-side: can per-analyst price targets be aggregated
  into a useful probability distribution over a stock's price 12 months out?
  Built as a research pipeline, not a tool — an append-only point-in-time store,
  a data-quality gate that has to pass before anything downstream is allowed to
  run, and a Stage 2 de-biaser that strips each firm's habitual multiple of
  spot, an empirically fitted age decay (fitted, or explicitly refused — never
  an assumed half-life), and the beta-scaled sector move. **Stages 1–2 are
  built; the validation stages that would decide whether any of it beats spot
  are deliberately not**, and every report says so. No vendor credential was
  available, so the demo runs on a clearly-labelled simulated panel over real
  prices; see that folder's README for what is verified, what is not, and the
  list of known limitations.

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
