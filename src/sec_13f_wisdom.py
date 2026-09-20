"""Smart-money wisdom of the crowd: aggregate SEC Form 13F holdings from a
curated set of well-known active hedge funds into a consensus portfolio,
track quarter-over-quarter position changes, and (in the runner scripts)
backtest that consensus portfolio's historical performance against the
individual funds and a benchmark.

Data source: SEC's own quarterly Form 13F structured datasets (free, public,
no API key -- https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets).
Every institutional manager with >$100M in US equities must file a 13F
within 45 days of quarter-end, disclosing every long common-stock/option
position over a small threshold. This is the closest thing to genuine
"smart money" wisdom of the crowd that's freely available -- real capital
allocation decisions by professional managers, not a survey.

Two things this is NOT:
  1. NOT real-time. There is a mandatory ~45-day filing lag, and a fund's
     position may have changed again by the time you read the filing. Any
     backtest here "buys" a quarter's aggregated portfolio only from the
     filing date onward, never before -- but even that lags the fund's own
     true trade by weeks to months.
  2. NOT a full picture of any given fund. 13F only covers US-listed long
     equity positions (plus listed options) over the reporting threshold --
     no shorts, no non-US holdings, no bonds/cash/private investments. A
     macro fund like Bridgewater looks nearly empty in 13F data because its
     real risk lives elsewhere; that's why it's excluded from the curated
     list below rather than included and misread as "Bridgewater's real
     portfolio."

Fund selection: ranking by raw reported 13F value would just hand back
BlackRock/Vanguard/State Street -- passive index and custody giants that
mechanically own the whole market (BlackRock alone reports ~50,000
positions most quarters). That's not "top funds" in any useful sense, so
selection here is: (a) restricted to a curated list of well-known ACTIVE,
concentrated hedge funds/stock-pickers, (b) further filtered to managers
reporting a plausibly concentrated book (a position-count cap), (c) ranked
by their own reported 13F portfolio value within that filtered set. This
is derived entirely from SEC's own data, not an external "top funds" claim.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / "data" / "raw" / "sec_13f"
FIGI_CACHE_PATH = CACHE_DIR / "cusip_ticker_cache.json"

HEADERS = {"User-Agent": "wisdom-of-crowds-research contact@example.com"}
LISTING_URL = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"

# Curated, well-known ACTIVE/concentrated hedge funds and famous investors.
# Matched against COVERPAGE.FILINGMANAGER_NAME by substring (case-insensitive).
# Deliberately excludes quant/multi-strategy/market-making shops (Citadel,
# AQR, Two Sigma, Millennium, D.E. Shaw, Marshall Wace, Man Group) and pure
# asset managers (Neuberger Berman) -- their 13Fs run into the thousands of
# systematic positions and don't represent "top picks" the way a
# concentrated fundamental stock-picker's does. See module docstring.
KNOWN_ACTIVE_FUNDS = [
    "BERKSHIRE HATHAWAY", "TIGER GLOBAL", "THIRD POINT", "PERSHING SQUARE",
    "APPALOOSA", "DUQUESNE", "BAUPOST", "VIKING GLOBAL", "COATUE",
    "LONE PINE", "ELLIOTT", "SOROS FUND", "ICAHN", "VALUEACT",
    "GREENLIGHT CAPITAL", "SCION ASSET", "FARALLON", "YORK CAPITAL",
    "TIGER GLOBAL", "PSAGX", "PENNANT", "TWIN CEDAR", "KLARMAN",
]
MAX_POSITIONS_FOR_CONCENTRATED = 500  # excludes index-like/quant/market-making books

# Keyword blocklist for scaling BEYOND the curated KNOWN_ACTIVE_FUNDS list
# (used by select_top_funds_objective). Confirmed live: ranking purely by
# "concentrated + high value" with no name curation pulls in institution
# types that aren't stock-picking "smart money" at all -- insurers, pension
# funds, and companies filing 13F for their OWN corporate treasury (Alphabet
# Inc. and NVIDIA CORP both showed up this way, with $60-100B "portfolios"
# that are really just their own cash/investments). This is a best-effort,
# keyword-based filter, not a guarantee -- some non-fund institutions (e.g.
# market-making/prop-trading shops with moderate position counts) can still
# slip through, and this is documented as a known limitation, not fixed.
NON_FUND_KEYWORDS = [
    "INSURANCE", "ASSURANCE", "REINSURANCE", "PENSION", "RETIREMENT",
    "TEACHERS", "PROVIDENT FUND", " BANK ", " BANK,", "TRUST COMPANY",
    "TRUST CO", "CREDIT UNION", "BOARD", "AUTHORITY", "MINISTRY",
    "SOVEREIGN WEALTH", "MUTUAL AUTOMOBILE", "FEDERATION OF",
    "TJANSTEPENSION", "FORSIKRING", "PENSIONSKASSE", "BANCORP",
    "SAVINGS BANK", "FEDERAL RESERVE", "STATE STREET", "NORGES BANK",
]


def list_available_periods() -> list[str]:
    r = requests.get(LISTING_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    import re
    return re.findall(r'href="([^"]*form13f\.zip)"', r.text)


def _period_dir(window_slug: str) -> Path:
    d = CACHE_DIR / window_slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def _zip_member_path(zip_path: Path, filename: str) -> str:
    """Some periods' zips nest everything under a subfolder instead of the
    root (confirmed live: 01jun2025-31aug2025_form13f.zip contains
    "01JUN2025-31AUG2025_form13f/SUBMISSION.tsv", not "SUBMISSION.tsv" --
    extracting by the bare name silently failed with 'filename not
    matched'). Resolve the real internal path by listing the archive."""
    listing = subprocess.run(["unzip", "-Z1", str(zip_path)], check=True, capture_output=True, text=True).stdout
    for line in listing.splitlines():
        if line.split("/")[-1] == filename:
            return line
    raise FileNotFoundError(f"{filename} not found in {zip_path}")


def download_period(zip_href: str) -> Path:
    """zip_href like '/files/structureddata/data/form-13f-data-sets/2023q4_form13f.zip'."""
    window_slug = Path(zip_href).stem.replace("_form13f", "")
    pdir = _period_dir(window_slug)
    zip_path = pdir / "raw.zip"
    if not zip_path.exists():
        url = "https://www.sec.gov" + zip_href
        r = requests.get(url, headers=HEADERS, timeout=180)
        r.raise_for_status()
        zip_path.write_bytes(r.content)

    for name in ["SUBMISSION.tsv", "COVERPAGE.tsv", "SUMMARYPAGE.tsv"]:
        out = pdir / name
        if not out.exists():
            member = _zip_member_path(zip_path, name)
            # -j: junk paths, flatten straight into pdir regardless of internal nesting
            subprocess.run(["unzip", "-o", "-j", "-q", str(zip_path), member, "-d", str(pdir)], check=True)
    return pdir


def load_period_frames(pdir: Path) -> dict[str, pd.DataFrame]:
    sub = pd.read_csv(pdir / "SUBMISSION.tsv", sep="\t")
    cov = pd.read_csv(pdir / "COVERPAGE.tsv", sep="\t")
    summ = pd.read_csv(pdir / "SUMMARYPAGE.tsv", sep="\t")
    return {"submission": sub, "coverpage": cov, "summarypage": summ}


def dominant_report_period(sub: pd.DataFrame) -> str:
    hr = sub[sub["SUBMISSIONTYPE"] == "13F-HR"]
    return hr["PERIODOFREPORT"].value_counts().idxmax()


def select_top_funds(pdir: Path, n: int = 10) -> pd.DataFrame:
    frames = load_period_frames(pdir)
    sub, cov, summ = frames["submission"], frames["coverpage"], frames["summarypage"]
    period = dominant_report_period(sub)

    hr = sub[(sub["PERIODOFREPORT"] == period) & (sub["SUBMISSIONTYPE"] == "13F-HR")]
    merged = hr.merge(cov[["ACCESSION_NUMBER", "FILINGMANAGER_NAME"]], on="ACCESSION_NUMBER") \
               .merge(summ[["ACCESSION_NUMBER", "TABLEVALUETOTAL", "TABLEENTRYTOTAL"]], on="ACCESSION_NUMBER")

    name_upper = merged["FILINGMANAGER_NAME"].str.upper()
    is_known = name_upper.apply(lambda nm: any(k in nm for k in KNOWN_ACTIVE_FUNDS))
    concentrated = merged["TABLEENTRYTOTAL"] < MAX_POSITIONS_FOR_CONCENTRATED
    candidates = merged[is_known & concentrated].copy()
    # dedup: if a fund filed both an original and an amendment for the same
    # period, keep the one with the highest TABLEENTRYTOTAL (most complete)
    candidates = candidates.sort_values("TABLEENTRYTOTAL", ascending=False).drop_duplicates("CIK")
    candidates["report_period"] = period
    return candidates.sort_values("TABLEVALUETOTAL", ascending=False).head(n)


_CORP_SUFFIX_RE = re.compile(
    r"\s*\((CLASS [A-Z]|[A-Z])\)|\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|"
    r"LTD|LIMITED|LLC|LP|L P|PLC|THE|CLASS [A-Z]|HOLDINGS?)\b"
)


def _normalize_company_name(name: str) -> str:
    """Strips corporate suffixes/class designators so "NVIDIA CORP" and
    "NVIDIA" (or "Alphabet Inc." and "ALPHABET INC (CLASS A)") normalize to
    the same core string. Deliberately loose -- a false-positive exclusion
    (dropping a legitimate fund whose name happens to collide) is a much
    smaller problem for this use case than a false negative (letting a
    corporate treasury filing through as if it were a stock-picker)."""
    s = name.upper()
    s = re.sub(r"[.,/]", "", s)
    s = _CORP_SUFFIX_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


_sp500_name_cache: set[str] | None = None


def _sp500_company_names() -> set[str]:
    """Corporate self-filers (a company filing 13F for its own treasury,
    not a fund) confirmed live for Alphabet Inc. and NVIDIA CORP -- both
    ranked in the top 10 of a name-blind concentration+value sort. Cross-
    referencing against S&P 500 constituent names catches the common case
    cheaply; smaller/non-S&P500 corporate filers can still slip through."""
    global _sp500_name_cache
    if _sp500_name_cache is not None:
        return _sp500_name_cache
    try:
        headers = {"User-Agent": "wisdom-of-crowds-research contact@example.com"}
        r = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=headers, timeout=20)
        r.raise_for_status()
        df = pd.read_html(StringIO(r.text))[0]
        _sp500_name_cache = {_normalize_company_name(n) for n in df["Security"]}
    except Exception:
        _sp500_name_cache = set()
    return _sp500_name_cache


