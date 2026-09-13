# Wisdom of the Markets

A dashboard that pulls forward-looking price information from multiple
market-based sources and aggregates it into one probabilistic forecast per
asset per future date.

**Phase 1 (built, live):** Bitcoin and Crude Oil (WTI), via Polymarket,
Kalshi, and Manifold prediction markets.
**Phase 2/3 (designed, not wired up):** options-implied distributions
(Deribit) and futures/perpetual curves. The adapter interface and
aggregation engine already support them; only the actual data-fetching
logic is unwritten. See `backend/adapters/deribit_options.py` and
`backend/adapters/perp_futures.py` for exactly what's missing and how it
would be implemented.

### Adding crude oil: what changed, and what oil currently lacks

Each Phase 1 adapter used to hardcode Bitcoin's title patterns/series
tickers; they're now keyed by a per-asset config dict at the top of each
file (`ASSET_SERIES` in `kalshi.py`, `ASSET_CONFIG` in `polymarket.py`,
`ASSET_SEARCH_TERMS` in `manifold.py`) -- adding a new asset to an
already-integrated platform means adding one row to that dict, not writing
a new file. Verified live per platform (2026-09-13) before adding OIL:

- **Kalshi** has a real oil analog to every BTC series used here (`KXWTI`
  daily ladder, `KXWTIW` weekly range, `KXWTIMAX`/`KXWTIMIN` touch) --
  currently the *only* point-in-time oil source.
- **Polymarket** has no "WTI price on `<date>`" range-bucket family --
  every WTI event found is touch-style ("What will WTI Crude Oil (WTI) hit
  ...?"), so it contributes to the touch-probability section only, not
  `fetch()`'s point-in-time pipeline.
- **Manifold**'s oil search results are all touch-style "highest/lowest
  price this year" markets, which the adapter's existing
  `shouldAnswersSumToOne` check already correctly excludes -- no live
  Manifold contribution for oil right now, same as BTC's occasional dormant
  series.
- **No historical "actual price" line yet for oil's fan chart.**
  `backend/spot_price.py` only maps BTC to a CoinGecko coin id (CoinGecko
  is crypto-only) -- oil's fan chart currently shows the forward cone with
  no historical line, which the frontend already handles gracefully rather
  than breaking. A real fix means a second spot-price source (e.g. a
  commodities feed) added to `spot_price.py`, not wired up here.

Generalizing this fix surfaced a real pre-existing bug, not just new code:
Polymarket's old single-touch-event BTC discovery returned on its *first*
regex match and silently dropped the other 3 real "what price will Bitcoin
hit" events (weekly/monthly/daily variants) that were sitting right next to
the one it kept. Oil needing to discover two touch events (not one) forced
discovery to return a list instead of one slug, which fixed BTC's touch
section too -- it was undercounting before. See `polymarket.py` and
`aggregation.py`'s `build_touch_groups` (which also needed a same-source-
same-date dedup once more than one event could come from one platform).

## Run it

```
./run.sh
```

Then open **http://localhost:8000**. First run installs Python deps and
builds the frontend (needs `python3`/`pip3` and `node`/`npm` on your PATH);
subsequent runs reuse the existing `frontend/dist` build. Force a frontend
rebuild (after editing the UI) with:

```
REBUILD_FRONTEND=1 ./run.sh
```

To run on a different port: `PORT=8080 ./run.sh`.

### Developing the frontend

`./run.sh` serves a production build. While actively editing the UI, run
the backend and Vite's dev server (hot reload) separately:

```
cd backend && python3 -m uvicorn app:app --port 8000     # terminal 1
cd frontend && npm run dev                                 # terminal 2, http://localhost:5173
```

`frontend/vite.config.ts` proxies `/api/*` from 5173 to 8000, so both point
at the same live data.

## What it shows

