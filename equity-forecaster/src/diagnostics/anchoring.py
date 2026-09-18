"""Notebook 02 as a tested module: is the anchoring story actually true here?

The design brief asserts three things as priors from the literature. This
module tests all three on the panel in hand rather than taking them on faith,
because the corrections in Stage 2 only earn their place if the pathologies
they target are present:

**A. Targets are a stable multiple of prevailing price.** Regress
``log(PT)`` on ``log(spot_at_action)``. A slope near 1 with a firm-specific
intercept says the analyst mostly restates the current price times their own
habitual multiple. That is the anchoring the firm correction removes.

**B. Analysts revise after the stock moves, not before.** Regress the log change
in target on the stock's trailing 20-day return. A positive loading means the
firm is following price, and a firm that follows price carries little
independent information -- this is the Stage 3 "timeliness" measure, computed
here because it is also the cleanest single piece of evidence for the brief's
second design constraint.

**C. The level carries little information; the derivative carries some.**
Regress the realised forward 12-month return on the panel's median implied
return, and separately on revision breadth and rate. Reported with Newey-West
standard errors, because month-end observations of a 12-month forward return
overlap by eleven months and a naive t-stat here is inflated several-fold.

Test C's limits, stated plainly: this is a SINGLE-TICKER TIME-SERIES test. The
literature's result is CROSS-SECTIONAL -- rank many stocks by consensus implied
return each month and compare deciles. A single ticker cannot reproduce that and
cannot refute it. What this version can do is catch the case where the panel in
hand is so uninformative that there is no point proceeding, and set up the real
test in Stage 8.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from .. import config
from ..model.debias import attach_implied_returns
from ..model.regress import ols_cluster, ols_hac
from ..model.revisions import extract_revisions, monthly_snapshots
from ..store import pit


@dataclass
class AnchoringStudy:
    ticker: str
    asof: date
    pit_mode: str
    is_synthetic: bool
    n_records: int
    firm_ratios: pd.DataFrame
    anchor_slope: float
    anchor_slope_se: float
    anchor_slope_t: float
    anchor_r2: float
    anchor_n: int
    follow_beta: float | None
    follow_se: float | None
    follow_t: float | None
    follow_n: int
    follow_by_firm: pd.DataFrame
    snapshots: pd.DataFrame
    level_test: dict
    revision_test: dict
    notes: list[str]


def _hac_row(y, x, lags, label) -> dict:
    ok = np.isfinite(np.asarray(y, float)) & np.isfinite(np.asarray(x, float))
    n = int(ok.sum())
    if n < 24:
        return {"label": label, "n": n, "beta": float("nan"), "se": float("nan"),
                "t": float("nan"), "r2": float("nan"),
                "note": f"only {n} usable month-ends; need at least 24 before a "
                        "slope on overlapping annual returns means anything"}
    res = ols_hac(np.asarray(y, float)[ok], np.asarray(x, float)[ok], lags=lags)
    return {"label": label, "n": n, "beta": float(res.params[1]),
            "se": float(res.se[1]), "t": float(res.tstat[1]), "r2": float(res.r2),
            "note": res.cov_type}


def run_anchoring_study(
    con, ticker: str, asof: date, *, pit_mode: str = pit.STRICT,
    horizon_days: int = config.FORECAST_HORIZON_DAYS,
) -> AnchoringStudy:
    ticker = ticker.upper()
    panel = pit.price_targets_asof(con, ticker, asof, pit_mode=pit_mode)
    history = pit.price_history_asof(con, ticker, asof)
    notes: list[str] = []

    df = attach_implied_returns(panel, history)
    df = extract_revisions(df)
    usable = df[df["usable"] & df["spot_pit"].notna() & df["price_target_used"].notna()]

    # -- A. PT/spot ratio by firm ----------------------------------------
    usable = usable.assign(
        log_pt=np.log(usable["price_target_used"]),
        log_spot=np.log(usable["spot_pit"]),
        pt_spot_ratio=usable["price_target_used"] / usable["spot_pit"],
    )
    firm_ratios = (
        usable.groupby("analyst_firm")["pt_spot_ratio"]
        .agg(n="size", mean="mean", median="median", sd="std", lo="min", hi="max")
        .reset_index()
        .sort_values("n", ascending=False)
    )

    if len(usable) >= 20 and usable["analyst_firm"].nunique() >= 2:
        # Firm fixed effects: demean both sides within firm so the slope is the
        # WITHIN-firm response of the target to the prevailing price, which is
        # the anchoring claim. A pooled slope would partly reflect firms
        # covering the stock at different price levels.
        g = usable.groupby("analyst_firm")
        y = usable["log_pt"] - g["log_pt"].transform("mean")
        x = usable["log_spot"] - g["log_spot"].transform("mean")
        res = ols_cluster(y, x, usable["analyst_firm"], add_const=False)
        anchor_slope, anchor_se = float(res.params[0]), float(res.se[0])
        anchor_t = float((anchor_slope - 1.0) / anchor_se) if anchor_se > 0 else float("nan")
        anchor_r2, anchor_n = float(res.r2), res.n_obs
        notes.append(
            "anchoring slope is a within-firm estimate with standard errors "
            "clustered by firm; the reported t-statistic tests slope = 1 (pure "
            "anchoring), not slope = 0"
        )
    else:
        anchor_slope = anchor_se = anchor_t = anchor_r2 = float("nan")
        anchor_n = int(len(usable))
        notes.append("too few records for the anchoring regression")

    # -- B. do revisions follow price? -----------------------------------
    # Trailing 20-trading-day return as of each record's anchor date. Looked up
    # by bisect rather than by scanning the date list per record, which turns a
    # quadratic loop into a linear one on panels of any size.
    from bisect import bisect_right

    trail = []
    for anchor_date in usable["spot_price_date"]:
        past = np.nan
        if anchor_date is not None:
            j = bisect_right(history.dates, anchor_date) - 1
            if j >= 20:
                c0, c1 = history.closes[j - 20], history.closes[j]
                if np.isfinite(c0) and np.isfinite(c1) and c0 > 0:
                    past = c1 / c0 - 1.0
        trail.append(past)
    usable = usable.assign(trailing_20d=trail)

    rev = usable[usable["revision_log"].notna() & usable["trailing_20d"].notna()]
    if len(rev) >= 30:
        res = ols_cluster(rev["revision_log"], rev["trailing_20d"], rev["analyst_firm"])
        follow_beta, follow_se, follow_t = (float(res.params[1]), float(res.se[1]),
                                            float(res.tstat[1]))
        follow_n = res.n_obs
    else:
        follow_beta = follow_se = follow_t = None
        follow_n = int(len(rev))
        notes.append(f"only {len(rev)} revisions with a trailing return; skipping "
                     "the follower regression")

    rows = []
    for firm, grp in rev.groupby("analyst_firm"):
        if len(grp) < 8:
            continue
        try:
            r = ols_cluster(grp["revision_log"], grp["trailing_20d"],
                            np.arange(len(grp)))
            rows.append({"analyst_firm": firm, "n": len(grp),
                         "follow_beta": float(r.params[1]), "t": float(r.tstat[1])})
        except ValueError:
            continue
    follow_by_firm = pd.DataFrame(rows).sort_values("follow_beta", ascending=False) \
        if rows else pd.DataFrame(columns=["analyst_firm", "n", "follow_beta", "t"])

    # -- C. level vs derivative against the realised outcome --------------
    snaps = monthly_snapshots(df, history, asof, horizon_days=horizon_days)
    lags = max(12, int(horizon_days / 30) + 1)
    if len(snaps):
        level_test = _hac_row(snaps["fwd_return"], snaps["level_raw"], lags,
                              "fwd 12m return ~ median implied return (level)")
        revision_test = _hac_row(snaps["fwd_return"], snaps["revision_breadth"], lags,
                                 "fwd 12m return ~ revision breadth (derivative)")
    else:
        level_test = {"label": "level", "n": 0, "beta": float("nan"),
                      "se": float("nan"), "t": float("nan"), "r2": float("nan"),
                      "note": "no month-end snapshots with a completed 12-month horizon"}
        revision_test = dict(level_test, label="derivative")
    notes.append(
        "the level and derivative tests are single-ticker time series with "
        "overlapping annual horizons; Newey-West lags set to "
        f"{lags}. They cannot reproduce the cross-sectional result in the "
        "literature and must not be read as confirming or refuting it"
    )

    return AnchoringStudy(
        ticker=ticker, asof=asof, pit_mode=pit_mode,
        is_synthetic=bool(panel.attrs.get("is_synthetic", False)),
        n_records=int(len(usable)), firm_ratios=firm_ratios,
        anchor_slope=anchor_slope, anchor_slope_se=anchor_se,
        anchor_slope_t=anchor_t, anchor_r2=anchor_r2, anchor_n=anchor_n,
        follow_beta=follow_beta, follow_se=follow_se, follow_t=follow_t,
        follow_n=follow_n, follow_by_firm=follow_by_firm, snapshots=snaps,
        level_test=level_test, revision_test=revision_test, notes=notes,
    )


def render_anchoring_report(s: AnchoringStudy, top: int = 20) -> str:
    rule = "=" * 78
    thin = "-" * 78
    out = [rule, f"  ANCHORING EVIDENCE -- {s.ticker} as of {s.asof} "
                 f"({s.n_records} usable records)"]
    if s.is_synthetic:
        out += ["  *** SIMULATED PANEL: these regressions recover the parameters the",
                "      generator injected. They are a test of the code, not a finding. ***"]
    out += [rule, "",
            "A. IS THE TARGET A STABLE MULTIPLE OF SPOT?", thin]
    if np.isfinite(s.anchor_slope):
        out += [f"  within-firm slope of log(PT) on log(spot)   {s.anchor_slope:.3f}"
                f"  (se {s.anchor_slope_se:.3f})",
                f"  t-statistic against pure anchoring (slope=1) {s.anchor_slope_t:+.2f}",
                f"  R^2 {s.anchor_r2:.3f} on {s.anchor_n} records, SEs clustered by firm",
                ""]
        # Judged on an ECONOMIC threshold, then separately on a statistical one.
        # With a few hundred records the standard error is small enough that a
        # slope of 1.02 is "significantly different from 1" while being, for
        # every practical purpose, one-for-one anchoring.
        gap = abs(s.anchor_slope - 1.0)
        if gap < 0.10:
            out.append(f"  Within {gap:.2f} of one-for-one: a 1% move in the stock moves the")
            out.append(f"  target by about {s.anchor_slope:.2f}%. The target is essentially a")
            out.append("  restatement of the prevailing price times a firm-specific")
            out.append("  multiple, which is the effect Stage 2a removes.")
            if abs(s.anchor_slope_t) > 2:
                out.append(f"  (Statistically the slope does differ from 1, t={s.anchor_slope_t:+.2f},")
                out.append("   but on this sample size that is a precision result, not an")
                out.append("   economic one.)")
        elif s.anchor_slope < 1.0:
            out.append(f"  {s.anchor_slope:.2f}, below one-for-one (t={s.anchor_slope_t:+.2f}): targets are STICKY.")
            out.append("  A 1% move in the stock moves the target by less than 1%, so")
            out.append("  the street under-adjusts and its targets trail the price.")
            out.append("  This is still anchoring, but to a STALE price rather than to")
            out.append("  the current one, and it has a different consequence: after a")
            out.append("  run-up the implied return compresses or goes negative for")
            out.append("  everyone at once, which is a panel-wide artifact and not a")
            out.append("  bearish view. Step (a) removes the firm's habitual level; it")
            out.append("  does not fix the lag, and Stage 2b's age decay is the part")
            out.append("  that is supposed to.")
        else:
            out.append(f"  {s.anchor_slope:.2f}, above one-for-one (t={s.anchor_slope_t:+.2f}): targets")
            out.append("  OVER-extrapolate. A 1% move moves the target by more than 1%,")
            out.append("  so the street amplifies recent moves rather than merely")
            out.append("  restating them. Check section B: a high follower loading")
            out.append("  alongside this means targets are chasing momentum.")
    else:
        out.append("  not estimated (too few records)")

    out += ["", f"  PT/spot ratio by firm (top {top} by coverage):",
            f"  {'firm':<26}{'n':>5}{'mean':>9}{'median':>9}{'sd':>8}{'min':>8}{'max':>8}"]
    for _, r in s.firm_ratios.head(top).iterrows():
        sd = r["sd"] if np.isfinite(r["sd"]) else float("nan")
        out.append(f"  {str(r['analyst_firm'])[:25]:<26}{int(r['n']):>5}{r['mean']:>9.3f}"
                   f"{r['median']:>9.3f}{sd:>8.3f}{r['lo']:>8.3f}{r['hi']:>8.3f}")

    out += ["", "B. DO REVISIONS FOLLOW THE STOCK?", thin]
    if s.follow_beta is None:
        out.append(f"  not estimated ({s.follow_n} usable revisions)")
    else:
        out += [f"  d log(PT) on trailing 20-day stock return: "
                f"{s.follow_beta:+.3f} (se {s.follow_se:.3f}, t {s.follow_t:+.2f})",
                f"  on {s.follow_n} revisions, SEs clustered by firm", ""]
        if s.follow_t > 2:
            out += ["  Positive and significant: the street revises AFTER the stock",
                    "  moves. A firm with a high loading is restating the recent past,",
                    "  not forecasting, and Stage 3 will discount it accordingly."]
        else:
            out.append("  No significant following behaviour detected in this panel.")
        if len(s.follow_by_firm):
            out += ["", f"  {'firm':<26}{'n':>5}{'follow beta':>13}{'t':>8}"]
            for _, r in s.follow_by_firm.head(top).iterrows():
                out.append(f"  {str(r['analyst_firm'])[:25]:<26}{int(r['n']):>5}"
                           f"{r['follow_beta']:>13.3f}{r['t']:>8.2f}")

    out += ["", "C. LEVEL vs DERIVATIVE AGAINST THE REALISED OUTCOME", thin,
            f"  month-end snapshots with a completed 12-month horizon: {len(s.snapshots)}"]
    for t in (s.level_test, s.revision_test):
        out.append("")
        out.append(f"  {t['label']}")
        if t["n"] and np.isfinite(t.get("beta", float('nan'))):
            out.append(f"     beta {t['beta']:+.3f}   se {t['se']:.3f}   "
                       f"t {t['t']:+.2f}   R^2 {t['r2']:.3f}   n {t['n']}   [{t['note']}]")
        else:
            out.append(f"     {t['note']}")
    out += ["", "  A |t| below about 2 here is the expected result, and is the reason",
            "  the composite must be validated against spot in Stage 8 before it is",
            "  allowed to emit anything."]

    out += ["", thin, "NOTES", thin]
    for n in s.notes:
        out.append(f"  - {n}")
    out.append(rule)
    return "\n".join(out)
