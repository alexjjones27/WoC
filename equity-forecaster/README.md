# Equity price-target forecaster

A research pipeline that aggregates per-analyst sell-side price targets into a
**probability distribution** over a stock's price 12 months forward, rather than
a point estimate, and that is built to report honestly when the aggregate
carries no information.

**Status: Stages 1 and 2 are built and tested. Stages 3–8 are not.** That is the
intended stopping point: the build order is store and ingest first, then the
data-quality gate, then de-biasing, then validation, and nothing past de-biasing
gets built until the de-biased distribution has been reviewed. See
[What is deliberately not built](#what-is-deliberately-not-built).

---

## It runs on real data, with no credential

Two keyless per-analyst sources are wired in and verified against live
responses, so nothing here depends on buying a feed:

- **`finviz`** carries the depth: firm, action date, rating and prior rating,
  price target and prior target, reaching back about two years.
- **`stockanalysis`** carries the breadth: the eight most recent actions per
  ticker, but naming the *individual analyst* and timestamping them intraday.

A full-universe pull (`ingest --universe`) currently lands **1,484 real
per-analyst records across 68 tickers, 112 firm strings (89 firms after
canonicalisation) and 330 named analysts**, plus 952 consensus benchmark rows
from Nasdaq and 212,440 daily closes from Yahoo.

A **simulated panel generator** (`src/ingest/synthetic.py`) still ships, for
testing and for exercising code paths the real data does not currently reach. It
never activates on its own: `ingest` falls back to it only when no real source is
usable *and* `--allow-synthetic` was passed explicitly. Three guardrails keep
simulated rows from being mistaken for data: every one is stamped
`source="synthetic:v1"` and triggers a banner, every simulated firm is named
`SYNTH-...`, and the point-in-time reader **raises** rather than blending
simulated and real rows in one analysis.

## What the real data actually shows

Measured, not assumed. All of it under `pit_mode="assume_vendor_history"`, which
is the caveat that matters most (see below).

**Analysts revise after the stock moves.** Regressing the log change in a firm's
target on the stock's trailing 20-day return gives **+0.887 (t = +4.48)**, SEs
clustered by firm, on 33 AAPL revisions. Close to one-for-one. That is the
brief's second design constraint, confirmed on real records rather than cited.

**Firm identity explains about 30% of the spread in implied returns.** On 1,255
records across 81 firms, firm dummies explain an adjusted **0.297** of the
variance in log implied return. Controlling for ticker barely moves it (0.296 to
0.297), which rules out the obvious confound: this is firms behaving
differently, not firms covering different stocks.

**The street sits about +14.6% above spot on average**, and firms differ
persistently around that: DZ Bank at +3.2% raw, UBS at +17.5%. But the
between-firm variance of *true* offsets is small relative to estimation noise
(tau-squared = 0.00075), so James-Stein shrinkage pulls most firms two-thirds of
the way back to the panel mean. Firms are less distinguishable than their raw
means suggest, which is exactly why the shrinkage intensity is reported.

**Targets are sticky, not fixed multiples.** The within-firm slope of log(PT) on
log(spot) is **0.75**, below one-for-one: targets under-adjust and trail the
price. That has a consequence worth internalising. After a run-up the implied
return compresses or goes negative for the whole panel at once, which is an
artifact of the lag and not a bearish view. AAPL as of 2026-09-18 shows exactly
that, with a raw median implied return of **-2.8%** against a spot of $335.50.

**The age decay refuses to fit, correctly.** Two years of history cannot support
measuring how 12-month forecast accuracy degrades with age: the fit needs 200
(age, error) pairs over at least 12 evaluation dates and finds 28 over 0. It says
so and falls back to flat weights inside the 180-day cutoff rather than inventing
a half-life. The same shortage blocks the level-versus-derivative test, which
needs 24 month-ends with completed horizons and has 5.

**So depth, not access, is what is now binding.**

## Quick start

```bash
pip install -r requirements.txt

python -m src.cli sources                     # what data is reachable right now
python -m src.cli ingest --universe           # ~68 tickers of REAL analyst data (~12 min)
python -m src.cli debias AAPL --pit-mode assume_vendor_history
```

The individual stages:

```bash
python -m src.cli ingest AAPL MSFT NVDA --start 2016-01-01
python -m src.cli quality   AAPL --pit-mode assume_vendor_history
python -m src.cli anchoring AAPL --pit-mode assume_vendor_history
python -m src.cli debias    AAPL --pit-mode assume_vendor_history --csv panel.csv
python -m src.cli demo      AAPL              # all four, one ticker
```

Setting `BENZINGA_API_KEY` or `FMP_API_KEY` adds those sources; nothing requires
them. `--allow-synthetic` falls back to the simulated panel when no real source
works.

```bash
python -m pytest tests/ -q      # 73 tests, no network access required
```

---

## The five design constraints, and where each is enforced

The naive version of this tool (average the price targets, compare to spot) is
known to be uninformative, and this codebase is shaped around not building it.

| Constraint | Where it lives |
|---|---|
| **1. Targets are anchored to spot.** Analysts set targets at a roughly stable multiple of prevailing price and revise *after* moves. Errors are correlated across analysts, so averaging does not cancel them. | `model/debias.py` step (a) removes each firm's own historical multiple, shrunk empirical-Bayes. `diagnostics/anchoring.py` **tests** the premise rather than assuming it: a within-firm regression of `log(PT)` on `log(spot)`. |
| **2. The signal, if any, is in the derivative.** | `model/revisions.py` computes revision rate, breadth and magnitude as first-class point-in-time outputs, not as a by-product of a composite. |
| **3. Dispersion is a penalty, not just an error bar.** | Carried as `iqr`/`sd` on every `Distribution`. The *penalty term* and the bimodality test belong to Stages 4 and 7 and are not built. |
| **4. 13F cannot produce a price.** | Not built. When it is, it enters as a conviction tilt and confidence weight, never as a point-estimate input. |
| **5. Spot is the benchmark to beat.** | Not yet tested. This is why every report ends by saying the correct prior is that the distribution carries no information until Stage 8's null test runs. |

---

## Stage 1: the data layer

### Sources

| Source | Role | Credential | Verified? |
|---|---|---|---|
| **Yahoo** `/v8/finance/chart` | daily split-adjusted closes, split events | none | **yes**, against live responses |
| **Finviz** `/quote.ashx` | per-analyst panel, ~2 years deep, firm level | none | **yes**, 12 tickers live |
| **stockanalysis.com** `/ratings/__data.json` | per-analyst panel, 8 most recent, names the analyst | none | **yes**, 12 tickers live |
| **Nasdaq** `/api/analyst/<s>/targetprice` | **consensus benchmark only**, ~13 dated monthly points | none | **yes**, against live responses |
| **Benzinga** Analyst Ratings v2.1 | per-analyst panel | `BENZINGA_API_KEY` | **no**, written to the published schema |
| **FMP** `price-target`, `upgrades-downgrades` | per-analyst panel | `FMP_API_KEY` | **no**, same |
| **Finnhub** `price-target` | **consensus benchmark only** | `FINNHUB_API_KEY` | endpoint confirmed (401); parsing untested |
| **SEC EDGAR** | 13F (Stage 6) | none | not built |

**Terms of use.** Finviz's robots.txt permits `/quote.ashx` and `/stock`;
stockanalysis.com's disallows only `/e/` and `/p/`. Neither is the same as a
site's terms of service, and Finviz sells a tier that includes data export. Both
clients sleep a second between tickers and the pipeline needs at most one request
per ticker per day. Read both sites' terms before running this on a schedule. If
you have a university affiliation, **WRDS/IBES is the genuinely clean option**
and is usually free to students and faculty: properly point-in-time, with real
delisted coverage, which would also fix limitation 3 below.

Two notes worth flagging rather than burying:

- **Finnhub and Nasdaq cannot supply a panel.** Both return only *pre-aggregated*
  consensus: high/low/mean/median, or counts of buy/hold/sell. Neither exposes a
  single analyst's action. So both clients deliberately **refuse** to emit
  `PriceTargetRecord` rows and return `ConsensusSnapshot`s instead, stored as the
  benchmark Stage 8's consensus test has to beat, never as an input. A test
  asserts the refusal.
- **stockanalysis.com ships its own analyst skill scores, and they are poison.**
  Each record carries that analyst's success rate, average return and rank,
  computed over their *entire* history including everything after the record's
  date. Using them to weight a 2025 forecast would be scoring it with knowledge
  of how it turned out. They are stored under the deliberately awkward key
  `lookahead_contaminated_scores` so they cannot be reached for by accident, and
  are good for exactly one thing: cross-checking the point-in-time skill
  estimates Stage 3 will compute for itself.
- **Firm names are canonicalised at read time, never in the store.** Vendors
  spell one firm several ways (`J.P. Morgan` / `JP Morgan`, `BofA Securities` /
  `Bank of America Securities`, `BNP Paribas Exane` / `Exane BNP Paribas`), and
  the fragmentation is not cosmetic: each spelling gets its own noisy offset,
  each shrunk harder than the combined firm would be, and Stage 3 would count one
  firm as two independent opinions. `base.canonical_firm` collapses the 112
  observed strings to 89 firms. It is derived in the read path because the store
  holds what vendors published, and a canonical name is an opinion about that
  which should stay revisable without rewriting history.
- The two unverified clients keep their parsing in standalone functions
  (`parse_ratings`, `parse_price_targets`, `parse_grades`) precisely so they can
  be tested against a captured payload the moment a key exists.

### The store is append-only by construction

`store/schema.py` has no primary key a restatement could collide with, the
writers issue only `INSERT`, and `retrieved_at` is `NOT NULL` everywhere so a row
without provenance cannot physically exist.

Two identifiers do the work:

- `natural_key`: the forecast **event** (firm, analyst, ticker, action date,
  source).
- `payload_hash`: the **content** of one observation of it.

A vendor restating a historical target produces a second row with the same
natural key and a different payload hash. Both are kept. Byte-identical repeats
*are* deduped, because re-running an ingest must not multiply the panel. That is
dedupe, not overwriting.

### Point-in-time reads, and the assumption you have to name

`store/pit.py` enforces three rules: original publication wins (earliest
`retrieved_at` per event); nothing dated after the as-of date; and nothing
*known* after the as-of date.

The third rule bites, and this is the part most retail implementations get
silently wrong. If the panel was pulled in one snapshot today, every row's
`retrieved_at` is today, so a strict read as of 2022 returns **nothing**, which
is the truthful answer. A single snapshot of a vendor's back-history is not
evidence about what was knowable in 2022.

So backtesting from one snapshot requires an explicit assumption, and the reader
makes you name it:

- `pit_mode="strict"`: all three rules. The only mode whose output is safe to
  publish as evidence.
- `pit_mode="assume_vendor_history"`: rules 1 and 2 only. Asserts the vendor's
  back-history equals what was published at the time: no deleted analysts, no
  back-filled coverage, no restated targets. **Usually false in some degree**,
  and the single most common source of phantom alpha in this kind of research.
  Never the default; stamped on every frame it produces; printed by every report
  that consumes one.

---

## Stage 2: de-biasing

Everything is computed in **log space** and reported in simple returns. In logs
each correction is a subtraction and they compose exactly; in simple returns they
compose only approximately, and the error grows precisely where the corrections
matter most.

**Before any correction**, three guards run in `attach_implied_returns`:

- **Anchor price.** `r_i = PT_i / spot_at_action_i - 1` uses the price on the
  action date from our own history, never today's price, never the vendor's
  `spot_at_action` field. The default convention is the last close *strictly
  before* the action date, because the action-date close can already contain the
  market's reaction to the target itself. Configurable, so the difference can be
  measured rather than argued about.
- **Split units.** The price series is back-adjusted; several vendors store
  targets as quoted on the day. Where a split intervened and the raw ratio is
  absurd but the split-adjusted one is sane, the split-adjusted value is used and
  the row is flagged. Without this, a 4:1 split turns a +5% target into +320%.
- **Vendor spot check.** Where the vendor supplies its own `spot_at_action`, it is
  compared against ours. A vendor that back-fills that column with the *current*
  price makes every implied return near-zero by construction, and the
  data-quality gate treats that as a critical failure.

Then the brief's three corrections:

**(a) Firm anchoring offset.** Each firm's persistent log multiple, shrunk toward
the panel mean by `B_f = (s²_f/n_f) / (τ² + s²_f/n_f)`: empirical Bayes /
James-Stein. An unshrunk firm mean on four observations injects more error than
the bias it removes. The mean shrinkage intensity is reported: near 1 means the
panel cannot tell firms apart and the correction is doing nothing. Leave-one-out
by default, so a record is never de-biased by a statistic it helped compute.

**(b) Age decay, fitted and never assumed.** `exp(-λ·age)` where λ is the slope of
`log(u²)` on age, `u = log(PT / P_{t+horizon})`: if precision decays
exponentially then an inverse-variance weight is exactly that form. Two details
change the answer. **Evaluation-date fixed effects**, so λ is identified from
the spread of *ages on the same date* rather than from some periods being harder
than others; and **cluster-robust standard errors**, since every record scored on
one date shares a realised price. If the slope is not significantly positive, or
the sample is thin, or the implied half-life is absurd, the fit **refuses**: it
reports why and falls back to flat weights inside the 180-day cutoff. A half-life
is never invented.

**(c) Sector and beta adjustment.** Strips the beta-scaled sector move between
the action date and now, so what survives is the analyst's stock-specific call
rather than their sector timing. Beta is Vasicek-shrunk toward 1.0 by its own
precision, on the same logic as (a): a noisily estimated control adds more error than
it removes. The weight placed on the prior is reported. A low R² is reported
but is *not* treated as a failure; for some stocks the sector genuinely explains
little, and the right response is to remove the little it does, not to pretend
otherwise.

The report prints the distribution at each step, side by side, plus the share of
cross-sectional variance explained by firm identity before and after, which is
the mechanical evidence for whether step (a) did anything.

---

## What is deliberately not built

Stages 3–8, and the brief's instruction to stop for review after Stage 2 is why.
Listing them rather than stubbing them, because empty placeholder files are worse
than absent ones:

- **Stage 3, skill weighting.** Per-analyst MAPE, hit rate, information
  coefficient, timeliness; James-Stein shrinkage; and the **correlation penalty**
  that clusters analysts by residual correlation so five analysts copying one
  another count as roughly one opinion. The timeliness measure is already
  computed in `diagnostics/anchoring.py`, and `model/regress.py` already holds
  the shrinkage machinery.
- **Stage 4, aggregation to a density.** Weighted KDE, bimodality test (dip or
  two-component mixture likelihood ratio).
- **Stage 5, options-implied risk-neutral density.** Breeden–Litzenberger.
- **Stage 6, positioning overlay.** 13F concentrated-holder change, index vs
  discretionary separation, 45-day filing-lag enforcement, short interest,
  Form 4.
- **Stage 7, the composite signal** and the abstention rules.
- **Stage 8, validation**, which is the actual deliverable: null test against
  spot, consensus test, walk-forward long-short backtest, HAC inference, factor
  attribution, subsample stability.

Until Stage 8 runs, **the correct prior is that this distribution carries no
information**, and every report says so.

---

## Known limitations

Real ones, not ritual hedging:

1. **Two years of history is the binding constraint now, not access.** The free
   sources are a *current snapshot* of back-history, readable only under
   `pit_mode="assume_vendor_history"`. Two years cannot support the age-decay
   fit, the level-versus-derivative test, Stage 3's skill weights or Stage 8's
   walk-forward. The fix costs nothing but time: the store is append-only with
   `retrieved_at` on every row, so a daily snapshot turns a non-point-in-time
   source into a genuinely point-in-time dataset going forward, and in about
   twelve months `pit_mode="strict"` becomes readable.
2. **Firm offsets are now fitted across the coverage universe**, which fixes the
   earlier single-ticker problem: on one ticker a firm's anchoring bias cannot be
   told apart from its view on that stock. `run_debias` fits on all 68 tickers by
   default (`cross_ticker_offsets=True`), and the separability warning fires below
   about five.
3. **Yahoo cannot meet the survivorship requirement.** It back-adjusts silently,
   revises, and has no usable delisted-security coverage. The brief's
   "retain delisted and acquired tickers" rule cannot be satisfied with it. That
   needs CRSP or an equivalent.
4. **Dividends are ignored.** A price target is a price forecast, so realised
   performance is measured on the split-adjusted price series. This understates
   realised return by the dividend yield.
5. **Age decay partly measures selection.** Older records at a given evaluation
   date come from firms that *chose not to update*, which is not random. Some of
   any measured decay is that selection rather than information decaying.
6. **Sector proxies are a hand-maintained ETF map.** Real use takes GICS from the
   same vendor that supplies the targets; unmapped tickers fall back to SPY and
   the output records that as a reduced-quality adjustment.
7. **`fiscal_year_covered` is carried but not used.** Targets nominally share a
   12-month horizon, but firms differ, and nothing yet normalises for it.

---

## Layout

```
src/
  config.py              named constants; nothing fitted lives here
  cli.py                 sources | ingest | quality | anchoring | debias | demo
  pipeline.py            ingest → store → PIT read → Stage 2
  report.py              the terminal report
  ingest/
    base.py              PriceTargetRecord, natural_key/payload_hash, rating map,
                         canonical_firm
    http.py              retrying stdlib fetcher
    prices.py            Yahoo daily closes, splits, PIT price accessors
    finviz.py            per-analyst panel, keyless, ~2 years deep
    stockanalysis.py     per-analyst panel, keyless, names the analyst
    nasdaq.py            consensus benchmark; refuses to emit panel rows
    benzinga.py fmp.py   per-analyst clients (unverified without a key)
    finnhub.py           consensus benchmark; refuses to emit panel rows
    synthetic.py         SIMULATED panel + injected ground truth
  store/
    schema.py            append-only DuckDB schema
    writer.py            INSERT-only writers, restatement-aware
    pit.py               point-in-time reads; strict vs named-assumption modes
  model/
    debias.py            Stage 2 (a)(b)(c) + Distribution summaries
    revisions.py         revision rate / breadth / magnitude, monthly snapshots
    regress.py           cluster-robust and Newey-West OLS
  diagnostics/
    data_quality.py      the gate (notebook 01)
    anchoring.py         the anchoring tests (notebook 02)
notebooks/
  01_data_quality.ipynb        thin wrappers over the tested modules, so the
  02_anchoring_evidence.ipynb  analysis cannot diverge from what runs
tests/                   73 tests, offline
```

The notebooks hold no logic. Every check and regression lives in a module under
`src/diagnostics/` and is unit-tested; the notebooks exist to look at the
numbers. That way the gate cannot quietly drift from what the pipeline enforces.

## Test coverage worth knowing about

`tests/test_no_lookahead.py` is the load-bearing one. It runs Stage 2 at an
as-of date, then adds a year of future prices and every analyst action taken
after that date, re-runs at the *same* as-of date, and demands that nothing
moved: not the spot, not the distribution, not the fitted beta, not λ, not the
firm offsets. It also asserts that strict mode on a single snapshot **raises**
rather than quietly guessing, and that a restated value never reaches the model.

`tests/test_debias.py` checks the firm-offset estimator against the generator's
injected ground truth (correlation > 0.9, mean absolute error < 5pp) rather than
merely checking that it produces a plausible-looking number.

`tests/test_ingest_clients.py` parses trimmed copies of real captured responses,
so a silent upstream schema change shows up as a failing test rather than as an
empty panel. It also pins the things that were checked rather than assumed: that
Finviz timestamps read as UTC, that Nasdaq's current high/low band is not
back-filled onto its 2025 observations, and that both consensus clients refuse to
emit panel rows at all.