One card per date that has an active market: a point forecast (mean +
median), 68%/95% confidence intervals, standard deviation, a probability
histogram, a confidence/liquidity badge, and (when more than one platform
covers that date) a per-platform breakdown showing whether they agree and
how much volume backs each. Expand a card ("Details") for the full
per-platform table and round-number threshold probabilities ("62% chance
BTC > $100k by Nov 30"-style rows).

The dashboard refreshes automatically every 60s and on load; the Refresh
button forces an immediate re-fetch from every source, bypassing the
45-second server-side cache (`backend/orchestrator.py: CACHE_TTL_SECONDS`).
If every source fails on a refresh, the dashboard falls back to the last
successful result and says so (a "stale" banner) rather than going blank.

Above the date cards, a **fan chart** plots ~30 days of actual BTC price
(CoinGecko, `backend/spot_price.py`) leading up to now, then the forward
median and nested confidence bands (70/80/90/95/99%, `CONFIDENCE_LEVELS` in
`aggregation.py`) from every forecast date. Dates more than
`MAX_CONNECT_GAP_DAYS` apart (`frontend/src/components/FanChart.tsx`) are
deliberately NOT joined by a filled cone -- there's no real market data in
between (same gap as the touch-probability section below), so an isolated
date renders as its own small stacked marker with only a dashed median line
connecting it, rather than implying a smooth, data-backed cone across
~3.5 months of nothing.

Below the date cards, a separate **"Touch probability"** section shows
longer-horizon markets that answer a different question -- "does price
ever cross $X before date T" rather than "is price above $X AT T." These
are shown per-platform, side by side, and are never combined with each
other or with the point-in-time forecasts above (see `TouchForecast` in
`common/distribution.py` and `build_touch_groups` in `aggregation.py` for
why: they're independent barrier bets, not a probability distribution over
price, so a "volume-weighted mixture" of them wouldn't mean anything
statistically sound yet). This is currently the only way either platform
covers BTC much past ~1 week out.

## Architecture

```
backend/
  common/
    distribution.py   # PriceDistribution / PriceBucket -- the one shared format
    assets.py          # asset registry: which adapters apply to which ticker
  adapters/
    base.py             # the adapter contract every source implements
    polymarket.py        # Phase 1, live
    kalshi.py             # Phase 1, live
    deribit_options.py     # Phase 2, interface-complete stub
    perp_futures.py         # Phase 3, interface-complete stub
    registry.py               # name -> adapter instance lookup
  aggregation.py       # the only place that combines distributions into a forecast
  orchestrator.py      # wires registry + adapters + cache + aggregation -> API payload
  cache.py              # TTL cache + stale-fallback
  app.py                 # FastAPI: /api/assets, /api/forecast/{symbol}, serves frontend/dist
frontend/               # React + TypeScript + hand-rolled SVG charts (Vite)
```

**Why it's shaped this way:** every source type describes forward price
information differently -- Polymarket/Kalshi are discrete threshold bets,
options imply a distribution via the vol surface, futures/funding imply a
forward price and a sentiment signal. `common/distribution.py`'s
`PriceDistribution` is the one shape all of that collapses into: a
piecewise probability distribution over price at a fixed future date, plus
a liquidity-derived weight. `aggregation.py` only ever operates on lists of
`PriceDistribution` -- it has no idea whether a given one came from
Polymarket or (once built) Deribit. That's what makes "add a source" mean
"write an adapter," never "modify the aggregator."

### The aggregation math

Full detail is in `backend/aggregation.py`'s module docstring (read that
file first if you want to audit or tune the math -- it's the one place all
of this lives). Summary:

1. **Common grid.** Every source in a date-group describes its distribution
   with different bucket edges (Polymarket's $2k ranges vs. Kalshi's $100
   ladder). They're resampled onto one shared fine-grained price grid,
   spreading each bucket's probability *uniformly* within it.
2. **Per-source normalization.** Each resampled distribution is renormalized
   to sum to exactly 1.0 (guards against bid/ask noise).
