"""Positioning crowd: the CFTC's weekly Commitments of Traders report.

Every Friday the CFTC publishes (as of the previous Tuesday) how the
largest futures traders are positioned, split by trader type. It's the one
free source here of what large money is actually holding, rather than
saying or betting. Contracts, confirmed live 2026-09-23 on the CFTC's
public Socrata API (no key):

  GOLD  088691  COMEX gold                 Disaggregated report (72hh-3qpy)
  OIL   067651  NYMEX WTI (physical)       Disaggregated report
  BTC   133741  CME Bitcoin                Traders in Financial Futures (gpe5-46if)
  ETH   146021  CME Ether                  Traders in Financial Futures

Groups shown:
  - Commodities: "managed money" (hedge funds/CTAs, the speculative crowd)
    and "producer/merchant" (miners, refiners, hedgers -- usually the
    other side).
  - Crypto: "leveraged funds" and "asset managers".

READ WITH CARE for crypto: leveraged funds at CME have been structurally
net short Bitcoin since spot ETFs launched, because the popular basis
trade is "long the ETF, short CME futures" -- that short is a hedge, not a
bearish view. For that reason every group's position is also ranked
against its own last three years (percentile of net-as-%-of-open-
interest): "more short than usual" means something even when "short" by
itself doesn't.
"""
from __future__ import annotations

from adapters.http import get_json
from cache import TTLCache

CFTC_BASE = "https://publicreporting.cftc.gov/resource"
DISAGGREGATED = "72hh-3qpy"
TFF = "gpe5-46if"
HISTORY_WEEKS = 156

CONTRACTS = {
    "GOLD": {"code": "088691", "dataset": DISAGGREGATED, "label": "COMEX gold futures"},
    "OIL": {"code": "067651", "dataset": DISAGGREGATED, "label": "NYMEX WTI crude futures"},
    "BTC": {"code": "133741", "dataset": TFF, "label": "CME Bitcoin futures"},
    "ETH": {"code": "146021", "dataset": TFF, "label": "CME Ether futures"},
}

# (display name, long column, short column) per report type.
GROUPS = {
    DISAGGREGATED: [
        ("Managed money", "m_money_positions_long_all", "m_money_positions_short_all"),
        ("Producers & merchants", "prod_merc_positions_long", "prod_merc_positions_short"),
    ],
    TFF: [
        ("Leveraged funds", "lev_money_positions_long", "lev_money_positions_short"),
        ("Asset managers", "asset_mgr_positions_long", "asset_mgr_positions_short"),
    ],
}

_cache = TTLCache(ttl_seconds=6 * 3600)


def percentile_rank(values: list[float], x: float) -> float:
    """Share of `values` at or below x, 0-1."""
    if not values:
        return 0.5
    return sum(1 for v in values if v <= x) / len(values)


def summarize(rows: list[dict], dataset: str) -> list[dict]:
    """rows newest first. One summary per trader group."""
    out = []
    for name, long_col, short_col in GROUPS[dataset]:
        series = []
        for r in rows:
            try:
                oi = float(r["open_interest_all"])
                net = float(r[long_col]) - float(r[short_col])
            except (KeyError, TypeError, ValueError):
                continue
            if oi > 0:
                series.append({"date": r["report_date_as_yyyy_mm_dd"][:10], "net": net, "net_pct_oi": net / oi,
                               "long": float(r[long_col]), "short": float(r[short_col])})
        if not series:
            continue
        latest = series[0]
        prev = series[1] if len(series) > 1 else None
        hist = [s["net_pct_oi"] for s in series]
        out.append({
            "group": name,
            "long_contracts": latest["long"],
            "short_contracts": latest["short"],
            "net_contracts": latest["net"],
            "net_pct_open_interest": latest["net_pct_oi"],
            "weekly_change_contracts": latest["net"] - prev["net"] if prev else None,
            "percentile_3y": percentile_rank(hist, latest["net_pct_oi"]),
            "history": [{"date": s["date"], "net_pct_oi": s["net_pct_oi"]} for s in reversed(series)],
        })
    return out


def fetch_positioning(asset: str) -> dict:
    spec = CONTRACTS.get(asset)
    if spec is None:
        return {"error": f"no CFTC contract mapped for {asset}"}
    cached = _cache.get(asset)
    if cached is not None:
        return cached
    try:
        rows = get_json(
            f"{CFTC_BASE}/{spec['dataset']}.json",
            {
                "$where": f"cftc_contract_market_code='{spec['code']}'",
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": HISTORY_WEEKS,
            },
            timeout=25.0,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"CFTC fetch failed: {exc}"}
    if not rows:
        return {"error": "CFTC returned no rows"}
    result = {
        "contract": spec["label"],
        "report_date": rows[0]["report_date_as_yyyy_mm_dd"][:10],
        "open_interest": float(rows[0]["open_interest_all"]),
        "groups": summarize(rows, spec["dataset"]),
        "source_url": "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
    }
    _cache.set(asset, result)
    return result