def select_top_funds_objective(pdir: Path, n: int, max_positions: int = MAX_POSITIONS_FOR_CONCENTRATED, min_positions: int = 5) -> pd.DataFrame:
    """Scalable version of select_top_funds with NO name curation -- ranks
    ALL 13F-HR filers by their own reported value, filtered to a plausible
    "concentrated active manager" position-count range and a best-effort
    keyword/company-name exclusion of insurers, pension funds, banks, and
    corporate treasury self-filers (see NON_FUND_KEYWORDS). Use this when N
    is too large for a hand-curated list to be practical; select_top_funds
    (curated) remains the more carefully vetted choice for small N.
    """
    frames = load_period_frames(pdir)
    sub, cov, summ = frames["submission"], frames["coverpage"], frames["summarypage"]
    period = dominant_report_period(sub)

    hr = sub[(sub["PERIODOFREPORT"] == period) & (sub["SUBMISSIONTYPE"] == "13F-HR")]
    merged = hr.merge(cov[["ACCESSION_NUMBER", "FILINGMANAGER_NAME"]], on="ACCESSION_NUMBER") \
               .merge(summ[["ACCESSION_NUMBER", "TABLEVALUETOTAL", "TABLEENTRYTOTAL"]], on="ACCESSION_NUMBER")

    name_upper = merged["FILINGMANAGER_NAME"].str.upper()
    is_known_fund = name_upper.apply(lambda nm: any(k in nm for k in KNOWN_ACTIVE_FUNDS))
    is_blocked_keyword = name_upper.apply(lambda nm: any(k in nm for k in NON_FUND_KEYWORDS))
    sp500_names = _sp500_company_names()
    # Berkshire Hathaway is both an S&P 500 company AND exactly the kind of
    # concentrated stock-picker this list wants -- the corporate-self-filer
    # check would wrongly exclude it (and any other curated fund that
    # happens to share a name with a public company), so it's exempted for
    # anything already vetted in KNOWN_ACTIVE_FUNDS.
    is_corporate_selffiler = merged["FILINGMANAGER_NAME"].apply(lambda nm: _normalize_company_name(nm) in sp500_names) & ~is_known_fund
    concentrated = (merged["TABLEENTRYTOTAL"] < max_positions) & (merged["TABLEENTRYTOTAL"] >= min_positions)

    candidates = merged[concentrated & ~is_blocked_keyword & ~is_corporate_selffiler].copy()
    candidates = candidates.sort_values("TABLEENTRYTOTAL", ascending=False).drop_duplicates("CIK")
    candidates["report_period"] = period
    return candidates.sort_values("TABLEVALUETOTAL", ascending=False).head(n)