3. **Volume-weighted mixture.** `aggregate_pdf = Σ(weight_s · pdf_s) / Σ(weight_s)`
   -- a platform with 10x the volume contributes 10x the probability mass to
   the combined curve. This is what makes it volume-*weighted* rather than a
   simple average.
4. **Stats from the actual (possibly skewed) aggregate curve**, not a normal
   assumption: mean/variance analytically; median and the 68%/95% intervals
   by interpolating the real cumulative distribution.
5. **Confidence score/tier**: a log-scale function of total USD volume
   behind the group (`CONFIDENCE_TIERS`, `CONFIDENCE_SCORE_REF_VOLUME` --
   both tunable constants at the top of `aggregation.py`, not derived from
   anything statistical, adjust to taste).
6. **Disagreement score**: the volume-weighted standard deviation of each
   source's *own* mean (not the mixture), as a % of the aggregate mean --
   flagged prominently (`high_divergence`) above a threshold
   (`HIGH_DIVERGENCE_PCT`) rather than being silently smoothed into a wider
   std dev. Two sources that individually agree tightly but sit far apart
   can otherwise look like one plausible curve; this is what catches that.
7. **Calibration correction**, applied to every forecast: a backtest
   (`../results/btc_price_market_calibration/report.md` in the parent
   Finance repo, `scripts/run_btc_price_market_calibration.py` to
   reproduce/extend it) scored 96 resolved Polymarket BTC markets against
   realized price and found two consistent, lead-time-dependent biases --
   buckets priced under ~10% resolve Yes less often than stated (shrunk by
   `SHRINK_BY_LEAD_HOURS`), and the stated 68% CI was badly overconfident
   close to resolution but mildly underconfident further out (rescaled by
   `CI_WIDTH_MULT_BY_LEAD_HOURS`). Both tables and the interpolation
   between their anchor points live at the top of `aggregation.py`; set
   `APPLY_CALIBRATION_CORRECTIONS = False` there to see the raw, uncorrected
   numbers. The backtest itself covers one ~3.5-month uptrending window on
   Polymarket only -- treat these as a documented starting point to refine
   with more data, not a settled result (the report's Caveats section says
   more). Each `AggregateForecast` exposes `lead_hours`,
   `longshot_shrink_applied`, and `ci_width_mult_applied` so you can see
   exactly what was applied to any given card.

### Turning noisy quotes into probabilities (per-source, before aggregation)

Two refinements sit between "raw market quote" and "bucket probability
fed to the aggregator," both in `adapters/kalshi.py` (Polymarket/Manifold
publish pre-bucketed ranges directly, so neither applies there -- see
below for what that leaves as a deferred gap):

- **Microprice, not naive midpoint.** `_market_price_and_weight` prices
  each Kalshi strike as `bid * ask_size/(bid_size+ask_size) + ask *
  bid_size/(bid_size+ask_size)` -- the standard market-microstructure
  convention of weighting each side by the OTHER side's resting size
  (heavy size stacked at the ask pulls the price toward the bid, since
  that side is more likely to get traded through). Falls back to plain
  midpoint when depth data isn't usable.
- **Isotonic regression before differencing a threshold ladder.**
  `backend/isotonic.py` fits the closest monotone (non-increasing) curve
  to a whole KXBTCD ladder's `P(price > strike)` sequence -- weighted by
  each strike's own depth/spread-derived confidence -- before it gets
  differenced into bucket probabilities. Replaces an earlier version that
  just clipped each negative difference to 0 locally, which only patches
  the single violating pair instead of finding the best fit for the whole
  curve. See `isotonic.py`'s docstring for why (and a worked example).

