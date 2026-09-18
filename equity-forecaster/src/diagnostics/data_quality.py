"""Notebook 01 as a tested module: is the per-analyst data clean enough to use?

The brief's build order is explicit -- do not build the composite signal until
the data-quality work has shown the underlying per-analyst data supports it. So
this is a GATE, not a slideshow. :func:`run_quality_checks` returns a pass/fail
plus every number behind it, and the CLI exits non-zero when it fails.

Each check names the specific way it can wreck a backtest, because "data
quality" as a category is useless; "38% of your action dates are Saturdays, so
that column is the vendor's publication date and every implied return is
anchored to the wrong price" is not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .. import config
from ..model.debias import attach_implied_returns, effective_n
from ..model.revisions import extract_revisions
from ..store import pit

CRITICAL, WARN, INFO = "critical", "warn", "info"


@dataclass
class Check:
    name: str
    passed: bool
    severity: str
    detail: str
    numbers: dict = field(default_factory=dict)


@dataclass
class QualityReport:
    ticker: str
    asof: date
    pit_mode: str
    is_synthetic: bool
    checks: list[Check]
    provenance: dict

    @property
    def passed(self) -> bool:
        return not any(c.severity == CRITICAL and not c.passed for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


def run_quality_checks(
    con, ticker: str, asof: date, *, pit_mode: str = pit.STRICT
) -> QualityReport:
    ticker = ticker.upper()
    panel = pit.price_targets_asof(con, ticker, asof, pit_mode=pit_mode)
    prov = pit.provenance(con, ticker)
    checks: list[Check] = []

    if panel.empty:
        checks.append(Check("panel non-empty", False, CRITICAL,
                            f"no records visible for {ticker} as of {asof} under "
                            f"pit_mode={pit_mode}", {}))
        return QualityReport(ticker, asof, pit_mode, False, checks, prov)

    history = pit.price_history_asof(con, ticker, asof)
    df = attach_implied_returns(panel, history)
    df = extract_revisions(df)
    ad = pd.to_datetime(df["action_date"]).dt.date
    df["age_days"] = [(asof - d).days for d in ad]
    in_window = df["usable"] & (df["age_days"] <= config.MAX_LEVEL_AGE_DAYS)

    # -- 1. coverage ------------------------------------------------------
    n_firms_window = int(df.loc[in_window, "analyst_firm"].nunique())
    checks.append(Check(
        "coverage depth", n_firms_window >= 4,
        CRITICAL if n_firms_window < 4 else INFO,
        f"{n_firms_window} distinct firms inside the {config.MAX_LEVEL_AGE_DAYS}-day "
        "window. Below 4 there is no crowd to aggregate, and Stage 7's abstention "
        "rule would refuse to emit a signal anyway.",
        {"n_firms_window": n_firms_window, "n_firms_total": int(df["analyst_firm"].nunique()),
         "n_events": int(len(df)), "n_in_window": int(in_window.sum()),
         "naive_n_eff": effective_n(np.ones(int(in_window.sum())))},
    ))

    # -- 2. staleness -----------------------------------------------------
    # Measured on the CURRENT panel -- the latest record per covering firm --
    # not on the share of all historical records beyond the cutoff. The latter
    # is near 100% for any multi-year store and says nothing about whether the
    # panel you would aggregate today is fresh.
    covering = df[df["usable"] & (df["age_days"] <= 365)]
    latest = covering.groupby("analyst_firm")["age_days"].min() if len(covering) else pd.Series(dtype=float)
    med_latest = float(latest.median()) if len(latest) else float("nan")
    share_firms_stale = float((latest > config.MAX_LEVEL_AGE_DAYS).mean()) if len(latest) else float("nan")
    med_age = float(df.loc[in_window, "age_days"].median()) if in_window.any() else float("nan")
    passed_stale = (
        np.isfinite(med_latest) and med_latest <= config.MAX_LEVEL_AGE_DAYS
        and np.isfinite(share_firms_stale) and share_firms_stale < 0.5
    )
    checks.append(Check(
        "staleness", passed_stale, WARN,
        f"of the {len(latest)} firms with a record in the last year, the median "
        f"firm's most recent action is {med_latest:.0f} days old and "
        f"{share_firms_stale:.0%} have not acted inside the "
        f"{config.MAX_LEVEL_AGE_DAYS}-day cutoff; median age of the records that "
        f"do sit inside the window is {med_age:.0f} days. A firm that stopped "
        "updating still shows up in a naive consensus, quoting a price level the "
        "stock has already left.",
        {"median_latest_age_per_firm": med_latest,
         "share_firms_stale": share_firms_stale,
         "median_age_in_window": med_age,
         "n_firms_covering_1y": int(len(latest))},
    ))

    # -- 3. vendor spot integrity ----------------------------------------
    has_vendor_spot = df["vendor_spot_at_action"].notna()
    if has_vendor_spot.any():
        sub = df[has_vendor_spot & df["spot_pit"].notna()]
        err_vs_action = np.abs(sub["vendor_spot_at_action"] / sub["spot_pit"] - 1.0)
        spot_now = history.close_on_or_before(asof)
        err_vs_today = (
            np.abs(sub["vendor_spot_at_action"] / spot_now[1] - 1.0)
            if spot_now else pd.Series(np.nan, index=sub.index)
        )
        contaminated = bool(
            np.isfinite(err_vs_today).any()
            and float(np.nanmedian(err_vs_today)) < float(np.nanmedian(err_vs_action))
        )
        checks.append(Check(
            "vendor spot_at_action integrity", not contaminated, CRITICAL,
            f"vendor's spot_at_action vs our action-date close: median relative "
            f"error {np.nanmedian(err_vs_action):.3%}; vs TODAY's close: "
            f"{np.nanmedian(err_vs_today):.3%}. If the second is smaller the vendor "
            "back-filled that column with the current price, which makes every "
            "implied return computed from it near-zero by construction.",
            {"median_err_vs_action_close": float(np.nanmedian(err_vs_action)),
             "median_err_vs_today": float(np.nanmedian(err_vs_today)),
             "n_with_vendor_spot": int(has_vendor_spot.sum())},
        ))
    else:
        checks.append(Check(
            "vendor spot_at_action integrity", True, INFO,
            "no vendor-supplied spot_at_action to cross-check; the anchor price is "
            "resolved entirely from our own point-in-time price history, which is "
            "the safer of the two.",
            {"n_with_vendor_spot": 0},
        ))

    # -- 4. corporate actions --------------------------------------------
    n_extreme = int(df["flag_extreme"].sum())
    n_rescued = int(df["flag_split_rescued"].sum())
    share_extreme = n_extreme / len(df)
    checks.append(Check(
        "corporate-action integrity", share_extreme < 0.05, CRITICAL,
        f"{n_extreme} records ({share_extreme:.1%}) imply a return beyond "
        f"+/-{config.EXTREME_IMPLIED_RETURN:.0%} and are excluded; {n_rescued} were "
        "rescued by undoing a stock split. A high extreme rate usually means the "
        "vendor stores targets as-quoted while the price series is back-adjusted.",
        {"n_extreme": n_extreme, "n_split_rescued": n_rescued,
         "share_extreme": share_extreme},
    ))

    # -- 5. action-date integrity ----------------------------------------
    weekday = pd.Series([d.weekday() for d in ad])
    share_weekend = float((weekday >= 5).mean())
    trading_days = set(history.dates)
    share_non_trading = float(np.mean([d not in trading_days for d in ad]))
    checks.append(Check(
        "action_date is a trading date", share_non_trading < 0.15, WARN,
        f"{share_weekend:.1%} of action dates fall on a weekend and "
        f"{share_non_trading:.1%} on a non-trading day. A high share means the "
        "column is the vendor's publication timestamp rather than when the "
        "analyst acted, which shifts every anchor price by a day or more.",
        {"share_weekend": share_weekend, "share_non_trading": share_non_trading},
    ))

    # -- 6. restatements --------------------------------------------------
    n_restated = int(df["was_restated"].sum()) if "was_restated" in df else 0
    share_restated = n_restated / len(df)
    checks.append(Check(
        "vendor restatement rate", share_restated < 0.10, WARN,
        f"{n_restated} of {len(df)} visible events ({share_restated:.1%}) have been "
        "restated by the vendor since first capture. The store keeps both and the "
        "point-in-time reader uses the original, so this is contained -- but a high "
        "rate means any research built on a single snapshot of this vendor's "
        "back-history is reading restated values throughout.",
        {"n_restated": n_restated, "share_restated": share_restated},
    ))

    # -- 7. field completeness -------------------------------------------
    completeness = {
        c: float(df[c].notna().mean())
        for c in ("price_target", "price_target_prev", "rating", "analyst_name",
                  "fiscal_year_covered")
        if c in df
    }
    prev_from_panel = float((df["prev_source"] == "panel").mean())
    checks.append(Check(
        "field completeness", completeness.get("price_target", 0) > 0.8, WARN,
        "share of records carrying each field: "
        + ", ".join(f"{k} {v:.0%}" for k, v in completeness.items())
        + f". {prev_from_panel:.0%} of prior targets had to be reconstructed from "
        "the firm's own previous action in the panel rather than read from the "
        "vendor, which is only valid if the panel holds that firm's full history.",
        {**completeness, "prev_reconstructed_share": prev_from_panel},
    ))

    # -- 8. rating / target coherence ------------------------------------
    rated = df[in_window & df["rating"].notna() & df["implied_return"].notna()]
    coherent, corr = True, float("nan")
    if len(rated) >= 10 and rated["rating"].nunique() >= 2:
        corr = float(np.corrcoef(rated["rating"], rated["implied_return"])[0, 1])
        coherent = corr > 0
    checks.append(Check(
        "rating / target coherence", coherent, WARN,
        f"correlation between normalised rating and implied return is {corr:.2f} "
        f"on {len(rated)} in-window records. A non-positive value means ratings "
        "and targets disagree about direction, which usually indicates a broken "
        "rating mapping rather than genuine analyst inconsistency.",
        {"rating_return_corr": corr, "n_rated": int(len(rated))},
    ))

    # -- 9. vendor disagreement ------------------------------------------
    sources = sorted(df["source"].unique().tolist())
    if len(sources) >= 2:
        key = ["analyst_firm", "action_date"]
        wide = df.pivot_table(index=key, columns="source", values="price_target",
                              aggfunc="first")
        both = wide.dropna()
        disagree = float("nan")
        if len(both) and both.shape[1] >= 2:
            a, b = both.iloc[:, 0], both.iloc[:, 1]
            disagree = float(np.mean(np.abs(a / b - 1.0) > 0.001))
        checks.append(Check(
            "vendor agreement", not (np.isfinite(disagree) and disagree > 0.05),
            WARN,
            f"{len(both)} events are covered by more than one vendor; they "
            f"disagree on the target in {disagree:.1%} of cases. Vendors differ on "
            "staleness windows and panel inclusion, which is exactly why this "
            "pipeline consumes per-analyst records rather than anyone's consensus.",
            {"n_overlapping_events": int(len(both)), "disagreement_rate": disagree,
             "sources": sources},
        ))
    else:
        checks.append(Check(
            "vendor agreement", True, INFO,
            f"only one source present ({sources[0]}), so cross-vendor disagreement "
            "cannot be measured. Single-source panels inherit that vendor's "
            "inclusion rules invisibly.",
            {"sources": sources},
        ))

    # -- 10. target round-number clustering -------------------------------
    pts = df.loc[df["price_target_used"].notna(), "price_target_used"]
    round5 = float(np.mean(np.isclose(pts % 5, 0) | np.isclose(pts % 5, 5))) if len(pts) else float("nan")
    checks.append(Check(
        "target granularity", True, INFO,
        f"{round5:.0%} of targets are exact multiples of 5. Heavy round-number "
        "clustering is normal for real sell-side targets and is worth remembering "
        "when a density is estimated from them: the underlying grid is coarse.",
        {"share_multiple_of_5": round5, "n_distinct_targets": int(pts.nunique())},
    ))

    return QualityReport(
        ticker=ticker, asof=asof, pit_mode=pit_mode,
        is_synthetic=bool(panel.attrs.get("is_synthetic", False)),
        checks=checks, provenance=prov,
    )


def render_quality_report(rep: QualityReport) -> str:
    rule = "=" * 78
    out = [rule, f"  DATA QUALITY -- {rep.ticker} as of {rep.asof}  "
                 f"(pit_mode={rep.pit_mode})"]
    if rep.is_synthetic:
        out.append("  *** SIMULATED PANEL -- these checks exercise the gate, they do")
        out.append("      not certify anything about a real vendor's data. ***")
    out.append(rule)
    p = rep.provenance
    if p:
        out.append(f"  store: {p.get('n_rows', 0)} rows / {p.get('n_events', 0)} events / "
                   f"{p.get('n_firms', 0)} firms, "
                   f"{p.get('first_action')} .. {p.get('last_action')}")
        out.append(f"  sources: {', '.join(p.get('sources', []))}   "
                   f"restated events: {p.get('n_restated_events', 0)}")
    out.append("")

    for c in rep.checks:
        mark = "PASS" if c.passed else ("FAIL" if c.severity == CRITICAL else "warn")
        out.append(f"  [{mark:>4}] {c.name}  ({c.severity})")
        for line in _wrap(c.detail, 72):
            out.append(f"         {line}")
        out.append("")

    out.append("-" * 78)
    if rep.passed:
        out.append("  GATE: PASSED -- no critical check failed.")
    else:
        out.append("  GATE: FAILED -- the brief says do not build the composite signal")
        out.append("  on this panel. Failing checks:")
        for c in rep.failures:
            if c.severity == CRITICAL:
                out.append(f"    - {c.name}")
    out.append(rule)
    return "\n".join(out)


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines
