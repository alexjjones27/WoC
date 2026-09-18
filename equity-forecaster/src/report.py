"""Terminal reporting for Stage 2.

The point of the Stage 2 report is comparison, not a number: the raw panel and
the de-biased panel side by side, with enough diagnostics to see WHY they
differ and enough caveats that nobody mistakes the output for a signal.
"""
from __future__ import annotations

import numpy as np

from . import config
from .model.debias import DebiasResult

RULE = "=" * 78
THIN = "-" * 78


def _pct(x, dp=1):
    return "n/a" if x is None or not np.isfinite(x) else f"{x * 100:+.{dp}f}%"


def _px(x, dp=2):
    return "n/a" if x is None or not np.isfinite(x) else f"{x:,.{dp}f}"


def banner(result: DebiasResult) -> list[str]:
    out = [RULE]
    if result.is_synthetic:
        out += [
            "  *** SIMULATED PANEL -- NOT DATA ***",
            "  No vendor credential was available, so the analyst panel below was",
            "  GENERATED (src/ingest/synthetic.py) on top of this ticker's real",
            "  prices. It exercises the pipeline. It says nothing whatsoever about",
            f"  {result.ticker} and must never be quoted as a finding.",
            RULE,
        ]
    out += [
        f"  STAGE 2 DE-BIASING REPORT -- {result.ticker}",
        f"  as of {result.asof}   spot {_px(result.spot)} (close {result.spot_date})",
        f"  sources: {', '.join(result.diagnostics['sources']) or 'none'}",
        f"  point-in-time mode: {result.pit_mode}",
    ]
    if result.pit_mode == "assume_vendor_history":
        out += [
            "    ^ assumes the vendor's back-history equals what was published at",
            "      the time. Usually false in some degree; see src/store/pit.py.",
        ]
    out.append(RULE)
    return out


def panel_composition(result: DebiasResult) -> list[str]:
    d = result.diagnostics
    out = ["", "PANEL COMPOSITION", THIN,
           f"  forecast events visible        {d['n_events_total']:>6}",
           f"  usable (spot + target + sane)  {d['n_usable']:>6}",
           f"  inside {d['max_level_age_days']}-day level window     {d['n_in_level_window']:>6}"
           f"   from {d['n_firms_in_window']} firms",
           f"  median age in window           {d['median_age_days']:>6.0f} days",
           f"  vendor-restated events         {d['n_restated_events']:>6}"
           "   (original value used, restatement ignored)"]
    flags = {k: v for k, v in d["flag_counts"].items() if v}
    if flags:
        out.append("  quality flags:")
        for k, v in sorted(flags.items(), key=lambda kv: -kv[1]):
            out.append(f"      {k[5:]:<26} {v:>6}")
    else:
        out.append("  quality flags:                 none raised")
    out.append(f"  anchor price convention        {d['spot_convention']}")
    return out


def firm_offsets_table(result: DebiasResult, top: int = 25) -> list[str]:
    fo = result.firm_offsets
    out = ["", "STEP (a) FIRM ANCHORING OFFSETS", THIN,
           f"  fitted on {result.diagnostics['offsets_fitted_on_records']} records "
           f"across {fo.n_tickers} ticker(s) and {fo.n_firms} firms",
           f"  panel mean log offset  {fo.mu_global:+.4f}  "
           f"({_pct(np.exp(fo.mu_global) - 1)} in return terms)",
           f"  between-firm variance of true offsets (tau^2)  {fo.tau2:.5f}",
           f"  mean James-Stein shrinkage toward the panel     {fo.mean_shrinkage:.3f}"]
    if fo.mean_shrinkage > 0.8:
        out.append("      ^ high: the panel cannot really tell firms apart, so this")
        out.append("        correction is close to subtracting a single constant.")
    out += ["",
            f"  {'firm':<26}{'n':>5}{'raw mean':>11}{'shrunk mu':>11}{'shrink':>8}"]
    in_panel = set(result.panel[result.panel["age_weight"] > 0].get(
        "analyst_firm_canonical", result.panel["analyst_firm"]))
    tab = fo.table[fo.table["analyst_firm"].isin(in_panel)] \
        .sort_values("n_obs", ascending=False).head(top)
    for _, r in tab.iterrows():
        out.append(
            f"  {str(r['analyst_firm'])[:25]:<26}{int(r['n_obs']):>5}"
            f"{_pct(np.exp(r['raw_mean_log']) - 1):>11}"
            f"{_pct(r['mu_return']):>11}{r['shrinkage']:>8.2f}"
        )
    out.append(f"  (showing the {len(tab)} firms with a live target on this ticker; "
               f"{fo.n_firms} firms were fitted in total)")
    return out