def extract_holdings(pdir: Path, accession_numbers: list[str]) -> pd.DataFrame:
    """Streams INFOTABLE.tsv out of the zip and greps for just the accession
    numbers we need, instead of extracting/loading the full ~400MB file."""
    acc_file = pdir / "_target_accessions.txt"
    acc_file.write_text("\n".join(accession_numbers))

    zip_path = pdir / "raw.zip"
    member = _zip_member_path(zip_path, "INFOTABLE.tsv")
    unzip_proc = subprocess.Popen(["unzip", "-p", str(zip_path), member], stdout=subprocess.PIPE)
    grep_proc = subprocess.run(["grep", "-F", "-f", str(acc_file)], stdin=unzip_proc.stdout, capture_output=True, text=True)
    unzip_proc.wait()

    cols = ["ACCESSION_NUMBER", "INFOTABLE_SK", "NAMEOFISSUER", "TITLEOFCLASS", "CUSIP", "FIGI",
            "VALUE", "SSHPRNAMT", "SSHPRNAMTTYPE", "PUTCALL", "INVESTMENTDISCRETION",
            "OTHERMANAGER", "VOTING_AUTH_SOLE", "VOTING_AUTH_SHARED", "VOTING_AUTH_NONE"]
    df = pd.read_csv(StringIO(grep_proc.stdout), sep="\t", names=cols, header=None)
    return df[df["PUTCALL"].isna()].copy()  # common stock only -- see module docstring


