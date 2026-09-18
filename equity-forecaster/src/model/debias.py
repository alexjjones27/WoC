"""Stage 2: turn raw price targets into de-biased, comparable implied returns.

The brief's three corrections, in the order they are applied:

a. **Firm-level anchoring correction.** Sell-side targets sit at a roughly
   stable multiple of prevailing price, and that multiple differs by firm and
   persists. Subtracting each firm's own historical multiple leaves the part of
   the forecast that is about THIS stock at THIS time.

b. **Age decay.** A target set eight months ago is a forecast about a price
   level that the stock has since walked away from. Records are weighted by
   ``exp(-lambda * age)`` where lambda is FITTED from how forecast accuracy
   actually degrades with age -- never hard-coded. When the fit fails its own
   significance test, the module says so and falls back to flat weights inside
   a hard age cutoff rather than inventing a half-life.

c. **Sector adjustment.** A target set into a sector rally is partly a bet on
   the sector. Stripping the beta-scaled sector move between the action date
   and now leaves the analyst's stock-specific call.

Everything is computed in LOG space and reported in simple returns. In logs
each correction is a subtraction and the corrections compose exactly; in simple
returns they only compose approximately, and the approximation error grows
precisely where the corrections matter most (big moves, long gaps).

Two things this module is NOT
-----------------------------
It is not a signal. The distribution it produces is the input to Stages 3-7,
not a forecast: there is no skill weighting, no correlation penalty, no options
anchor, and no abstention logic yet. The summary object says so.

It also does not claim that de-biasing improves prediction. That claim belongs
to Stage 8 and has not been tested. What is shown here is mechanical: how much
of the cross-sectional spread in implied returns is firm identity rather than
disagreement about the stock.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .. import config
from ..ingest.prices import PriceHistory, spot_at_action
from .regress import ols_cluster

# ---------------------------------------------------------------------------
# Distribution summary
# ---------------------------------------------------------------------------


def weighted_quantile(values, weights, q) -> float:
    """Weighted quantile using the standard (cw - w/2) / sum(w) plotting rule.

    Chosen over the naive cumulative-weight rule because the latter is biased
    toward the upper tail for small panels, and an analyst panel is always a
    small panel.
    """
    v = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    ok = np.isfinite(v) & np.isfinite(w) & (w > 0)
    v, w = v[ok], w[ok]
    if v.size == 0:
        return float("nan")
    order = np.argsort(v)
    v, w = v[order], w[order]
    cw = np.cumsum(w)
    pos = (cw - 0.5 * w) / cw[-1]
    return float(np.interp(q, pos, v))


def effective_n(weights) -> float:
    """Kish effective sample size: (sum w)^2 / sum(w^2)."""
    w = np.asarray(weights, dtype=float)
    w = w[np.isfinite(w) & (w > 0)]
    if w.size == 0:
        return 0.0
    return float(w.sum() ** 2 / (w**2).sum())


@dataclass
class Distribution:
    """Summary of a weighted panel of implied returns, in simple-return units."""

    n: int
    n_eff: float
    median: float
    p25: float
    p75: float
    p10: float
    p90: float
    mean: float
    sd: float
    iqr: float

    @classmethod
    def summarise(cls, values, weights=None) -> "Distribution":
        v = np.asarray(values, dtype=float)
        w = np.ones_like(v) if weights is None else np.asarray(weights, dtype=float)
        ok = np.isfinite(v) & np.isfinite(w) & (w > 0)
        v, w = v[ok], w[ok]
        if v.size == 0:
            nan = float("nan")
            return cls(0, 0.0, nan, nan, nan, nan, nan, nan, nan, nan)
        mean = float(np.average(v, weights=w))
        var = float(np.average((v - mean) ** 2, weights=w))
        p25 = weighted_quantile(v, w, 0.25)
        p75 = weighted_quantile(v, w, 0.75)
        return cls(
            n=int(v.size),
            n_eff=effective_n(w),
            median=weighted_quantile(v, w, 0.50),
            p25=p25,
            p75=p75,
            p10=weighted_quantile(v, w, 0.10),
            p90=weighted_quantile(v, w, 0.90),
            mean=mean,
            sd=math.sqrt(max(var, 0.0)),
            iqr=p75 - p25,
        )

    def as_prices(self, spot: float) -> dict:
        return {
            "median": spot * (1 + self.median),
            "p25": spot * (1 + self.p25),
            "p75": spot * (1 + self.p75),
            "p10": spot * (1 + self.p10),
            "p90": spot * (1 + self.p90),
        }


# ---------------------------------------------------------------------------
# Step 0: implied returns, with the corporate-action and vendor-spot guards
# ---------------------------------------------------------------------------


def attach_implied_returns(
    panel: pd.DataFrame,
    history: PriceHistory,
    *,
    convention: str = config.SPOT_AT_ACTION_CONVENTION,
    extreme: float = config.EXTREME_IMPLIED_RETURN,
    vendor_spot_tolerance: float = 0.05,
) -> pd.DataFrame:
    """Compute ``r_i = PT_i / spot_at_action_i - 1`` and flag what went wrong.

    Three guards run here, each of which silently ruins the signal if omitted:

    * **Anchor price.** ``spot_at_action`` is resolved from our own price
      history at the action date (see ``config.SPOT_AT_ACTION_CONVENTION``),
      not from today's price, and not from the vendor's field.

    * **Split units.** The price series is back-adjusted; some vendors store
      targets as quoted on the day. Where a split intervened and the raw ratio
      is absurd but the split-adjusted one is sane, the split-adjusted value is
      used and the row is flagged ``split_rescued``. Where both are absurd the
      row is flagged ``extreme`` and excluded from fitting rather than winsorised
      into looking reasonable.

    * **Vendor spot check.** Where the vendor supplies its own
      ``spot_at_action``, it is compared with ours. A vendor that back-fills
      that field with the CURRENT price produces rows whose "implied return"
      is near zero by construction; ``vendor_spot_mismatch`` counts them so the
      data-quality report can catch it instead of the backtest inheriting it.
    """
    df = panel.copy()
    n = len(df)
    cols = {
        "spot_pit": np.full(n, np.nan),
        "spot_price_date": [None] * n,
        "spot_note": [""] * n,
        "split_factor": np.ones(n),
        "price_target_used": np.full(n, np.nan),
        "log_implied": np.full(n, np.nan),
        "implied_return": np.full(n, np.nan),
        "flag_no_spot": np.zeros(n, dtype=bool),
        "flag_no_target": np.zeros(n, dtype=bool),
        "flag_extreme": np.zeros(n, dtype=bool),
        "flag_split_rescued": np.zeros(n, dtype=bool),
        "flag_vendor_spot_mismatch": np.zeros(n, dtype=bool),
        "vendor_spot_rel_error": np.full(n, np.nan),
    }

    for i, (_, row) in enumerate(df.iterrows()):
        pt = row.get("price_target")
        action = row["action_date"]
        if isinstance(action, pd.Timestamp):
            action = action.date()

        pdate, spot, note = spot_at_action(history, action, convention)
        cols["spot_price_date"][i] = pdate
        cols["spot_note"][i] = note
        if spot is None or not np.isfinite(spot) or spot <= 0:
            cols["flag_no_spot"][i] = True
            continue
        cols["spot_pit"][i] = spot

        vendor_spot = row.get("vendor_spot_at_action")
        if vendor_spot is not None and np.isfinite(vendor_spot) and vendor_spot > 0:
            rel = abs(vendor_spot / spot - 1.0)
            cols["vendor_spot_rel_error"][i] = rel
            cols["flag_vendor_spot_mismatch"][i] = rel > vendor_spot_tolerance

        if pt is None or not np.isfinite(pt) or pt <= 0:
            cols["flag_no_target"][i] = True
            continue

        factor = history.split_factor_after(action)
        cols["split_factor"][i] = factor
        r_raw = pt / spot - 1.0
        used = pt
        if factor != 1.0:
            r_adj = (pt / factor) / spot - 1.0
            if abs(r_raw) > extreme and abs(r_adj) <= extreme:
                used = pt / factor
                cols["flag_split_rescued"][i] = True
                r_raw = r_adj
        cols["price_target_used"][i] = used
        cols["implied_return"][i] = r_raw
        cols["log_implied"][i] = math.log(used / spot)
        cols["flag_extreme"][i] = abs(r_raw) > extreme

    for k, v in cols.items():
        df[k] = v
    df["usable"] = (
        ~df["flag_no_spot"] & ~df["flag_no_target"] & ~df["flag_extreme"]
    )
    return df


# ---------------------------------------------------------------------------
# Step a: firm anchoring offsets, empirical-Bayes shrunk
# ---------------------------------------------------------------------------


@dataclass
class FirmOffsets:
    table: pd.DataFrame
    mu_global: float
    tau2: float
    mean_shrinkage: float
    n_firms: int
    n_tickers: int
    cutoff: date | None
    warnings: list[str] = field(default_factory=list)


def estimate_firm_offsets(
    panel: pd.DataFrame,
    *,
    cutoff: date | None = None,
    leave_one_out: bool = True,
    min_obs: int = config.FIRM_OFFSET_MIN_OBS,
) -> FirmOffsets:
    """Empirical-Bayes estimate of each firm's persistent log anchoring offset.

    The unshrunk firm mean is a terrible estimator here: a firm with four
    observations has a standard error on its mean of roughly half its own
    dispersion, and subtracting that noisy number from every one of its
    forecasts injects more error than the bias it removes. So each firm's mean
    is shrunk toward the panel mean by

        ``B_f = (s2_f / n_f) / (tau2 + s2_f / n_f)``

    where ``tau2`` is the estimated between-firm variance of TRUE offsets --
    total dispersion of firm means minus the part explained by sampling noise.
    This is the James-Stein/empirical-Bayes rule the brief asks for in Stage 3,
    applied here because the same small-sample problem arises a stage earlier.
    ``mean_shrinkage`` is reported as a diagnostic: near 1 means the panel is
    too thin to distinguish firms at all, and firm de-biasing is doing nothing.

    ``leave_one_out`` removes each record's own contribution to its firm mean,
    so a record is never de-biased by a statistic it helped compute.

    Point-in-time: pass ``cutoff`` to restrict the estimate to actions strictly
    before a date. Walk-forward use must refit at every rebalance.

    LIMITATION, and it is a real one: with a single ticker in the panel, a
    firm's offset cannot be separated from that firm's genuine view on that
    stock. Subtracting it then removes signal along with bias. The warning
    fires whenever fewer than about five tickers are present.
    """
    df = panel
    if cutoff is not None:
        ad = pd.to_datetime(df["action_date"]).dt.date
        df = df[ad < cutoff]
    df = df[df.get("usable", True) & df["log_implied"].notna()]

    warnings: list[str] = []
    n_tickers = int(df["ticker"].nunique()) if len(df) else 0
    if n_tickers < 5:
        warnings.append(
            f"firm offsets estimated on only {n_tickers} ticker(s): a firm's "
            "anchoring bias is not separable from its genuine view on the "
            "stock, so this correction removes some signal along with the bias"
        )

    if df.empty:
        return FirmOffsets(
            table=pd.DataFrame(
                columns=["analyst_firm", "n_obs", "raw_mean_log", "sd_log",
                         "shrinkage", "mu_log", "mu_return"]
            ),
            mu_global=float("nan"), tau2=float("nan"), mean_shrinkage=float("nan"),
            n_firms=0, n_tickers=0, cutoff=cutoff,
            warnings=warnings + ["no usable records"],
        )

    g = df.groupby("analyst_firm")["log_implied"]
    stats = pd.DataFrame({"n_obs": g.size(), "raw_mean_log": g.mean(), "sd_log": g.std(ddof=1)})
    # Firms with a single observation carry no dispersion of their own; give
    # them the pooled within-firm dispersion so their shrinkage is well defined.
    pooled_sd = float(np.sqrt(np.nanmean(stats.loc[stats["n_obs"] >= 2, "sd_log"] ** 2))) \
        if (stats["n_obs"] >= 2).any() else float(df["log_implied"].std(ddof=1) or 0.0)
    sd_f = stats["sd_log"].fillna(pooled_sd).replace(0.0, pooled_sd)
    s2_f = sd_f**2

    eligible = stats["n_obs"] >= 2
    mu_global = float(stats.loc[eligible, "raw_mean_log"].mean()) if eligible.any() \
        else float(stats["raw_mean_log"].mean())
    if eligible.sum() >= 2:
        between = float(stats.loc[eligible, "raw_mean_log"].var(ddof=1))
        noise = float((s2_f[eligible] / stats.loc[eligible, "n_obs"]).mean())
        tau2 = max(between - noise, 0.0)
    else:
        tau2 = 0.0
        warnings.append("fewer than 2 firms with >=2 observations: tau2 set to 0, "
                        "every firm shrunk fully to the panel mean")

    n_used = np.maximum(stats["n_obs"] - (1 if leave_one_out else 0), 1)
    shrink = (s2_f / n_used) / (tau2 + s2_f / n_used) if tau2 > 0 else pd.Series(
        1.0, index=stats.index
    )
    stats["pooled_sd_used"] = sd_f
    stats["shrinkage"] = shrink
    stats["mu_log"] = (1 - shrink) * stats["raw_mean_log"] + shrink * mu_global
    stats["mu_return"] = np.exp(stats["mu_log"]) - 1.0
    stats["sum_log"] = g.sum()
    stats["thin"] = stats["n_obs"] < min_obs

    table = stats.reset_index()
    return FirmOffsets(
        table=table,
        mu_global=mu_global,
        tau2=tau2,
        mean_shrinkage=float(shrink.mean()),
        n_firms=int(len(table)),
        n_tickers=n_tickers,
        cutoff=cutoff,
        warnings=warnings,
    )


def apply_firm_offsets(
    panel: pd.DataFrame, offsets: FirmOffsets, *, leave_one_out: bool = True
) -> pd.DataFrame:
    """Attach ``firm_mu_log`` per record and the residual after removing it."""
    df = panel.copy()
    tab = offsets.table.set_index("analyst_firm") if len(offsets.table) else None
    mu_log, shrink_col = np.full(len(df), np.nan), np.full(len(df), np.nan)

    for i, (_, row) in enumerate(df.iterrows()):
        firm = row["analyst_firm"]
        if tab is None or firm not in tab.index:
            mu_log[i] = offsets.mu_global
            shrink_col[i] = 1.0
            continue
        rec = tab.loc[firm]
        n, x = float(rec["n_obs"]), row.get("log_implied")
        if leave_one_out and n > 1 and x is not None and np.isfinite(x):
            loo_mean = (float(rec["sum_log"]) - x) / (n - 1)
            b = float(rec["shrinkage"])
            mu_log[i] = (1 - b) * loo_mean + b * offsets.mu_global
        else:
            mu_log[i] = float(rec["mu_log"])
        shrink_col[i] = float(rec["shrinkage"])

    df["firm_mu_log"] = mu_log
    df["firm_shrinkage"] = shrink_col
    df["log_after_firm"] = df["log_implied"] - df["firm_mu_log"]
    return df


# ---------------------------------------------------------------------------
# Step c: sector / beta adjustment
# ---------------------------------------------------------------------------


@dataclass
class BetaFit:
    beta: float           # the beta actually used (shrunk)
    n_obs: int
    r2: float
    fitted: bool
    reason: str
    sector_symbol: str
    is_real_sector: bool
    beta_raw: float = float("nan")
    se: float = float("nan")
    shrinkage: float = float("nan")   # weight placed on the prior beta = 1


def estimate_beta(
    stock: PriceHistory,
    sector: PriceHistory,
    asof: date,
    *,
    window: int = config.BETA_WINDOW_DAYS,
    min_obs: int = config.BETA_MIN_OBS,
    sector_symbol: str = "",
    is_real_sector: bool = True,
) -> BetaFit:
    """OLS beta of the stock on its sector proxy over a trailing window at ``asof``.

    Two guards:

    * Falls back to beta = 1.0 -- and says so -- rather than fitting on a
      handful of observations. A beta fitted on 30 days is noise dressed as a
      control.
    * Applies Vasicek shrinkage toward 1.0, weighting the fitted beta by its own
      precision against a cross-sectional prior of sd ``config.BETA_PRIOR_SD``.
      Same logic as the James-Stein shrinkage on firm offsets: a noisily
      estimated control adds more error than it removes. The weight placed on
      the prior is reported, so a reader can see whether the prior or the data
      is doing the work.

    A low R^2 is reported but is NOT treated as a failure. For some stocks the
    sector genuinely explains little of the daily variance; that is a fact about
    the stock, and the correct response is to remove the little it does explain,
    not to pretend the relationship is stronger than it is.
    """
    sd, sr = stock.returns()
    xd, xr = sector.returns()
    if len(sd) == 0 or len(xd) == 0:
        return BetaFit(1.0, 0, float("nan"), False, "no return history",
                       sector_symbol, is_real_sector)
    a = pd.Series(sr, index=pd.Index(sd))
    b = pd.Series(xr, index=pd.Index(xd))
    joined = pd.concat([a, b], axis=1, join="inner").dropna()
    joined = joined[joined.index <= asof].tail(window)
    if len(joined) < min_obs:
        return BetaFit(1.0, len(joined), float("nan"), False,
                       f"only {len(joined)} overlapping days (<{min_obs})",
                       sector_symbol, is_real_sector, beta_raw=float("nan"),
                       se=float("nan"), shrinkage=1.0)
    y = joined.iloc[:, 0].to_numpy()
    x = joined.iloc[:, 1].to_numpy()
    res = ols_cluster(y, x, clusters=np.arange(len(y)))
    beta_raw, se = float(res.params[1]), float(res.se[1])

    tau2 = config.BETA_PRIOR_SD**2
    if np.isfinite(se) and se > 0:
        w_prior = se**2 / (se**2 + tau2)
    else:
        w_prior = 1.0
    beta = (1 - w_prior) * beta_raw + w_prior * 1.0

    return BetaFit(beta, len(joined), float(res.r2), True, "ok",
                   sector_symbol, is_real_sector, beta_raw=beta_raw, se=se,
                   shrinkage=w_prior)


def attach_sector_adjustment(
    panel: pd.DataFrame, sector: PriceHistory, beta: BetaFit, asof: date
) -> pd.DataFrame:
    """Strip the beta-scaled sector move between each action date and ``asof``.

    The target is re-based against where the stock would sit today if it had
    simply tracked its sector since the action, so what survives is the
    analyst's stock-specific call rather than their sector timing.
    """
    df = panel.copy()
    hit_now = sector.close_on_or_before(asof)
    n = len(df)
    sector_move = np.full(n, np.nan)
    sector_log = np.zeros(n)

    if hit_now is None:
        df["sector_move"] = sector_move
        df["sector_log"] = sector_log
        df["log_after_sector"] = df.get("log_after_firm", df["log_implied"])
        df.attrs["sector_adjusted"] = False
        return df

    sec_now = hit_now[1]
    for i, (_, row) in enumerate(df.iterrows()):
        anchor_date = row.get("spot_price_date")
        if anchor_date is None or (isinstance(anchor_date, float) and not np.isfinite(anchor_date)):
            continue
        hit_then = sector.close_on_or_before(anchor_date)
        if hit_then is None or hit_then[1] <= 0:
            continue
        m = sec_now / hit_then[1] - 1.0
        sector_move[i] = m
        scaled = 1.0 + beta.beta * m
        # A beta-scaled move below -100% is not a price path; clamp and let the
        # row keep its unadjusted value rather than produce a complex number.
        sector_log[i] = math.log(scaled) if scaled > 1e-6 else 0.0

    df["sector_move"] = sector_move
    df["sector_log"] = sector_log
    base = df["log_after_firm"] if "log_after_firm" in df else df["log_implied"]
    df["log_after_sector"] = base - df["sector_log"]
    df.attrs["sector_adjusted"] = True
    return df


# ---------------------------------------------------------------------------
# Step b: the fitted age decay
# ---------------------------------------------------------------------------


@dataclass
class AgeDecayFit:
    fitted: bool
    lam: float | None
    half_life_days: float | None
    se: float | None
    tstat: float | None
    ci: tuple[float, float] | None
    n_obs: int
    n_clusters: int
    reason: str
    buckets: pd.DataFrame
    pooled_lam: float | None = None

    def weights(self, ages, max_age: int = config.MAX_LEVEL_AGE_DAYS) -> np.ndarray:
        """Decay weights for record ages, zero beyond the hard cutoff."""
        a = np.asarray(ages, dtype=float)
        if self.fitted and self.lam is not None:
            w = np.exp(-self.lam * a)
        else:
            w = np.ones_like(a)
        w = np.where(np.isfinite(a) & (a <= max_age) & (a >= 0), w, 0.0)
        return w


def fit_age_decay(
    panel: pd.DataFrame,
    history: PriceHistory,
    *,
    asof: date,
    horizon_days: int = config.FORECAST_HORIZON_DAYS,
    max_age_days: int = 730,
    eval_freq: str = "ME",
    min_obs: int = 200,
    min_clusters: int = 12,
    min_tstat: float = 1.64,
) -> AgeDecayFit:
    """Fit ``lambda`` in ``exp(-lambda * age)`` from realised forecast accuracy.

    Method. On a grid of evaluation dates ``t`` for which the realised price at
    ``t + horizon`` is known, every record visible at ``t`` (action on or before
    ``t``, age within ``max_age_days``) contributes one observation: its log
    forecast error ``u = log(PT / P_{t+horizon})`` and its age ``A``. If a
    record's precision decays exponentially then ``Var(u | A) = v0 * exp(lam*A)``,
    so ``lam`` is the slope of ``log(u^2)`` on ``A`` -- and an inverse-variance
    weight is exactly ``exp(-lam*A)``, which is the functional form the brief
    specifies.

    Two statistical details that change the answer:

    * **Evaluation-date fixed effects.** ``log(u^2)`` is demeaned within each
      evaluation date, so lambda is identified from the spread of AGES on the
      same date rather than from the accident that some dates are harder than
      others. Without this, a volatile stretch that happens to contain older
      records masquerades as decay. The pooled (no-FE) slope is reported too,
      and a large gap between them is itself a warning.
    * **Cluster-robust inference.** All records scored on one evaluation date
      share a realised price, so errors cluster by date; naive standard errors
      here are inflated several-fold.

    Refusal. If the slope is not positive with ``t >= min_tstat``, or the sample
    is too thin, or the implied half-life is absurd, ``fitted`` is False and the
    reason is carried in the result. The caller then uses flat weights inside a
    hard age cutoff. A half-life is never assumed.

    Known limitation: older records at a given evaluation date come from firms
    that chose not to update, which is not random. Part of any measured decay is
    that selection rather than information decaying on its own.
    """
    df = panel[panel.get("usable", True) & panel["price_target_used"].notna()].copy()
    empty_buckets = pd.DataFrame(columns=["age_bucket", "n", "rmse_log", "mae_log"])
    if df.empty:
        return AgeDecayFit(False, None, None, None, None, None, 0, 0,
                           "no usable records", empty_buckets)

    df["action_date"] = pd.to_datetime(df["action_date"]).dt.date
    first, last = min(df["action_date"]), asof
    grid = pd.date_range(first, last, freq=eval_freq)
    rows = []
    for ts in grid:
        t = ts.date()
        fwd = history.forward_close(t, horizon_days)
        if fwd is None or fwd[1] <= 0:
            continue
        realised = fwd[1]
        vis = df[(df["action_date"] <= t)]
        if vis.empty:
            continue
        ages = np.array([(t - d).days for d in vis["action_date"]], dtype=float)
        keep = ages <= max_age_days
        if not keep.any():
            continue
        pt = vis["price_target_used"].to_numpy()[keep]
        u = np.log(pt / realised)
        for age, err in zip(ages[keep], u):
            rows.append((t, float(age), float(err)))

    if len(rows) < min_obs:
        return AgeDecayFit(False, None, None, None, None, None, len(rows), 0,
                           f"only {len(rows)} (age, error) pairs, need {min_obs}; "
                           "usually means the history is shorter than the "
                           "forecast horizon plus the decay window",
                           empty_buckets)

    obs = pd.DataFrame(rows, columns=["eval_date", "age", "log_err"])
    obs["y"] = np.log(np.maximum(obs["log_err"] ** 2, 1e-12))

    bucket_edges = [0, 30, 60, 90, 120, 180, 270, 365, 540, 730]
    obs["age_bucket"] = pd.cut(obs["age"], bucket_edges, right=False)
    buckets = (
        obs.groupby("age_bucket", observed=True)
        .agg(n=("log_err", "size"),
             rmse_log=("log_err", lambda s: float(np.sqrt(np.mean(s**2)))),
             mae_log=("log_err", lambda s: float(np.mean(np.abs(s)))))
        .reset_index()
    )
    buckets["age_bucket"] = buckets["age_bucket"].astype(str)

    n_clusters = int(obs["eval_date"].nunique())
    if n_clusters < min_clusters:
        return AgeDecayFit(False, None, None, None, None, None, len(obs), n_clusters,
                           f"only {n_clusters} evaluation dates, need {min_clusters}",
                           buckets)

    pooled = ols_cluster(obs["y"], obs["age"], obs["eval_date"])
    pooled_lam = float(pooled.params[1])

    # Within-evaluation-date estimator (date fixed effects).
    grp = obs.groupby("eval_date")
    y_d = obs["y"] - grp["y"].transform("mean")
    a_d = obs["age"] - grp["age"].transform("mean")
    if float(np.nanstd(a_d)) < 1e-9:
        return AgeDecayFit(False, None, None, None, None, None, len(obs), n_clusters,
                           "no within-date variation in record age", buckets,
                           pooled_lam=pooled_lam)
    within = ols_cluster(y_d, a_d, obs["eval_date"], add_const=False)
    lam = float(within.params[0])
    se = float(within.se[0])
    t = float(within.tstat[0])
    lo, hi = within.ci(0)

    if not np.isfinite(lam) or lam <= 0 or t < min_tstat:
        return AgeDecayFit(
            False, None, None, se, t, (lo, hi), len(obs), n_clusters,
            f"no significant accuracy decay with age (lambda={lam:.3g}/day, "
            f"t={t:.2f} vs required {min_tstat}); using flat weights inside the "
            f"{config.MAX_LEVEL_AGE_DAYS}-day cutoff instead of assuming a half-life",
            buckets, pooled_lam=pooled_lam,
        )

    half_life = math.log(2) / lam
    if not (5 <= half_life <= 2000):
        return AgeDecayFit(
            False, None, None, se, t, (lo, hi), len(obs), n_clusters,
            f"implied half-life {half_life:.0f}d is outside the plausible "
            "5-2000 day range; treating the fit as spurious",
            buckets, pooled_lam=pooled_lam,
        )

    return AgeDecayFit(True, lam, half_life, se, t, (lo, hi), len(obs), n_clusters,
                       "ok", buckets, pooled_lam=pooled_lam)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class DebiasResult:
    ticker: str
    asof: date
    spot: float
    spot_date: date
    panel: pd.DataFrame
    firm_offsets: FirmOffsets
    age_decay: AgeDecayFit
    beta: BetaFit
    raw: Distribution
    after_firm: Distribution
    after_sector: Distribution
    final: Distribution
    diagnostics: dict
    pit_mode: str
    is_synthetic: bool
    warnings: list[str] = field(default_factory=list)


def firm_variance_share(panel: pd.DataFrame, column: str = "log_implied") -> float:
    """Share of cross-sectional variance in ``column`` explained by firm identity.

    This is the mechanical evidence for whether step (a) is doing anything. A
    high share means implied returns say more about who wrote them than about
    the stock; that is the anchoring effect the correction targets.
    """
    df = panel[panel.get("usable", True)][["analyst_firm", column]].dropna()
    if df[column].empty or df["analyst_firm"].nunique() < 2:
        return float("nan")
    total = float(df[column].var(ddof=0))
    if total <= 0:
        return float("nan")
    within = float(
        df.groupby("analyst_firm")[column]
        .transform(lambda s: s - s.mean())
        .var(ddof=0)
    )
    return max(0.0, min(1.0, 1.0 - within / total))


def debias(
    panel: pd.DataFrame,
    history: PriceHistory,
    sector: PriceHistory | None,
    *,
    ticker: str,
    asof: date,
    sector_symbol: str = "",
    is_real_sector: bool = True,
    convention: str = config.SPOT_AT_ACTION_CONVENTION,
    max_level_age: int = config.MAX_LEVEL_AGE_DAYS,
    horizon_days: int = config.FORECAST_HORIZON_DAYS,
    fit_decay: bool = True,
) -> DebiasResult:
    """Run the full Stage 2 pipeline for one ticker as of one date."""
    warnings: list[str] = list(panel.attrs.get("warnings", []))
    pit_mode = panel.attrs.get("pit_mode", "unknown")
    is_synth = bool(panel.attrs.get("is_synthetic", False))

    spot_hit = history.close_on_or_before(asof)
    if spot_hit is None:
        raise ValueError(f"no price for {ticker} on or before {asof}")
    spot_date, spot = spot_hit

    df = attach_implied_returns(panel, history, convention=convention)

    offsets = estimate_firm_offsets(df, cutoff=None, leave_one_out=True)
    warnings.extend(offsets.warnings)
    df = apply_firm_offsets(df, offsets, leave_one_out=True)

    if sector is not None:
        beta = estimate_beta(history, sector, asof, sector_symbol=sector_symbol,
                             is_real_sector=is_real_sector)
        if not beta.fitted:
            warnings.append(f"beta not fitted ({beta.reason}); using beta=1.0")
        if beta.fitted and np.isfinite(beta.r2) and beta.r2 < config.BETA_LOW_R2:
            warnings.append(
                f"{beta.sector_symbol} explains only {beta.r2:.1%} of {ticker}'s "
                "daily variance over the beta window, so step (c) removes very "
                "little. That is a fact about the stock, not a defect -- but it "
                "means the sector adjustment is not protecting you from much"
            )
        if not is_real_sector:
            warnings.append(
                f"no sector mapping for {ticker}; using {sector_symbol} as a "
                "broad-market proxy, which removes market beta but not sector beta"
            )
        df = attach_sector_adjustment(df, sector, beta, asof)
    else:
        beta = BetaFit(1.0, 0, float("nan"), False, "no sector history supplied",
                       sector_symbol, is_real_sector)
        warnings.append("no sector history supplied; step (c) skipped entirely")
        df["sector_move"] = np.nan
        df["sector_log"] = 0.0
        df["log_after_sector"] = df["log_after_firm"]

    ad = pd.to_datetime(df["action_date"]).dt.date
    df["age_days"] = [(asof - d).days for d in ad]

    decay = (
        fit_age_decay(df, history, asof=asof, horizon_days=horizon_days)
        if fit_decay
        else AgeDecayFit(False, None, None, None, None, None, 0, 0,
                         "decay fitting disabled by caller",
                         pd.DataFrame(columns=["age_bucket", "n", "rmse_log", "mae_log"]))
    )
    if not decay.fitted:
        warnings.append(f"age decay not fitted: {decay.reason}")

    df["age_weight"] = decay.weights(df["age_days"].to_numpy(), max_age=max_level_age)
    df.loc[~df["usable"], "age_weight"] = 0.0
    df["resid_return"] = np.exp(df["log_after_sector"]) - 1.0
    df["after_firm_return"] = np.exp(df["log_after_firm"]) - 1.0

    in_window = df["age_weight"] > 0
    raw = Distribution.summarise(df.loc[in_window, "implied_return"])
    after_firm = Distribution.summarise(df.loc[in_window, "after_firm_return"])
    after_sector = Distribution.summarise(df.loc[in_window, "resid_return"])
    final = Distribution.summarise(
        df.loc[in_window, "resid_return"], df.loc[in_window, "age_weight"]
    )

    diagnostics = {
        "n_events_total": int(len(df)),
        "n_usable": int(df["usable"].sum()),
        "n_in_level_window": int(in_window.sum()),
        "n_firms_in_window": int(df.loc[in_window, "analyst_firm"].nunique()),
        "median_age_days": float(df.loc[in_window, "age_days"].median()) if in_window.any() else float("nan"),
        "max_level_age_days": max_level_age,
        "firm_variance_share_raw": firm_variance_share(df, "log_implied"),
        "firm_variance_share_after": firm_variance_share(df, "log_after_firm"),
        "flag_counts": {
            k: int(df[k].sum())
            for k in df.columns
            if k.startswith("flag_")
        },
        "n_restated_events": int(df["was_restated"].sum()) if "was_restated" in df else 0,
        "spot_convention": convention,
        "sources": panel.attrs.get("sources", []),
    }

    return DebiasResult(
        ticker=ticker.upper(), asof=asof, spot=spot, spot_date=spot_date,
        panel=df, firm_offsets=offsets, age_decay=decay, beta=beta,
        raw=raw, after_firm=after_firm, after_sector=after_sector, final=final,
        diagnostics=diagnostics, pit_mode=pit_mode, is_synthetic=is_synth,
        warnings=warnings,
    )
