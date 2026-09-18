# Equity price-target forecaster

A research pipeline that aggregates per-analyst sell-side price targets into a
**probability distribution** over a stock's price 12 months forward, rather than
a point estimate — and that is built to report honestly when the aggregate
carries no information.

**Status: Stages 1 and 2 are built and tested. Stages 3–8 are not.** That is the
intended stopping point: the build order is store and ingest first, then the
data-quality gate, then de-biasing, then validation, and nothing past de-biasing
gets built until the de-biased distribution has been reviewed. See
[What is deliberately not built](#what-is-deliberately-not-built).

---

## The one thing to read first

**No vendor credential was available in the environment this was built in.** The
per-analyst clients (Benzinga, FMP) are written and wired in, but without an API
key there is no real analyst panel to run on. So the pipeline ships with a
**simulated panel generator** (`src/ingest/synthetic.py`) that runs on the
ticker's *real* prices and injects the statistical pathologies the design is
built around.

That simulated panel exists to exercise and test the code. It is not data.
Nothing it produces is evidence about any real stock. Three guardrails enforce
this rather than relying on a reader noticing a caveat:

- every simulated row is stamped `source="synthetic:v1"` in the store, and every
  report prints a banner;
- every simulated firm is named `SYNTH-…`, so it cannot be mistaken for a real
  one in any output;
- the point-in-time reader **raises** rather than blending simulated and real
  rows in one analysis.

With a real key set, the same commands run the same pipeline on real records and
the synthetic path never activates — `ingest` only falls back to it when no real
source is usable *and* `--allow-synthetic` was passed explicitly.

---

## Quick start

```bash
pip install -r requirements.txt

python -m src.cli sources                              # what data is reachable
python -m src.cli demo AAPL --allow-synthetic          # ingest → quality → anchoring → de-bias
```

`demo` runs the whole sequence on one ticker. The individual stages:

```bash
python -m src.cli ingest AAPL --allow-synthetic --start 2016-01-01
python -m src.cli quality   AAPL --pit-mode assume_vendor_history
python -m src.cli anchoring AAPL --pit-mode assume_vendor_history
python -m src.cli debias    AAPL --pit-mode assume_vendor_history --csv panel.csv
```

To run on real data, set any of `BENZINGA_API_KEY`, `FMP_API_KEY`,
`FINNHUB_API_KEY` and drop `--allow-synthetic`.

```bash
python -m pytest tests/ -q      # 53 tests, no network access required
```

---

## The five design constraints, and where each is enforced

The naive version of this tool — average the price targets, compare to spot — is
known to be uninformative, and this codebase is shaped around not building it.

| Constraint | Where it lives |
|---|---|
| **1. Targets are anchored to spot.** Analysts set targets at a roughly stable multiple of prevailing price and revise *after* moves. Errors are correlated across analysts, so averaging does not cancel them. | `model/debias.py` step (a) removes each firm's own historical multiple, shrunk empirical-Bayes. `diagnostics/anchoring.py` **tests** the premise rather than assuming it: a within-firm regression of `log(PT)` on `log(spot)`. |
| **2. The signal, if any, is in the derivative.** | `model/revisions.py` computes revision rate, breadth and magnitude as first-class point-in-time outputs, not as a by-product of a composite. |
| **3. Dispersion is a penalty, not just an error bar.** | Carried as `iqr`/`sd` on every `Distribution`. The *penalty term* and the bimodality test belong to Stages 4 and 7 and are not built. |
| **4. 13F cannot produce a price.** | Not built. When it is, it enters as a conviction tilt and confidence weight, never as a point-estimate input. |
| **5. Spot is the benchmark to beat.** | Not yet tested — which is why every report ends by saying the correct prior is that the distribution carries no information until Stage 8's null test runs. |

---

## Stage 1 — the data layer

### Sources

| Source | Role | Credential | Response parsing verified? |
|---|---|---|---|
| **Yahoo** `/v8/finance/chart` | daily split-adjusted closes, split events | none | **yes** — exercised against live responses |
| **Benzinga** Analyst Ratings v2.1 | per-analyst panel (has prior rating *and* prior target) | `BENZINGA_API_KEY` | **no** — written to the published schema, untested without a key |
| **FMP** `price-target`, `upgrades-downgrades` | per-analyst panel | `FMP_API_KEY` | **no** — same |
| **Finnhub** `price-target` | **consensus benchmark only** | `FINNHUB_API_KEY` | endpoint confirmed (401 without a key); parsing untested |
| **SEC EDGAR** | 13F — Stage 6 | none | not built |

Two notes worth flagging rather than burying:

- **Finnhub cannot supply a panel.** Both endpoints the brief lists
  (`price-target`, `recommendation-trends`) return *pre-aggregated* consensus —
  high/low/mean/median, or counts of buy/hold/sell. Neither exposes a single
  analyst's action. So `ingest/finnhub.py` deliberately **refuses** to emit
  `PriceTargetRecord` rows and returns a `ConsensusSnapshot` instead: stored as
  the benchmark Stage 8's consensus test has to beat, never as an input.
- The two unverified clients keep their parsing in standalone functions
  (`parse_ratings`, `parse_price_targets`, `parse_grades`) precisely so they can
  be tested against a captured payload the moment a key exists.

### The store is append-only by construction

`store/schema.py` has no primary key a restatement could collide with, the
writers issue only `INSERT`, and `retrieved_at` is `NOT NULL` everywhere so a row
without provenance cannot physically exist.

Two identifiers do the work:

- `natural_key` — the forecast **event** (firm, analyst, ticker, action date,
  source).
- `payload_hash` — the **content** of one observation of it.

A vendor restating a historical target produces a second row with the same
natural key and a different payload hash. Both are kept. Byte-identical repeats
*are* deduped, because re-running an ingest must not multiply the panel — that is
dedupe, not overwriting.

### Point-in-time reads, and the assumption you have to name

`store/pit.py` enforces three rules: original publication wins (earliest
`retrieved_at` per event); nothing dated after the as-of date; and nothing
*known* after the as-of date.

The third rule bites, and this is the part most retail implementations get
silently wrong. If the panel was pulled in one snapshot today, every row's
`retrieved_at` is today, so a strict read as of 2022 returns **nothing** — which
is the truthful answer. A single snapshot of a vendor's back-history is not
evidence about what was knowable in 2022.

So backtesting from one snapshot requires an explicit assumption, and the reader
makes you name it:

- `pit_mode="strict"` — all three rules. The only mode whose output is safe to
  publish as evidence.
- `pit_mode="assume_vendor_history"` — rules 1 and 2 only. Asserts the vendor's
  back-history equals what was published at the time: no deleted analysts, no
  back-filled coverage, no restated targets. **Usually false in some degree**,
  and the single most common source of phantom alpha in this kind of research.
  Never the default; stamped on every frame it produces; printed by every report
  that consumes one.

---

## Stage 2 — de-biasing

Everything is computed in **log space** and reported in simple returns. In logs
each correction is a subtraction and they compose exactly; in simple returns they
compose only approximately, and the error grows precisely where the corrections
matter most.

**Before any correction**, three guards run in `attach_implied_returns`:

- **Anchor price.** `r_i = PT_i / spot_at_action_i - 1` uses the price on the
  action date from our own history — never today's price, never the vendor's
  `spot_at_action` field. The default convention is the last close *strictly
  before* the action date, because the action-date close can already contain the
  market's reaction to the target itself. Configurable, so the difference can be
  measured rather than argued about.
- **Split units.** The price series is back-adjusted; several vendors store
  targets as quoted on the day. Where a split intervened and the raw ratio is
  absurd but the split-adjusted one is sane, the split-adjusted value is used and
  the row is flagged. Without this, a 4:1 split turns a +5% target into +320%.
- **Vendor spot check.** Where the vendor supplies its own `spot_at_action`, it is
  compared against ours — a vendor that back-fills that column with the *current*
  price makes every implied return near-zero by construction, and the
  data-quality gate treats that as a critical failure.

Then the brief's three corrections:

**(a) Firm anchoring offset.** Each firm's persistent log multiple, shrunk toward
the panel mean by `B_f = (s²_f/n_f) / (τ² + s²_f/n_f)` — empirical Bayes /
James-Stein. An unshrunk firm mean on four observations injects more error than
the bias it removes. The mean shrinkage intensity is reported: near 1 means the
panel cannot tell firms apart and the correction is doing nothing. Leave-one-out
by default, so a record is never de-biased by a statistic it helped compute.

**(b) Age decay — fitted, never assumed.** `exp(-λ·age)` where λ is the slope of
`log(u²)` on age, `u = log(PT / P_{t+horizon})`: if precision decays
exponentially then an inverse-variance weight is exactly that form. Two details
change the answer — **evaluation-date fixed effects**, so λ is identified from
the spread of *ages on the same date* rather than from some periods being harder
than others; and **cluster-robust standard errors**, since every record scored on
one date shares a realised price. If the slope is not significantly positive, or
the sample is thin, or the implied half-life is absurd, the fit **refuses**: it
reports why and falls back to flat weights inside the 180-day cutoff. A half-life
is never invented.

**(c) Sector and beta adjustment.** Strips the beta-scaled sector move between
the action date and now, so what survives is the analyst's stock-specific call
rather than their sector timing. Beta is Vasicek-shrunk toward 1.0 by its own
precision — same logic as (a): a noisily estimated control adds more error than
it removes — and the weight placed on the prior is reported. A low R² is reported
but is *not* treated as a failure; for some stocks the sector genuinely explains
little, and the right response is to remove the little it does, not to pretend
otherwise.

The report prints the distribution at each step, side by side, plus the share of
cross-sectional variance explained by firm identity before and after — which is
the mechanical evidence for whether step (a) did anything.

---

## What is deliberately not built

Stages 3–8, and the brief's instruction to stop for review after Stage 2 is why.
Listing them rather than stubbing them, because empty placeholder files are worse
than absent ones:

- **Stage 3 — skill weighting.** Per-analyst MAPE, hit rate, information
  coefficient, timeliness; James-Stein shrinkage; and the **correlation penalty**
  that clusters analysts by residual correlation so five analysts copying one
  another count as roughly one opinion. The timeliness measure is already
  computed in `diagnostics/anchoring.py`, and `model/regress.py` already holds
  the shrinkage machinery.
- **Stage 4 — aggregation to a density.** Weighted KDE, bimodality test (dip or
  two-component mixture likelihood ratio).
- **Stage 5 — options-implied risk-neutral density.** Breeden–Litzenberger.
- **Stage 6 — positioning overlay.** 13F concentrated-holder change, index vs
  discretionary separation, 45-day filing-lag enforcement, short interest,
  Form 4.
- **Stage 7 — composite signal** and the abstention rules.
- **Stage 8 — validation**, which is the actual deliverable: null test against
  spot, consensus test, walk-forward long-short backtest, HAC inference, factor
  attribution, subsample stability.

Until Stage 8 runs, **the correct prior is that this distribution carries no
information**, and every report says so.

---

## Known limitations

Real ones, not ritual hedging:

1. **No real analyst panel was available.** Everything demonstrated so far runs on
   simulated records. The pipeline is tested; the *findings* are nil by
   construction.
2. **Single-ticker firm offsets are not separable from firm views.** With one
   ticker, a firm's anchoring bias cannot be told apart from its genuine opinion
   on that stock, so the correction removes some signal along with the bias. The
   warning fires below about five tickers. Real use needs the firm's whole
   coverage universe.
3. **Yahoo cannot meet the survivorship requirement.** It back-adjusts silently,
   revises, and has no usable delisted-security coverage. The brief's
   "retain delisted and acquired tickers" rule cannot be satisfied with it — that
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
    base.py              PriceTargetRecord, natural_key/payload_hash, rating map
    http.py              retrying stdlib fetcher
    prices.py            Yahoo daily closes, splits, PIT price accessors
    benzinga.py fmp.py   per-analyst clients (unverified without a key)
    finnhub.py           consensus benchmark only — refuses to emit panel rows
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
tests/                   53 tests, offline
```

The notebooks hold no logic. Every check and regression lives in a module under
`src/diagnostics/` and is unit-tested; the notebooks exist to look at the
numbers. That way the gate cannot quietly drift from what the pipeline enforces.

## Test coverage worth knowing about

`tests/test_no_lookahead.py` is the load-bearing one. It runs Stage 2 at an
as-of date, then adds a year of future prices and every analyst action taken
after that date, re-runs at the *same* as-of date, and demands that nothing
moved — not the spot, not the distribution, not the fitted beta, not λ, not the
firm offsets. It also asserts that strict mode on a single snapshot **raises**
rather than quietly guessing, and that a restated value never reaches the model.

`tests/test_debias.py` checks the firm-offset estimator against the generator's
injected ground truth (correlation > 0.9, mean absolute error < 5pp) rather than
merely checking that it produces a plausible-looking number.