def _load_cusip_cache() -> dict:
    if FIGI_CACHE_PATH.exists():
        return json.loads(FIGI_CACHE_PATH.read_text())
    return {}


def _save_cusip_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    FIGI_CACHE_PATH.write_text(json.dumps(cache, indent=2))


_VALID_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,6}$")


def resolve_cusips_to_tickers(cusips: list[str]) -> dict[str, str | None]:
    """OpenFIGI's free, unauthenticated mapping endpoint (no API key needed;
    confirmed live: capped at 10 jobs per request unauthenticated -- a 100-item
    batch gets a flat 413 rejection, not a partial result, and without this
    fix a single oversized batch silently poisoned the whole cache with false
    "no ticker" nulls). ~25 requests/6s rate limit. Cached to disk since
    CUSIPs are stable identifiers reused across every quarter this is run
    for; transient failures are retried, never cached as a negative result."""
    cache = _load_cusip_cache()
    todo = [c for c in set(cusips) if c not in cache]
    BATCH = 10
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        jobs = [{"idType": "ID_CUSIP", "idValue": c} for c in batch]
        resp = requests.post("https://api.openfigi.com/v3/mapping", json=jobs, timeout=30)
        if resp.status_code != 200:
            time.sleep(3)  # transient (likely rate-limit) failure -- leave unresolved for a later run, don't poison the cache
            continue
        results = resp.json()
        for c, res in zip(batch, results):
            data = res.get("data")
            if not data:
                cache[c] = None
                continue
            us = [d for d in data if d.get("exchCode") == "US"] or data
            ticker = us[0].get("ticker")
            # 13F covers some bond/debt positions too (PUTCALL isna alone
            # doesn't exclude them); OpenFIGI happily "resolves" a bond
            # CUSIP to its description (e.g. "AAL 6.5 07/01/25" -- a real
            # American Airlines bond), which isn't a fetchable stock ticker
            # and, worse, contains "/" -- confirmed live to crash the price
            # cache by being used as a file path. Reject anything that
            # doesn't look like a plain equity ticker.
            cache[c] = ticker if ticker and _VALID_TICKER_RE.match(ticker) else None
        if i + BATCH < len(todo):
            time.sleep(0.3)
    _save_cusip_cache(cache)
    return {c: cache.get(c) for c in cusips}


