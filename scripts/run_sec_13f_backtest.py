"""Historical backtest: track a fixed panel of 10 well-known active hedge
funds through SEC 13F filings back to 2021, build a dollar-weighted
consensus portfolio from their aggregated holdings each quarter, and
compare its performance against each individual fund's own 13F-reconstructed
portfolio and against SPY.

Long-running (23 quarterly SEC downloads + CUSIP resolution + price
history for every ticker involved) -- meant to run in the background.

Usage: python3 scripts/run_sec_13f_backtest.py
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
    CACHE_DIR,
    download_period,
    load_period_frames,
    dominant_report_period,
    extract_holdings,
    resolve_cusips_to_tickers,
    KNOWN_ACTIVE_FUNDS,
)
from analyst_price_target_backtest import fetch_price_history_cached, _price_at_or_before  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results" / "sec_13f_backtest"

# Fixed panel: today's top-10 (see run_sec_13f_snapshot.py), tracked back
# through history by CIK. `ciks` lists every CIK this fund identity has
# filed under -- confirmed live that Pershing Square filed this quarter
# under a brand new CIK (2026053) distinct from its prior one (1336528),
# a real corporate restructuring, not a data error; matching by name alone
# would have missed it, and matching by a single fixed CIK would too.
FUND_PANEL = {
    "BERKSHIRE": {"ciks": [1067983], "display": "Berkshire Hathaway"},
    "COATUE": {"ciks": [1135730], "display": "Coatue Management"},
    "VIKING": {"ciks": [1103804], "display": "Viking Global"},
    "TIGER_GLOBAL": {"ciks": [1167483], "display": "Tiger Global"},
    "ELLIOTT": {"ciks": [1791786], "display": "Elliott Management"},
    "FARALLON": {"ciks": [909661], "display": "Farallon Capital"},
    "PERSHING_SQUARE": {"ciks": [2026053, 1336528], "display": "Pershing Square"},
    "LONE_PINE": {"ciks": [1061165], "display": "Lone Pine Capital"},
    "ICAHN": {"ciks": [921669], "display": "Icahn"},
    "SOROS": {"ciks": [1029160], "display": "Soros Fund Management"},
}
ALL_TARGET_CIKS = [c for f in FUND_PANEL.values() for c in f["ciks"]]

# Oldest -> newest. Mix of clean quarterly hrefs (2021-2023) and the
# irregular 2-3 month filing-receipt windows SEC switched to in 2024+
# (each still dominated by one calendar report-quarter -- see
# dominant_report_period()).
PERIOD_HREFS = [
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

TOP_N_PER_PORTFOLIO = 15


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def cik_to_fund_id(cik: int) -> str | None:
    for fid, spec in FUND_PANEL.items():
        if cik in spec["ciks"]:
            return fid
    return None


def load_period_snapshot(href: str) -> dict | None:
    """Returns {report_period, fund_id -> {cik, accession, filing_date, holdings df (top N by value)}}."""
    pdir = download_period(href)
    frames = load_period_frames(pdir)
    sub, cov = frames["submission"], frames["coverpage"]
    period = dominant_report_period(sub)

    hr = sub[(sub["PERIODOFREPORT"] == period) & (sub["SUBMISSIONTYPE"].isin(["13F-HR", "13F-HR/A"])) & (sub["CIK"].isin(ALL_TARGET_CIKS))]
    if hr.empty:
        return None
    hr = hr.merge(cov[["ACCESSION_NUMBER", "FILINGMANAGER_NAME"]], on="ACCESSION_NUMBER")
    # prefer amendment over original if both exist for the same CIK+period
    hr = hr.sort_values("SUBMISSIONTYPE", ascending=True).drop_duplicates("CIK", keep="last")
    hr["fund_id"] = hr["CIK"].apply(cik_to_fund_id)

    accessions = hr["ACCESSION_NUMBER"].tolist()
    holdings = extract_holdings(pdir, accessions)
    if holdings.empty:
        return None

    acc_to_cik = dict(zip(hr["ACCESSION_NUMBER"], hr["CIK"]))
    holdings["cik"] = holdings["ACCESSION_NUMBER"].map(acc_to_cik)
    holdings["fund_id"] = holdings["cik"].apply(cik_to_fund_id)

    acc_to_filing_date = dict(zip(hr["ACCESSION_NUMBER"], hr["FILING_DATE"]))
    visible_date = max(acc_to_filing_date.values())  # when the whole basket became public

    return {"report_period": period, "visible_date": visible_date, "holdings": holdings, "fund_ids_present": sorted(hr["fund_id"].dropna().unique())}


def build_weighted_portfolio(holdings_subset: pd.DataFrame, top_n: int) -> pd.DataFrame:
    grouped = holdings_subset.groupby("CUSIP").agg(name=("NAMEOFISSUER", "first"), value=("VALUE", "sum")).reset_index()
    grouped = grouped.sort_values("value", ascending=False).head(top_n)
    grouped["weight"] = grouped["value"] / grouped["value"].sum()
    return grouped


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshots = []
    for href in PERIOD_HREFS:
        log(f"Processing {href.split('/')[-1]}...")
        snap = load_period_snapshot(href)
        if snap is None:
            log("  -> no data, skipping")
            continue
        log(f"  -> report_period={snap['report_period']} visible_date={snap['visible_date']} funds_present={snap['fund_ids_present']}")
        snapshots.append(snap)

    log(f"Loaded {len(snapshots)} period snapshots. Resolving CUSIPs to tickers...")
    all_cusips = set()
    for snap in snapshots:
        all_cusips.update(snap["holdings"]["CUSIP"].unique())
    ticker_map = resolve_cusips_to_tickers(list(all_cusips))
    log(f"Resolved {sum(1 for v in ticker_map.values() if v)}/{len(all_cusips)} CUSIPs to tickers.")

    # portfolios per snapshot: consensus + each fund present
    for snap in snapshots:
        snap["consensus_portfolio"] = build_weighted_portfolio(snap["holdings"], TOP_N_PER_PORTFOLIO)
        snap["fund_portfolios"] = {}
        for fid in snap["fund_ids_present"]:
            sub = snap["holdings"][snap["holdings"]["fund_id"] == fid]
            snap["fund_portfolios"][fid] = build_weighted_portfolio(sub, TOP_N_PER_PORTFOLIO)

    all_tickers = {ticker_map[c] for c in all_cusips if ticker_map.get(c)}
    all_tickers.add("SPY")
    log(f"Fetching price history for {len(all_tickers)} tickers...")
    price_series = {}
    for i, t in enumerate(sorted(all_tickers)):
        s = fetch_price_history_cached(t)
        if s is not None:
            price_series[t] = s
        if (i + 1) % 25 == 0:
            log(f"  fetched {i + 1}/{len(all_tickers)}")

    log("Computing period returns...")

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
        weights = np.array(weights) / np.sum(weights)  # renormalize over resolved tickers only
        return float(np.sum(np.array(rets) * weights))

    nav = {"consensus": [1.0], "SPY": [1.0]}
    for fid in FUND_PANEL:
        nav[fid] = [1.0]
    period_labels = [snapshots[0]["report_period"]]

    for i in range(len(snapshots) - 1):
        entry_ts = pd.Timestamp(snapshots[i]["visible_date"], tz="UTC")
        exit_ts = pd.Timestamp(snapshots[i + 1]["visible_date"], tz="UTC")
        period_labels.append(snapshots[i + 1]["report_period"])

        r = portfolio_return(snapshots[i]["consensus_portfolio"], entry_ts, exit_ts)
        nav["consensus"].append(nav["consensus"][-1] * (1 + r) if r is not None else nav["consensus"][-1])

        spy = _price_at_or_before(price_series["SPY"], entry_ts), _price_at_or_before(price_series["SPY"], exit_ts)
        spy_ret = (spy[1] / spy[0] - 1) if spy[0] and spy[1] else None
        nav["SPY"].append(nav["SPY"][-1] * (1 + spy_ret) if spy_ret is not None else nav["SPY"][-1])

        for fid in FUND_PANEL:
            if fid in snapshots[i]["fund_portfolios"]:
                r = portfolio_return(snapshots[i]["fund_portfolios"][fid], entry_ts, exit_ts)
                nav[fid].append(nav[fid][-1] * (1 + r) if r is not None else nav[fid][-1])
            else:
                nav[fid].append(nav[fid][-1])  # fund absent this period (didn't file / not in panel) -- carry flat

    with open(RESULTS_DIR / "nav_series.json", "w") as f:
        json.dump({"period_labels": period_labels, "nav": nav}, f, indent=2)

    stats = {}
    for key, series in nav.items():
        arr = np.array(series)
        total_return = arr[-1] / arr[0] - 1
        n_years = (len(arr) - 1) / 4  # quarterly periods
        cagr = (arr[-1] / arr[0]) ** (1 / n_years) - 1 if n_years > 0 else None
        period_rets = np.diff(arr) / arr[:-1]
        vol = float(np.std(period_rets) * np.sqrt(4)) if len(period_rets) > 1 else None
        running_max = np.maximum.accumulate(arr)
        max_dd = float(np.min(arr / running_max - 1))
        stats[key] = {
            "total_return": float(total_return), "cagr": float(cagr) if cagr is not None else None,
            "annualized_vol": vol, "max_drawdown": max_dd, "final_nav": float(arr[-1]),
        }
    with open(RESULTS_DIR / "stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    write_report(period_labels, nav, stats, snapshots[-1])
    log(f"Wrote report to {RESULTS_DIR / 'report.md'}")


def write_report(period_labels, nav, stats, latest_snap) -> None:
    lines = []
    lines.append("# SEC 13F smart-money consensus backtest\n")
    lines.append(
        f"Tracking {len(FUND_PANEL)} well-known active hedge funds "
        f"({', '.join(s['display'] for s in FUND_PANEL.values())}) through "
        f"SEC 13F filings from {period_labels[0]} to {period_labels[-1]} "
        f"({len(period_labels) - 1} quarterly rebalances). Each quarter, a "
        "consensus portfolio is built from the top holdings across all "
        "funds that filed that quarter (dollar-value weighted), 'bought' "
        "only from the date the LAST of that quarter's filings became "
        "public (the real 45-day disclosure lag, not the quarter-end date), "
        "and held until the next quarter's basket is revealed.\n"
    )
    lines.append("## Performance summary\n")
    lines.append("| Portfolio | Total return | CAGR | Annualized vol | Max drawdown | Final NAV (start=1.0) |")
    lines.append("|---|---|---|---|---|---|")
    display_names = {"consensus": "**Consensus (all 10 funds)**", "SPY": "SPY (benchmark)"}
    display_names.update({fid: spec["display"] for fid, spec in FUND_PANEL.items()})
    order = ["consensus", "SPY"] + list(FUND_PANEL.keys())
    for key in order:
        s = stats.get(key)
        if not s:
            continue
        cagr = f"{s['cagr']*100:.1f}%" if s["cagr"] is not None else "n/a"
        vol = f"{s['annualized_vol']*100:.1f}%" if s["annualized_vol"] is not None else "n/a"
        lines.append(
            f"| {display_names.get(key, key)} | {s['total_return']*100:+.1f}% | {cagr} | "
            f"{vol} | {s['max_drawdown']*100:.1f}% | {s['final_nav']:.2f} |"
        )
    lines.append(
        "\nNote: these are GROSS returns from 13F-reconstructed top-holdings "
        "portfolios only -- no transaction costs, no fees (real hedge funds "
        "charge management + performance fees that eat heavily into net "
        "investor returns), no short positions, no non-13F assets, and no "
        "reflection of each fund's actual trade timing/sizing within the "
        "quarter. A fund's TRUE net-of-fee return will differ, often "
        "substantially, from its 13F-reconstructed gross return shown here.\n"
    )
    lines.append("## Current (latest quarter) consensus portfolio\n")
    lines.append(f"Report period: {latest_snap['report_period']}, funds filing: {', '.join(latest_snap['fund_ids_present'])}\n")
    lines.append("## Caveats\n")
    lines.append(
        "- 13F only covers long US-listed equity + listed options positions "
        "over the reporting threshold -- no shorts, no international "
        "holdings, no bonds/cash/private investments. Some funds' real "
        "portfolios (and real risk) look very different from their 13F.\n"
        "- Fund identity tracked by CIK, with one known manual correction "
        "(Pershing Square re-filed under a new CIK due to a real corporate "
        "restructuring) -- other undiscovered entity renames/CIK changes "
        "among the panel over 2021-2026 could silently break a fund's "
        "continuity in this series.\n"
        "- \"Visible date\" uses the actual filing date of the last fund in "
        "the basket to file that quarter -- real, not a fixed 45-day "
        "assumption -- but a fund's true trade could have happened weeks to "
        "months before its filing date.\n"
        "- Equal treatment of a position whether it's a core long-term "
        "holding or a position about to be exited -- no attempt to weight "
        "by conviction beyond dollar size.\n"
    )
    (RESULTS_DIR / "report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
