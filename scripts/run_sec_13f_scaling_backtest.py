"""Does the smart-money consensus get better, worse, or just smoother as
you widen the panel from 10 funds to 20, 50, 100? Unlike
run_sec_13f_backtest.py's FIXED 10-fund panel (tracked by CIK across time,
which only works because 10 well-known funds can be manually vetted and
alias-tracked), this uses a ROLLING selection: each quarter, independently
pick the top N "concentrated, high-value, not obviously a bank/insurer/
pension-fund/corporate-treasury" managers from that quarter's own SEC data
(select_top_funds_objective -- no name curation, so it scales to any N).
No identity-tracking is attempted across quarters; the panel is "whoever
qualifies this quarter," which is also just a more realistic simulation of
mechanically rebalancing to a rules-based top-N list every quarter.

Long-running -- meant for the background.

Usage: python3 scripts/run_sec_13f_scaling_backtest.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sec_13f_wisdom import (  # noqa: E402
    download_period,
    select_top_funds_objective,
    extract_holdings,
    resolve_cusips_to_tickers,
)
from analyst_price_target_backtest import fetch_price_history_cached, _price_at_or_before  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results" / "sec_13f_scaling_backtest"

EARLY_YEARS = [2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020]
EARLY_PERIOD_HREFS = [
    f"/files/structureddata/data/form-13f-data-sets/{y}q{q}_form13f.zip"
    for y in EARLY_YEARS for q in [1, 2, 3, 4]
    if not (y == 2013 and q == 1)  # SEC's structured archive starts at 2013q2
]

PERIOD_HREFS = EARLY_PERIOD_HREFS + [
    "/files/structureddata/data/form-13f-data-sets/2021q1_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/2021q2_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/2021q3_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/2021q4_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/2022q1_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/2022q2_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/2022q3_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/2022q4_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/2023q1_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/2023q2_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/2023q3_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/2023q4_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/01jan2024-29feb2024_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/01mar2024-31may2024_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/01jun2024-31aug2024_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/01sep2024-30nov2024_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/01dec2024-28feb2025_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/01mar2025-31may2025_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/01jun2025-31aug2025_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/01sep2025-30nov2025_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/01dec2025-28feb2026_form13f.zip",
    "/files/structureddata/data/form-13f-data-sets/01mar2026-31may2026_form13f.zip",
    "/files/datastandardsinnovation/data/form-13f-data-sets/01jun2026-31aug2026_form13f.zip",
]

N_VALUES = [10, 20, 50]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def holdings_per_portfolio(n_funds: int) -> int:
    return min(150, n_funds * 2)


def build_weighted_portfolio(holdings_subset: pd.DataFrame, top_n: int) -> pd.DataFrame:
    grouped = holdings_subset.groupby("CUSIP").agg(name=("NAMEOFISSUER", "first"), value=("VALUE", "sum")).reset_index()
    grouped = grouped.sort_values("value", ascending=False).head(top_n)
    grouped["weight"] = grouped["value"] / grouped["value"].sum()
    return grouped


SNAPSHOT_CACHE_DIR = RESULTS_DIR / "period_cache"


def _snapshot_cache_path(href: str, n_values: list[int]) -> Path:
    slug = Path(href).stem.replace("_form13f", "")
    n_tag = "-".join(str(n) for n in sorted(n_values))
    return SNAPSHOT_CACHE_DIR / f"{slug}__N{n_tag}.json"


def _snapshots_to_json(snaps: dict[int, dict]) -> dict:
    return {
        str(n): {
            "report_period": s["report_period"],
            "visible_date": s["visible_date"],
            "n_funds_actual": s["n_funds_actual"],
            "portfolio": s["portfolio"].to_dict(orient="records"),
        }
        for n, s in snaps.items()
    }


def _snapshots_from_json(raw: dict) -> dict[int, dict]:
    out = {}
    for n_str, s in raw.items():
        out[int(n_str)] = {
            "report_period": s["report_period"],
            "visible_date": s["visible_date"],
            "n_funds_actual": s["n_funds_actual"],
            "portfolio": pd.DataFrame(s["portfolio"]),
        }
    return out


def load_snapshots_for_period_cached(href: str, n_values: list[int]) -> dict[int, dict] | None:
    """Caches load_snapshots_for_period's result to disk per (period, N-set)
    -- this pipeline has needed re-running from scratch multiple times
    already (a nested-zip layout, a bond CUSIP crashing the price cache),
    each time losing everything already computed because nothing was
    persisted until the very end. This makes a rerun after any future bug
    resume instantly instead of repeating potentially hours of extraction.
    """
    SNAPSHOT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _snapshot_cache_path(href, n_values)
    if cache_path.exists():
        raw = json.loads(cache_path.read_text())
        return _snapshots_from_json(raw) if raw is not None else None
    snaps = load_snapshots_for_period(href, n_values)
    cache_path.write_text(json.dumps(_snapshots_to_json(snaps) if snaps else None))
    return snaps


def load_snapshots_for_period(href: str, n_values: list[int]) -> dict[int, dict] | None:
    """Extracts holdings ONCE per period at max(n_values) and derives every
    smaller tier as an in-memory subset, instead of re-selecting funds and
    re-scanning the ~350MB per-quarter holdings file once per tier.
    select_top_funds_objective ranks by value, so the top-10 funds are
    always a strict prefix of the top-20, which is a prefix of the top-50 --
    this cuts SEC-side I/O by ~3x for a 3-tier sweep. (First version of this
    script did one full pass per tier; N=10/20/50 took ~10/20/49 minutes
    respectively -- almost exactly linear in N -- confirming the grep pass
    itself, not fund selection, was the dominant cost.)
    """
    n_max = max(n_values)
    pdir = download_period(href)
    funds_max = select_top_funds_objective(pdir, n=n_max)
    if funds_max.empty:
        return None
    holdings_max = extract_holdings(pdir, funds_max["ACCESSION_NUMBER"].tolist())
    if holdings_max.empty:
        return None

    out = {}
    for n in n_values:
        funds_n = funds_max.head(n)  # already sorted descending by TABLEVALUETOTAL
        accs_n = set(funds_n["ACCESSION_NUMBER"])
        holdings_n = holdings_max[holdings_max["ACCESSION_NUMBER"].isin(accs_n)]
        if holdings_n.empty:
            continue
        acc_to_filing_date = dict(zip(funds_n["ACCESSION_NUMBER"], funds_n["FILING_DATE"]))
        visible_date = max(acc_to_filing_date.values())
        portfolio = build_weighted_portfolio(holdings_n, holdings_per_portfolio(n))
        out[n] = {
            "report_period": funds_n["report_period"].iloc[0],
            "visible_date": visible_date,
            "n_funds_actual": len(funds_n),
            "portfolio": portfolio,
        }
    return out


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_snapshots = {n: [] for n in N_VALUES}
    all_cusips = set()

    log(f"=== Building panels for N={N_VALUES} together (one extraction per period) ===")
    for href in PERIOD_HREFS:
        snaps = load_snapshots_for_period_cached(href, N_VALUES)
        if not snaps:
            log(f"  {href.split('/')[-1]}: no data")
            continue
        for n, snap in snaps.items():
            all_snapshots[n].append(snap)
            all_cusips.update(snap["portfolio"]["CUSIP"].unique())
        log(f"  {href.split('/')[-1]}: done ({snaps[N_VALUES[0]]['report_period']})")
    for n in N_VALUES:
        log(f"  N={n}: {len(all_snapshots[n])} periods loaded")

    log(f"Resolving {len(all_cusips)} distinct CUSIPs to tickers...")
    ticker_map = resolve_cusips_to_tickers(list(all_cusips))
    log(f"Resolved {sum(1 for v in ticker_map.values() if v)}/{len(all_cusips)}")

    all_tickers = {ticker_map[c] for c in all_cusips if ticker_map.get(c)}
    all_tickers.add("SPY")
    log(f"Fetching price history for {len(all_tickers)} tickers...")
    price_series = {}
    for i, t in enumerate(sorted(all_tickers)):
        s = fetch_price_history_cached(t)
        if s is not None:
            price_series[t] = s
        if (i + 1) % 50 == 0:
            log(f"  fetched {i + 1}/{len(all_tickers)}")

    def portfolio_return(port_df: pd.DataFrame, entry_ts, exit_ts) -> float | None:
        rets, weights = [], []
        for _, row in port_df.iterrows():
            ticker = ticker_map.get(row["CUSIP"])
            if not ticker or ticker not in price_series:
                continue
            p0 = _price_at_or_before(price_series[ticker], entry_ts)
            p1 = _price_at_or_before(price_series[ticker], exit_ts)
            if p0 is None or p1 is None or p0 <= 0:
                continue
            rets.append(p1 / p0 - 1)
            weights.append(row["weight"])
        if not rets:
            return None
        weights = np.array(weights) / np.sum(weights)
        return float(np.sum(np.array(rets) * weights))

    log("Computing NAV series per N...")
    nav = {}
    period_labels_by_n = {}
    for n in N_VALUES:
        snaps = all_snapshots[n]
        if len(snaps) < 2:
            continue
        series = [1.0]
        labels = [snaps[0]["report_period"]]
        for i in range(len(snaps) - 1):
            entry_ts = pd.Timestamp(snaps[i]["visible_date"], tz="UTC")
            exit_ts = pd.Timestamp(snaps[i + 1]["visible_date"], tz="UTC")
            r = portfolio_return(snaps[i]["portfolio"], entry_ts, exit_ts)
            series.append(series[-1] * (1 + r) if r is not None else series[-1])
            labels.append(snaps[i + 1]["report_period"])
        nav[f"N{n}"] = series
        period_labels_by_n[f"N{n}"] = labels

    # SPY over the full common window
    ref_labels = period_labels_by_n[f"N{N_VALUES[0]}"]
    spy_nav = [1.0]
    for i in range(len(all_snapshots[N_VALUES[0]]) - 1):
        entry_ts = pd.Timestamp(all_snapshots[N_VALUES[0]][i]["visible_date"], tz="UTC")
        exit_ts = pd.Timestamp(all_snapshots[N_VALUES[0]][i + 1]["visible_date"], tz="UTC")
        p0 = _price_at_or_before(price_series["SPY"], entry_ts)
        p1 = _price_at_or_before(price_series["SPY"], exit_ts)
        r = (p1 / p0 - 1) if p0 and p1 else None
        spy_nav.append(spy_nav[-1] * (1 + r) if r is not None else spy_nav[-1])
    nav["SPY"] = spy_nav

    with open(RESULTS_DIR / "nav_series.json", "w") as f:
        json.dump({"period_labels": ref_labels, "nav": nav, "n_funds_actual": {
            f"N{n}": [s["n_funds_actual"] for s in all_snapshots[n]] for n in N_VALUES
        }}, f, indent=2)

    stats = {}
    for key, series in nav.items():
        arr = np.array(series)
        total_return = arr[-1] / arr[0] - 1
        n_years = (len(arr) - 1) / 4
        cagr = (arr[-1] / arr[0]) ** (1 / n_years) - 1 if n_years > 0 else None
        period_rets = np.diff(arr) / arr[:-1]
        vol = float(np.std(period_rets) * np.sqrt(4)) if len(period_rets) > 1 else None
        running_max = np.maximum.accumulate(arr)
        max_dd = float(np.min(arr / running_max - 1))
        stats[key] = {"total_return": float(total_return), "cagr": float(cagr) if cagr is not None else None,
                       "annualized_vol": vol, "max_drawdown": max_dd, "final_nav": float(arr[-1])}
    with open(RESULTS_DIR / "stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    write_report(stats, all_snapshots)
    log(f"Wrote report to {RESULTS_DIR / 'report.md'}")


def write_report(stats, all_snapshots) -> None:
    lines = []
    lines.append("# Does a bigger smart-money panel help? (N=10/20/50/100)\n")
    lines.append(
        "Same consensus-portfolio backtest as the curated 10-fund version, "
        "but the panel is now selected objectively every quarter (no "
        "hand-picked names): rank all 13F filers by their own reported "
        "value, restricted to a plausible 'concentrated active manager' "
        "position-count range, with a best-effort exclusion of insurers, "
        "pension funds, banks, and corporate treasury self-filers (see "
        "src/sec_13f_wisdom.py's NON_FUND_KEYWORDS). Unlike the curated "
        "backtest, the panel membership is NOT tracked as fixed identities "
        "across time -- each quarter independently re-selects 'whoever "
        "qualifies as top-N right now.'\n"
    )
    lines.append("## Results\n")
    lines.append("| Panel | Total return | CAGR | Annualized vol | Max drawdown |")
    lines.append("|---|---|---|---|---|")
    order = [f"N{n}" for n in N_VALUES] + ["SPY"]
    for key in order:
        s = stats.get(key)
        if not s:
            continue
        cagr = f"{s['cagr']*100:.1f}%" if s["cagr"] is not None else "n/a"
        vol = f"{s['annualized_vol']*100:.1f}%" if s["annualized_vol"] is not None else "n/a"
        lines.append(f"| {key} | {s['total_return']*100:+.1f}% | {cagr} | {vol} | {s['max_drawdown']*100:.1f}% |")

    lines.append("\n## Caveats\n")
    lines.append(
        "- The exclusion filter is best-effort keyword/name matching, not a "
        "guarantee -- some non-stock-picker institutions (e.g. "
        "market-making/prop-trading shops with moderate position counts, "
        "like CTC LLC) can still pass through.\n"
        "- No identity tracking across quarters at this scale -- a fund can "
        "drop in and out of the panel from quarter to quarter as its "
        "ranking changes, which is different from (and arguably more "
        "realistic than) the curated backtest's fixed 10-fund panel.\n"
        "- Same gross-returns caveat as the curated backtest: no fees, no "
        "transaction costs, no shorts, no non-13F assets.\n"
    )
    (RESULTS_DIR / "report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
