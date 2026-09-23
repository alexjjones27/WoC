"""Futures and perpetual-swap crowd: Deribit, OKX and Hyperliquid.

This replaces the old adapters/perp_futures.py design stub, following its
own conclusion: futures give a POINT forward price per expiry, and perp
funding is a SENTIMENT reading, so neither is forced into a
PriceDistribution. Instead:

  1. DATED FUTURES -> a forward curve: (expiry, price, open interest) per
     contract, plus the annualized basis vs. the current price
     (contango = traders pay up for later delivery). Deribit and OKX list
     BTC/ETH futures out to ~1 year (confirmed live 2026-09-23).
     For any date, `forward_at()` interpolates the curve in log-price, so
     the Crowds page can put a futures number next to each
     prediction-market date.

     A forward price is NOT a pure forecast: it also carries the cost of
     carry (interest rates, and for oil, storage and supply tightness).
     For crypto the carry is mostly the dollar funding rate, so a forward
     a few % above spot per year is "normal", not a bullish call. That's
     why the basis is shown annualized, next to the curve.

  2. PERPETUALS -> funding rate, annualized. Positive = longs pay shorts
     to hold their position = leveraged traders are net long. OKX lists
     gold (XAU) and WTI (CL) perps, and Hyperliquid's "xyz" venue lists
     GOLD, CL and BRENTOIL, so this is the only futures-style crowd
     covering all four assets.

Open interest is kept in USD everywhere so venues can be compared.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from adapters.http import get_json, post_json

DERIBIT_BASE = "https://www.deribit.com/api/v2/public"
OKX_BASE = "https://www.okx.com/api/v5"
HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"
EXPIRY_HOUR_UTC = 8  # Deribit and OKX dated futures both settle at 08:00 UTC

DERIBIT_CURRENCY = {"BTC": "BTC", "ETH": "ETH"}
OKX_FUTURES_FAMILY = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
OKX_SWAP = {"BTC": "BTC-USD-SWAP", "ETH": "ETH-USD-SWAP", "GOLD": "XAU-USDT-SWAP", "OIL": "CL-USDT-SWAP"}
# (dex, coin): "" is Hyperliquid's main venue, "xyz" its commodities venue.
HYPERLIQUID_PERP = {"BTC": ("", "BTC"), "ETH": ("", "ETH"), "GOLD": ("xyz", "xyz:GOLD"), "OIL": ("xyz", "xyz:CL")}

HOURS_PER_YEAR = 24 * 365.0


def _years_until(expiry: datetime, now: datetime) -> float:
    return max((expiry - now).total_seconds(), 0.0) / (3600 * HOURS_PER_YEAR)


def annualized_basis(price: float, spot: float, t_years: float) -> float | None:
    if spot <= 0 or price <= 0 or t_years < 1 / 365:
        return None
    return math.log(price / spot) / t_years


def _deribit(asset: str) -> tuple[list[dict], list[dict]]:
    cur = DERIBIT_CURRENCY.get(asset)
    if cur is None:
        return [], []
    rows = get_json(f"{DERIBIT_BASE}/get_book_summary_by_currency", {"currency": cur, "kind": "future"})["result"]
    futures, perps = [], []
    for r in rows:
        name = r["instrument_name"]
        if name.endswith("PERPETUAL"):
            perps.append({
                "venue": "deribit",
                "instrument": name,
                "price": float(r["mark_price"]),
                "funding_annualized": float(r.get("funding_8h") or 0.0) * 3 * 365,
                "open_interest_usd": float(r.get("open_interest") or 0.0),
            })
            continue
        try:
            expiry = datetime.strptime(name.split("-")[1], "%d%b%y").replace(hour=EXPIRY_HOUR_UTC, tzinfo=timezone.utc)
        except (IndexError, ValueError):
            continue
        futures.append({
            "venue": "deribit",
            "instrument": name,
            "expiry": expiry.isoformat(),
            "price": float(r["mark_price"]),
            # Deribit's inverse futures quote open interest in USD already.
            "open_interest_usd": float(r.get("open_interest") or 0.0),
        })
    return futures, perps


def _okx_futures(asset: str) -> list[dict]:
    fam = OKX_FUTURES_FAMILY.get(asset)
    if fam is None:
        return []
    tickers = get_json(f"{OKX_BASE}/market/tickers", {"instType": "FUTURES", "instFamily": fam})["data"]
    oi = {r["instId"]: float(r.get("oiUsd") or 0.0) for r in get_json(
        f"{OKX_BASE}/public/open-interest", {"instType": "FUTURES", "instFamily": fam})["data"]}
    out = []
    for t in tickers:
        try:
            expiry = datetime.strptime(t["instId"].split("-")[2], "%y%m%d").replace(hour=EXPIRY_HOUR_UTC, tzinfo=timezone.utc)
            bid, ask = float(t["bidPx"]), float(t["askPx"])
        except (IndexError, KeyError, ValueError):
            continue
        out.append({
            "venue": "okx",
            "instrument": t["instId"],
            "expiry": expiry.isoformat(),
            "price": (bid + ask) / 2 if bid > 0 and ask > 0 else float(t["last"]),
            "open_interest_usd": oi.get(t["instId"], 0.0),
        })
    return out


def _okx_perp(asset: str) -> dict | None:
    inst = OKX_SWAP.get(asset)
    if inst is None:
        return None
    ticker = get_json(f"{OKX_BASE}/market/ticker", {"instId": inst})["data"][0]
    fr = get_json(f"{OKX_BASE}/public/funding-rate", {"instId": inst})["data"][0]
    oi = get_json(f"{OKX_BASE}/public/open-interest", {"instType": "SWAP", "instId": inst})["data"][0]
    # Funding intervals differ by contract (8h for most, shorter for some
    # commodities), so derive it from the venue's own schedule.
    interval_h = (int(fr["nextFundingTime"]) - int(fr["fundingTime"])) / 3_600_000 or 8.0
    return {
        "venue": "okx",
        "instrument": inst,
        "price": float(ticker["last"]),
        "funding_annualized": float(fr["fundingRate"]) * HOURS_PER_YEAR / interval_h,
        "open_interest_usd": float(oi.get("oiUsd") or 0.0),
    }


def _hyperliquid_perp(asset: str) -> dict | None:
    spec = HYPERLIQUID_PERP.get(asset)
    if spec is None:
        return None
    dex, coin = spec
    body = {"type": "metaAndAssetCtxs", "dex": dex} if dex else {"type": "metaAndAssetCtxs"}
    meta, ctxs = post_json(HYPERLIQUID_INFO, body)
    for u, c in zip(meta["universe"], ctxs):
        if u["name"] == coin:
            price = float(c["markPx"])
            return {
                "venue": "hyperliquid",
                "instrument": coin,
                "price": price,
                "funding_annualized": float(c["funding"]) * HOURS_PER_YEAR,  # hourly funding
                "open_interest_usd": float(c["openInterest"]) * price,  # OI is in coins
            }
    return None


def forward_at(curve: list[dict], spot: float, when: datetime, now: datetime | None = None) -> float | None:
    """Futures-implied forward for an arbitrary date: log-linear
    interpolation between the OI-weighted curve points bracketing it, with
    (now, spot) as the curve's start. None beyond the last listed expiry
    -- no extrapolation."""
    now = now or datetime.now(timezone.utc)
    pts = [(0.0, spot)] + [(p["t_years"], p["price"]) for p in curve]
    t = _years_until(when, now)
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        if t0 <= t <= t1 and t1 > t0:
            frac = (t - t0) / (t1 - t0)
            return math.exp(math.log(p0) + frac * (math.log(p1) - math.log(p0)))
    return None


def build_curve(futures: list[dict], spot: float | None, now: datetime) -> list[dict]:
    """Merge venues into one curve: one point per expiry, price = open-
    interest-weighted average across venues listing that expiry."""
    by_expiry: dict[str, list[dict]] = {}
    for f in futures:
        by_expiry.setdefault(f["expiry"], []).append(f)
    curve = []
    for expiry_iso, rows in sorted(by_expiry.items()):
        expiry = datetime.fromisoformat(expiry_iso)
        t = _years_until(expiry, now)
        if t <= 0:
            continue
        w = [max(r["open_interest_usd"], 1.0) for r in rows]
        price = sum(r["price"] * wi for r, wi in zip(rows, w)) / sum(w)
        curve.append({
            "expiry": expiry_iso,
            "t_years": t,
            "price": price,
            "open_interest_usd": sum(r["open_interest_usd"] for r in rows),
            "venues": sorted({r["venue"] for r in rows}),
            "annualized_basis": annualized_basis(price, spot, t) if spot else None,
        })
    return curve


def fetch_futures_crowd(asset: str, spot: float | None) -> dict:
    now = datetime.now(timezone.utc)
    errors: list[str] = []
    futures: list[dict] = []
    perps: list[dict] = []

    for label, fn in (("deribit", _deribit), ("okx futures", _okx_futures)):
        try:
            res = fn(asset)
            if isinstance(res, tuple):
                futures += res[0]
                perps += res[1]
            else:
                futures += res
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
    for label, fn in (("okx perp", _okx_perp), ("hyperliquid", _hyperliquid_perp)):
        try:
            p = fn(asset)
            if p:
                perps.append(p)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")

    total_perp_oi = sum(p["open_interest_usd"] for p in perps)
    oi_weighted_funding = (
        sum(p["funding_annualized"] * p["open_interest_usd"] for p in perps) / total_perp_oi if total_perp_oi > 0 else None
    )
    return {
        "curve": build_curve(futures, spot, now),
        "contracts": sorted(futures, key=lambda f: (f["expiry"], f["venue"])),
        "perps": perps,
        "funding_annualized_oi_weighted": oi_weighted_funding,
        "errors": errors,
    }