@dataclass
class ConsensusHolding:
    cusip: str
    name: str
    ticker: str | None
    n_funds: int
    total_value: float
    weight: float
    fund_names: list[str]


def build_consensus_portfolio(holdings: pd.DataFrame, fund_names: dict, top_n: int = 25) -> list[ConsensusHolding]:
    holdings = holdings.copy()
    holdings["fund"] = holdings["ACCESSION_NUMBER"].map(fund_names)
    grouped = holdings.groupby("CUSIP").agg(
        name=("NAMEOFISSUER", "first"),
        total_value=("VALUE", "sum"),
        n_funds=("fund", "nunique"),
        fund_names=("fund", lambda s: sorted(set(s))),
    ).reset_index()
    grouped = grouped.sort_values("total_value", ascending=False).head(top_n)
    total = grouped["total_value"].sum()

    cusips = grouped["CUSIP"].tolist()
    tickers = resolve_cusips_to_tickers(cusips)

    out = []
    for _, row in grouped.iterrows():
        out.append(ConsensusHolding(
            cusip=row["CUSIP"], name=row["name"], ticker=tickers.get(row["CUSIP"]),
            n_funds=int(row["n_funds"]), total_value=float(row["total_value"]),
            weight=float(row["total_value"] / total), fund_names=row["fund_names"],
        ))
    return out


def classify_position_changes(prev_holdings: pd.DataFrame, curr_holdings: pd.DataFrame, cik_prev: dict, cik_curr: dict, display_names: dict) -> pd.DataFrame:
    """Per (fund, CUSIP): NEW / CLOSED / INCREASED / DECREASED / UNCHANGED.

    Joins on CIK, not the filing-manager name string -- confirmed live that
    the same real fund can change its legal filing name between quarters
    (e.g. "Pershing Square Capital Management, L.P." one quarter, "PERSHING
    SQUARE INC." the next); joining on name would misclassify every one of
    that fund's unchanged positions as closed-and-reopened. `cik_prev`/
    `cik_curr` map ACCESSION_NUMBER -> CIK; `display_names` maps CIK -> a
    human-readable name for the report.
    """
    prev = prev_holdings.copy()
    prev["cik"] = prev["ACCESSION_NUMBER"].map(cik_prev)
    curr = curr_holdings.copy()
    curr["cik"] = curr["ACCESSION_NUMBER"].map(cik_curr)

    prev_g = prev.groupby(["cik", "CUSIP"]).agg(name=("NAMEOFISSUER", "first"), value=("VALUE", "sum")).reset_index()
    curr_g = curr.groupby(["cik", "CUSIP"]).agg(name=("NAMEOFISSUER", "first"), value=("VALUE", "sum")).reset_index()

    merged = curr_g.merge(prev_g, on=["cik", "CUSIP"], how="outer", suffixes=("_curr", "_prev"))
    merged["name"] = merged["name_curr"].fillna(merged["name_prev"])
    merged["fund"] = merged["cik"].map(display_names)
    merged["value_curr"] = merged["value_curr"].fillna(0)
    merged["value_prev"] = merged["value_prev"].fillna(0)

    def classify(row):
        if row["value_prev"] == 0:
            return "NEW"
        if row["value_curr"] == 0:
            return "CLOSED"
        pct_change = (row["value_curr"] - row["value_prev"]) / row["value_prev"]
        if pct_change > 0.10:
            return "INCREASED"
        if pct_change < -0.10:
            return "DECREASED"
        return "UNCHANGED"

    merged["change"] = merged.apply(classify, axis=1)
    return merged