**Deferred, documented rather than built:** a few pieces from a more complete
market-microstructure treatment aren't done, because they need real
engineering, not just a formula swap:
- **Polymarket/Manifold order-book depth.** Both are used via their
  pre-computed market-level price (Polymarket's Gamma `outcomePrices`;
  Manifold's `answers[].probability`), not a fetched order book, so they
  don't get the microprice/depth-weighting treatment Kalshi does -- doing
  that right means an extra API call per bucket (Polymarket's CLOB
  `/book`, per token) rather than a formula change.
- **Bid-ask probability bounds as an uncertainty band**, instead of
  collapsing each quote to one point estimate. `PriceBucket` only carries
  a single `prob` today.
- **A joint cross-date model** (e.g. a shared implied-volatility term
  structure across dates, fit once and shared) so nearby-horizon forecasts
  are mutually consistent, rather than each date being reconstructed
  completely independently the way it is now. This is also the principled
  way to fill the 1-week-to-3-month gap described below with an actual
  interpolated distribution instead of a visual break in the fan chart --
  a substantial, separate piece of work, not a small addition.

### Known scope decisions (read before extending)

- **Polymarket**: only the recurring "Bitcoin price on `<date>`?" range-bucket
  events are used, not the "will BTC hit $X by [year end]" touch markets --
  those answer a different question (ever-crosses vs. price-at-a-date) and
  would bias the distribution if mixed in. See `adapters/polymarket.py`.
- **Kalshi**: two series are used -- `KXBTCD`, the hourly near-term
  threshold ladder, and `KXBTCY`, a pre-bucketed price-range event that
  resolves at year-end (currently: Jan 1, 2027). Both are genuine
  point-in-time distributions (confirmed live); `KXBTCMAXM`/`KXBTCMAXQ`/
  `KXBTCMINY` etc. are still excluded as touch/range-within-period
  products, same reasoning as Polymarket's excluded markets. Where Kalshi
  has more than one `KXBTCD` ladder closing on the same calendar date,
  only the latest is kept, to avoid silently blending two different times
  of day into one "period." See `adapters/kalshi.py`.
- **The 1-week-to-3-month gap is real, not a bug.** As of this build,
  neither platform has a *genuine point-in-time* BTC market between ~6
  days out (Polymarket's last "Bitcoin price on X") and Jan 1, 2027
  (Kalshi's `KXBTCY`) -- Kalshi's monthly/quarterly series (`KXBTCMAXM`,
  `KXBTCQ`) exist but had zero open markets when checked. That gap is
  covered only by the separate "touch probability" section described above
  (Polymarket's "What price will Bitcoin hit in 2026?", Kalshi's
  `KXBTCMAXY`/`KXBTCMINY`) -- real signal, but answering "ever crosses,"
  not "is above, at this date."
- **Cross-source-type weighting is an open question.** Phase 1 only ever
  mixes weights within one `source_type` (prediction markets), where both
  adapters use USD-ish volume, so this doesn't bite yet. Before wiring in
  Deribit or futures, decide how an options market's open interest and a
  prediction market's volume should be reconciled into one weight scale --
  flagged explicitly in `aggregation.py`'s docstring, not assumed solved.

## Other platforms considered

Asked to add Myriad Markets, PlotX, Hedgehog Markets, Overtime, and Azuro.
Verified live (not from memory) rather than guessed -- results, in case you
revisit this:

- **PlotX**: pivoted away from prediction markets entirely (site now reads
  "PlotX - Fantasy Trivia Game"); no working API.
- **Hedgehog Markets**: on-chain Solana only, no public indexer/API found.
- **Overtime**: does have BTC/ETH "Speed Markets," but both its APIs return
  `401` without an approved key issued case-by-case -- not something a
  script can just call.
- **Azuro**: excellent public API, but confirmed zero crypto-price markets
  -- sports/esports only, top to bottom.
- **Manifold Markets** (not on the original list, found while checking the
  others): real public API, real BTC range markets -- added, see below.

## Manifold Markets: included, but play-money

`adapters/manifold.py` is live, same as Polymarket/Kalshi, but with two
real differences handled explicitly rather than papered over:

- **Play money.** Manifold's token (MANA) has no redemption value. Every
  Manifold distribution's weight is multiplied by
  `PLAY_MONEY_WEIGHT_DISCOUNT` (10%, a documented judgment call, not a
  calibrated exchange rate -- none exists to calibrate against) before the
  aggregator ever sees it, so it can diversify a forecast but can't
  dominate one by raw bet volume. The dashboard tags every Manifold row
  with a "play money" badge and shows its volume as a plain number, not a
  `$` figure, in the expanded per-platform table.
- **User-generated, noisy.** Anyone can create a Manifold market, so
  discovery filters to `MULTI_NUMERIC` markets with
  `shouldAnswersSumToOne` (the same mutually-exclusive-bucket shape as
  Polymarket/Kalshi's range products) above a minimum volume floor --
  see the adapter's docstring for what that currently finds (as of this
  build: no near-term daily series is active, but a couple of far-dated
  ones are, including an Astral Codex Ten 2026 contest market).

Note: a Manifold market's own close date won't necessarily land on the
same calendar date as an existing Polymarket/Kalshi forecast for "the same"
horizon (e.g. one December-31-ish market may close Jan 1, another Jan 7) --
grouping stays exact-date, same rule as everywhere else in this module, so
these show up as their own nearby cards rather than being force-merged
into an existing one.

## Adding a new prediction-market-style source

1. Create `backend/adapters/my_source.py` implementing `SourceAdapter`
   (`backend/adapters/base.py`): a `fetch(asset: str) -> SourceFetchResult`
   that builds `PriceDistribution`/`PriceBucket` objects (`common/distribution.py`)
   and never raises (catch your own errors, put them in `SourceFetchResult.error`).
2. Register it: add one line to `ADAPTERS` in `backend/adapters/registry.py`.
3. Add its name to the relevant asset's `adapters` list in
   `backend/common/assets.py`.
4. If the source also has longer-dated *touch-probability* markets (see
   `TouchForecast` in `common/distribution.py`), override `fetch_touch()`
   too -- otherwise the base class's default (no touch data) is used and
   nothing breaks.

Nothing else changes -- the orchestrator, cache, aggregator, and frontend
all already treat every adapter identically.

## Adding a new asset (ticker)

Two steps, since covering a new asset on an already-integrated platform
(Polymarket/Kalshi/Manifold) means extending that adapter's per-asset
config, not writing a new file (see "Adding crude oil" above for exactly
what that looked like going from BTC-only to BTC+OIL):

1. Add a row for the new asset to each relevant adapter's per-asset config
   (`ASSET_SERIES` in `kalshi.py`, `ASSET_CONFIG` in `polymarket.py`,
   `ASSET_SEARCH_TERMS` in `manifold.py`) -- but verify live first what
   that platform actually offers for this asset (point-in-time range/ladder
   family? touch-only? nothing?) rather than assuming it mirrors BTC/OIL;
   set the missing pieces to `None`/omit them the way `polymarket.py` does
   for OIL's point-in-time series.
2. Add an `AssetSpec` entry to `ASSET_REGISTRY` in `backend/common/assets.py`
   with `enabled=True` and the list of adapters that cover it. It'll appear
   in the dashboard's asset selector automatically. (`AAPL`/`GOLD` are
   still listed there, disabled, to show the shape for an asset that needs
   the Phase 2/3 options/futures adapters actually built first -- neither
   has a prediction-market angle the way BTC/OIL do.)

The frontend needs no per-asset changes -- every component already takes
`asset`/`display_name` as props rather than hardcoding a ticker.

## Troubleshooting

- **A platform shows no cards / fewer than expected**: prediction markets
  open and close on their own schedule -- both platforms are queried for
  whatever's currently active, not a fixed calendar grid. Check the banner
  under the header for a per-source error message if one failed outright.
- **"stale" banner**: every source failed on the last refresh; you're
  looking at the last good fetch. Try Refresh again in a bit.