def decay_section(result: DebiasResult) -> list[str]:
    f = result.age_decay
    out = ["", "STEP (b) AGE DECAY (FITTED, NEVER ASSUMED)", THIN]
    if f.fitted:
        out += [
            f"  lambda      {f.lam:.5f} / day",
            f"  half-life   {f.half_life_days:.0f} days",
            f"  t-stat      {f.tstat:.2f}  (cluster-robust by evaluation date)",
            f"  95% CI      [{f.ci[0]:.5f}, {f.ci[1]:.5f}] per day",
            f"  fitted on   {f.n_obs:,} (age, error) pairs over {f.n_clusters} evaluation dates",
        ]
        if f.pooled_lam is not None:
            out.append(f"  pooled (no date FE) lambda {f.pooled_lam:.5f} / day")
            ratio = f.pooled_lam / f.lam if f.lam else float("nan")
            if np.isfinite(ratio) and not (0.5 <= ratio <= 2.0):
                out.append("     ^ differs from the within-date estimate by more than 2x:")
                out.append("       the pooled slope is picking up which PERIODS were hard,")
                out.append("       not how forecasts decay. The within-date number is used.")
    else:
        out += [
            "  NOT FITTED -- flat weights used inside the hard age cutoff.",
            f"  reason: {f.reason}",
        ]
        if f.n_obs:
            out.append(f"  (sample seen: {f.n_obs:,} pairs, {f.n_clusters} evaluation dates)")
    if len(f.buckets):
        out += ["", "  realised forecast error by record age:",
                f"  {'age bucket':<16}{'n':>8}{'RMSE(log)':>12}{'MAE(log)':>11}"]
        for _, r in f.buckets.iterrows():
            out.append(f"  {str(r['age_bucket']):<16}{int(r['n']):>8}"
                       f"{r['rmse_log']:>12.4f}{r['mae_log']:>11.4f}")
    return out


def sector_section(result: DebiasResult) -> list[str]:
    b = result.beta
    out = ["", "STEP (c) SECTOR / BETA ADJUSTMENT", THIN,
           f"  sector proxy   {b.sector_symbol}"
           f"{'' if b.is_real_sector else '   (BROAD-MARKET FALLBACK, not a sector)'}",
           f"  beta used      {b.beta:.3f}"
           f"{'' if b.fitted else '   (NOT FITTED: ' + b.reason + ')'}"]
    if b.fitted:
        out.append(f"  raw OLS beta   {b.beta_raw:.3f} (se {b.se:.3f}); Vasicek weight on"
                   f" the prior beta=1 is {b.shrinkage:.3f}")
        out.append(f"  fitted on      {b.n_obs} days, R^2 {b.r2:.3f}")
        if np.isfinite(b.r2) and b.r2 < config.BETA_LOW_R2:
            out.append("  note           the sector explains little of this stock's daily")
            out.append("                 variance, so this step removes little")
    moves = result.panel.loc[result.panel["age_weight"] > 0, "sector_move"].dropna()
    if len(moves):
        out.append(f"  sector move since action, records in window: "
                   f"median {_pct(float(moves.median()))}, "
                   f"range {_pct(float(moves.min()))} .. {_pct(float(moves.max()))}")
    return out


def distribution_table(result: DebiasResult) -> list[str]:
    rows = [
        ("raw implied return", result.raw),
        ("  - firm anchoring", result.after_firm),
        ("  - sector move", result.after_sector),
        ("  x age weight", result.final),
    ]
    out = ["", "THE DISTRIBUTION, RAW AND DE-BIASED", THIN,
           f"  {'stage':<22}{'n':>5}{'n_eff':>8}{'p25':>10}{'median':>10}"
           f"{'p75':>10}{'IQR':>9}{'sd':>9}"]
    for label, d in rows:
        out.append(
            f"  {label:<22}{d.n:>5}{d.n_eff:>8.1f}{_pct(d.p25):>10}"
            f"{_pct(d.median):>10}{_pct(d.p75):>10}{_pct(d.iqr):>9}{_pct(d.sd):>9}"
        )
    out += ["",
            "  n_eff here measures only how concentrated the AGE WEIGHTS are; with",
            "  flat weights it equals n. It is not yet the effective number of",
            "  INDEPENDENT opinions -- that needs Stage 3's correlation penalty,",
            "  and until then n_eff overstates the crowd.",
            "", "  implied 12-month price levels (spot "
            f"{_px(result.spot)}):",
            f"  {'stage':<22}{'p25':>12}{'median':>12}{'p75':>12}"]
    for label, d in rows:
        p = d.as_prices(result.spot)
        out.append(f"  {label:<22}{_px(p['p25']):>12}{_px(p['median']):>12}{_px(p['p75']):>12}")
    return out


def histograms(result: DebiasResult, bins: int = 18, width: int = 28) -> list[str]:
    df = result.panel[result.panel["age_weight"] > 0]
    raw = df["implied_return"].dropna().to_numpy()
    deb = df["resid_return"].dropna().to_numpy()
    if raw.size == 0 or deb.size == 0:
        return []
    lo = float(min(raw.min(), deb.min()))
    hi = float(max(raw.max(), deb.max()))
    if hi - lo < 1e-9:
        return []
    edges = np.linspace(lo, hi, bins + 1)
    hr, _ = np.histogram(raw, bins=edges)
    hd, _ = np.histogram(deb, bins=edges)
    scale = max(hr.max(), hd.max()) or 1
    out = ["", "SHAPE, SIDE BY SIDE", THIN,
           f"  {'bucket':<16}{'raw':<{width + 6}}de-biased"]
    for i in range(bins):
        mid = (edges[i] + edges[i + 1]) / 2
        br = "#" * int(round(width * hr[i] / scale))
        bd = "#" * int(round(width * hd[i] / scale))
        zero = " <- 0%" if edges[i] <= 0.0 < edges[i + 1] else ""
        out.append(f"  {_pct(mid, 0):>7}{'':<9}{br:<{width}}{hr[i]:>4}  "
                   f"{bd:<{width}}{hd[i]:>4}{zero}")
    return out


def variance_section(result: DebiasResult) -> list[str]:
    d = result.diagnostics
    before, after = d["firm_variance_share_raw"], d["firm_variance_share_after"]
    scope = d["firm_variance_scope"]
    out = ["", "WHAT THE FIRM CORRECTION ACTUALLY REMOVED", THIN,
           f"  measured on the {scope}: {before.n_obs} records, "
           f"{before.n_firms} firms ({before.obs_per_firm:.1f} per firm)",
           "",
           "  share of variance in log implied return explained by firm identity,",
           "  after removing each ticker's own mean (so a firm's coverage mix",
           "  cannot masquerade as its anchoring habit)",
           f"  {'':<22}{'raw R^2':>10}{'adjusted':>11}",
           f"  {'before correction':<22}{before.share:>10.3f}{before.adjusted:>11.3f}",
           f"  {'after correction':<22}{after.share:>10.3f}{after.adjusted:>11.3f}"]
    if not before.reliable:
        out += ["",
                "  NOT RELIABLE: too few records per firm. With roughly one record",
                "  each, firm dummies fit the data perfectly whatever the truth is,",
                "  so the raw share is near 1.0 by construction. The adjusted figure",
                "  is the one to read, and it goes negative exactly when the fit is",
                "  indistinguishable from noise."]
    elif before.adjusted > 0.25:
        out += ["",
                "  A large share means an implied return tells you more about which",
                "  firm wrote it than about the stock. That is the anchoring effect",
                "  the correction exists to remove, and it is present here."]
    else:
        out += ["",
                "  A small share means firm identity is not the dominant axis of",
                "  disagreement in this panel, so step (a) is a minor correction."]
    return out


def footer(result: DebiasResult) -> list[str]:
    out = ["", THIN, "CAVEATS", THIN]
    for w in dict.fromkeys(result.warnings):
        out.append(f"  ! {w}")
    out += [
        "  ! This is a Stage 2 diagnostic, NOT a signal. There is no skill",
        "    weighting, no correlation penalty, no options-implied anchor, no",
        "    positioning tilt and no abstention rule yet (Stages 3-7).",
        "  ! Nothing here has been validated against spot as a 12-month",
        "    predictor. Until Stage 8's null test runs, the correct prior is",
        "    that this distribution carries no information.",
        RULE,
    ]
    return out


def render(result: DebiasResult) -> str:
    parts = (
        banner(result)
        + panel_composition(result)
        + firm_offsets_table(result)
        + decay_section(result)
        + sector_section(result)
        + distribution_table(result)
        + histograms(result)
        + variance_section(result)
        + footer(result)
    )
    return "\n".join(parts)
