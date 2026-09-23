"""Offline tests for the added crowds: parsing of each venue's payload shape
(taken from live responses on 2026-09-23) and the maths that turns them
into forecasts. No network access."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from adapters.deribit_options import chain_to_smiles as deribit_smiles, parse_instrument
from adapters.derive_options import expiries_from_instruments, tickers_to_smile
from adapters.futuur import FutuurAdapter, parse_range_label, parse_touch_label
from adapters.limitless import LimitlessAdapter, parse_arrow_label
from adapters.okx_options import parse_inst_id
from adapters.options_common import SmilePoint, black76_call, build_options_distribution, smile_to_buckets
from aggregation import build_dashboard
from common.distribution import PriceBucket, PriceDistribution, SourceFetchResult
from crowds import build_horizon_views
from signals.cot import percentile_rank, summarize
from signals.eia import parse_steo
from signals.futures import annualized_basis, build_curve, forward_at
from signals.sentiment import tally_stocktwits

NOW = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- options ---

def _flat_smile(forward: float, iv: float, n: int = 41, width: float = 0.6) -> list[SmilePoint]:
    lo, hi = forward * (1 - width), forward * (1 + width)
    return [SmilePoint(strike=lo + i * (hi - lo) / (n - 1), iv=iv, open_interest_usd=1e6) for i in range(n)]


def _bucket_moments(buckets: list[PriceBucket]) -> tuple[float, float]:
    """Mean and median using closed-bucket midpoints (tails at their edge)."""
    mids = [((b.low if b.low is not None else b.high) + (b.high if b.high is not None else b.low)) / 2 for b in buckets]
    mean = sum(m * b.prob for m, b in zip(mids, buckets))
    cum = 0.0
    for b in buckets:
        if cum + b.prob >= 0.5 and b.low is not None and b.high is not None:
            return mean, b.low + (0.5 - cum) / b.prob * (b.high - b.low)
        cum += b.prob
    return mean, float("nan")


def test_black76_put_call_limits():
    assert black76_call(100, 50, 0.5, 0.0) == 50
    assert black76_call(100, 150, 0.0, 1.0) == 0
    # ATM call ~ 0.4 * F * sigma * sqrt(T) for small sigma
    assert black76_call(100, 100, 0.2, 1.0) == pytest.approx(0.4 * 100 * 0.2, rel=0.01)


def test_flat_smile_recovers_lognormal():
    forward, iv, t = 86_000.0, 0.45, 30 / 365.25
    buckets = smile_to_buckets(forward, t, _flat_smile(forward, iv))
    assert buckets is not None
    assert sum(b.prob for b in buckets) == pytest.approx(1.0, abs=1e-9)
    assert all(b.prob >= -1e-12 for b in buckets)
    mean, median = _bucket_moments(buckets)
    # Risk-neutral mean equals the forward; the lognormal median sits
    # below it by exp(-sigma^2 T / 2).
    assert mean == pytest.approx(forward, rel=0.01)
    assert median == pytest.approx(forward * math.exp(-0.5 * iv * iv * t), rel=0.01)


def test_noisy_smile_is_forced_monotone():
    forward = 100.0
    smile = _flat_smile(forward, 0.5)
    smile[20].iv = 0.9  # a bad mark at the money
    buckets = smile_to_buckets(forward, 0.25, smile)
    assert buckets is not None
    assert all(b.prob >= 0 for b in buckets)
    assert sum(b.prob for b in buckets) == pytest.approx(1.0)


def test_thin_chain_rejected():
    assert smile_to_buckets(100.0, 0.1, _flat_smile(100.0, 0.5, n=4)) is None


def test_near_expiry_dropped():
    d = build_options_distribution(
        asset="BTC", source_name="x", expiry=NOW + timedelta(hours=2), forward=100.0,
        smile=_flat_smile(100.0, 0.5), source_url="", venue_note="", now=NOW,
    )
    assert d is None


def test_deribit_instrument_parsing_and_grouping():
    assert parse_instrument("BTC-9OCT26-90000-C") == (datetime(2026, 10, 9, 8, tzinfo=timezone.utc), 90000.0)
    assert parse_instrument("BTC-PERPETUAL") is None
    rows = [
        {"instrument_name": "BTC-24SEP26-90000-C", "mark_iv": 48.19, "underlying_price": 85998.88, "open_interest": 205.4, "volume_usd": 10995.63},
        {"instrument_name": "BTC-24SEP26-80000-P", "mark_iv": 55.0, "underlying_price": 85998.88, "open_interest": 10, "volume_usd": 0},
        {"instrument_name": "BTC-24SEP26-85000-P", "mark_iv": None, "underlying_price": 85998.88},
    ]
    smiles = deribit_smiles(rows)
    fwd, smile = smiles[datetime(2026, 9, 24, 8, tzinfo=timezone.utc)]
    assert fwd == 85998.88
    assert [p.strike for p in smile] == [90000.0, 80000.0]
    assert smile[0].iv == pytest.approx(0.4819)
    assert smile[0].open_interest_usd == pytest.approx(205.4 * 85998.88)


def test_okx_ignores_usdt_margined_twin():
    assert parse_inst_id("BTC-USD-261030-90000-C") == (datetime(2026, 10, 30, 8, tzinfo=timezone.utc), 90000.0)
    assert parse_inst_id("BTC-USD_UM-260925-85000-P") is None


def test_derive_parsing():
    inst = [
        {"instrument_name": "BTC-20261030-180000-C", "is_active": True, "option_details": {"expiry": 1793347200}},
        {"instrument_name": "BTC-20261030-50000-P", "is_active": False, "option_details": {"expiry": 1793347200}},
    ]
    assert expiries_from_instruments(inst) == {"20261030": datetime(2026, 10, 30, 8, tzinfo=timezone.utc)}
    tickers = {
        "BTC-20261030-120000-P": {"option_pricing": {"i": "0.46896", "f": "86379"}, "stats": {"oi": "0", "v": "0"}},
        "BTC-20261030-55000-P": {"option_pricing": {"i": "0.61", "f": "86379"}, "stats": {"oi": "445.166", "v": "1"}},
    }
    fwd, smile = tickers_to_smile(tickers)
    assert fwd == 86379
    assert sorted(p.strike for p in smile) == [55000.0, 120000.0]
    assert max(p.open_interest_usd for p in smile) == pytest.approx(445.166 * 86379)


# ------------------------------------------------------ prediction markets ---

def test_futuur_labels():
    assert parse_range_label("Below $ 60,000") == (None, 60000.0)
    assert parse_range_label("Between $ 75,000 and $ 89,999.99") == (75000.0, 89999.99)
    assert parse_range_label("$ 105,000 or higher") == (105000.0, None)
    assert parse_range_label("Will it rain?") is None
    assert parse_touch_label("Reach $87,500", None) == ("above", 87500.0)
    assert parse_touch_label("Dip to $4,200", None) == ("below", 4200.0)
    assert parse_touch_label("Hit $5,000", None) == ("above", 5000.0)
    # bare numbers need the current price to know the direction
    assert parse_touch_label("55,000", 86000.0) == ("below", 55000.0)
    assert parse_touch_label("140,000", 86000.0) == ("above", 140000.0)
    assert parse_touch_label("55,000", None) is None


def test_futuur_uses_real_money_book():
    market = {
        "id": 239444, "slug": "btc-end-2026", "bet_end_date": "2026-12-31T00:00:00Z",
        "volume_real_money": 4270.9, "liquidity_real_money": 11859.8,
        "outcomes": [
            {"title": "Between $ 75,000 and $ 89,999.99", "price": {"OOM": 0.28, "USDC": 0.5}},
            {"title": "Below $ 60,000", "price": {"OOM": 0.25, "USDC": 0.06}},
            {"title": "Between $ 60,000 and $ 74,999.99", "price": {"OOM": 0.24, "USDC": 0.28}},
            {"title": "$ 105,000 or higher", "price": {"OOM": 0.11, "USDC": 0.04}},
            {"title": "Between $ 90,000 and $ 104,999.99", "price": {"OOM": 0.1, "USDC": 0.12}},
        ],
    }
    d = FutuurAdapter().market_to_distribution("BTC", market)
    assert d is not None and not d.is_play_money
    assert d.weight == pytest.approx(4270.9)
    assert sorted(b.prob for b in d.buckets) == sorted([0.5, 0.06, 0.28, 0.04, 0.12])


def test_limitless_touch_group():
    assert parse_arrow_label("↑ 88,000") == ("above", 88000.0)
    assert parse_arrow_label("↓ 68,000") == ("below", 68000.0)
    assert parse_arrow_label("September 30, 2026") is None
    group = {
        "title": "What price will Bitcoin hit September 21-27?", "slug": "x", "expirationTimestamp": 1790568000000,
        "volumeFormatted": "35.1",
        "markets": [
            {"title": "↑ 88,000", "prices": [0.53, 0.47], "volumeFormatted": "11.75"},
            {"title": "↓ 80,000", "prices": [0.088, 0.912], "volumeFormatted": "6.7"},
            {"title": "junk", "prices": [0.5, 0.5]},
        ],
    }
    t = LimitlessAdapter().group_to_touch("BTC", group)
    assert [(x.direction, x.price, x.prob_touch) for x in t.thresholds] == [("above", 88000.0, 0.53), ("below", 80000.0, 0.088)]
    assert "arbitraged against Polymarket" in t.raw_note


def test_crowds_are_never_mixed():
    def dist(src_type: str, name: str, lo: float, weight: float) -> PriceDistribution:
        return PriceDistribution(
            asset="BTC", source_type=src_type, source_name=name, target_date=NOW.date() + timedelta(days=30),
            period_label="x", buckets=[PriceBucket(None, lo, 0.2), PriceBucket(lo, lo + 1000, 0.6), PriceBucket(lo + 1000, None, 0.2)],
            weight=weight, volume=weight,
        )
    results = [
        SourceFetchResult("polymarket", "prediction_market", [dist("prediction_market", "polymarket", 80000, 1e4)]),
        SourceFetchResult("deribit_options", "options", [dist("options", "deribit_options", 90000, 1e9)]),
    ]
    pm, _ = build_dashboard(results)
    opt, _ = build_dashboard(results, source_type="options")
    assert [s.source_name for s in pm[0].sources] == ["polymarket"]
    assert [s.source_name for s in opt[0].sources] == ["deribit_options"]
    assert pm[0].median < 81000 < 90000 < opt[0].median
    # Polymarket-measured calibration corrections only touch prediction markets
    assert opt[0].longshot_shrink_applied == 1.0 and opt[0].ci_width_mult_applied == 1.0


# ---------------------------------------------------------------- signals ---

def test_futures_curve_and_forward_interpolation():
    futures = [
        {"venue": "deribit", "expiry": "2026-12-25T08:00:00+00:00", "price": 87000.0, "open_interest_usd": 3e8},
        {"venue": "okx", "expiry": "2026-12-25T08:00:00+00:00", "price": 87100.0, "open_interest_usd": 1e8},
        {"venue": "deribit", "expiry": "2027-03-26T08:00:00+00:00", "price": 88000.0, "open_interest_usd": 8e7},
    ]
    curve = build_curve(futures, 86000.0, NOW)
    assert len(curve) == 2
    assert curve[0]["price"] == pytest.approx(87025.0)  # OI-weighted
    assert curve[0]["venues"] == ["deribit", "okx"]
    assert curve[0]["annualized_basis"] == pytest.approx(math.log(87025 / 86000) / curve[0]["t_years"])
    mid = forward_at(curve, 86000.0, NOW + timedelta(days=45), NOW)
    assert 86000 < mid < 87025
    assert forward_at(curve, 86000.0, NOW + timedelta(days=400), NOW) is None  # no extrapolation
    assert annualized_basis(100, 100, 0.0001) is None


def test_eia_splits_history_and_forecast():
    rows = [
        {"seriesId": "WTIPUUS", "period": "2026-08", "value": "83.9"},
        {"seriesId": "WTIPUUS", "period": "2026-09", "value": "88"},
        {"seriesId": "BREPUUS", "period": "2026-09", "value": "92"},
        {"seriesId": "WTIPUUS", "period": "2027-12", "value": "58"},
        {"seriesId": "OTHER", "period": "2026-09", "value": "1"},
    ]
    out = parse_steo(rows, NOW)
    assert [(p["period"], p["is_forecast"]) for p in out["WTI"]] == [("2026-08", False), ("2026-09", True), ("2027-12", True)]
    assert out["WTI"][1]["date"] == "2026-09-15"
    assert out["Brent"][0]["price"] == 92.0


def test_cot_summary_and_percentile():
    rows = [
        {"report_date_as_yyyy_mm_dd": f"2026-09-{15 - i:02d}T00:00:00.000", "open_interest_all": "1000",
         "m_money_positions_long_all": str(500 - i * 50), "m_money_positions_short_all": "100",
         "prod_merc_positions_long": "100", "prod_merc_positions_short": "300"}
        for i in range(5)
    ]
    groups = summarize(rows, "72hh-3qpy")
    mm = groups[0]
    assert mm["group"] == "Managed money"
    assert mm["net_contracts"] == 400
    assert mm["weekly_change_contracts"] == 50
    assert mm["percentile_3y"] == 1.0  # the latest week is the most long in the window
    assert mm["history"][-1]["date"] == "2026-09-15"
    assert percentile_rank([1, 2, 3, 4], 2) == 0.5


def test_stocktwits_tally():
    msgs = [
        {"created_at": "2026-09-23T08:00:00Z", "entities": {"sentiment": {"basic": "Bullish"}}},
        {"created_at": "2026-09-23T07:00:00Z", "entities": {"sentiment": {"basic": "Bearish"}}},
        {"created_at": "2026-09-23T06:00:00Z", "entities": {"sentiment": None}},
        {"created_at": "2026-09-23T05:00:00Z", "entities": {"sentiment": {"basic": "Bullish"}}},
    ]
    t = tally_stocktwits(msgs)
    assert (t["bullish"], t["bearish"], t["untagged"]) == (2, 1, 1)
    assert t["bullish_share"] == pytest.approx(2 / 3)
    assert t["oldest_post"] == "2026-09-23T05:00:00Z"


def test_horizon_views_only_use_nearby_points():
    pm = [{"lead_hours": 6 * 24, "median": 87000.0, "ci_68": [85000.0, 89000.0], "target_date": "2026-09-29"}]
    options = [{"lead_hours": 200 * 24, "median": 90000.0, "ci_68": [80000.0, 100000.0], "target_date": "2027-04-11"}]
    curve = [{"t_years": 0.5, "price": 88000.0}]
    eia = [{"period": "2026-10", "date": "2026-10-15", "price": 87.0, "is_forecast": True}]
    views = {v["horizon_days"]: v["crowds"] for v in build_horizon_views(86000.0, pm, options, curve, eia, NOW)}
    assert views[7]["prediction_markets"]["median"] == 87000.0
    assert "prediction_markets" not in views[30]  # 6 days is not "about a month"
    assert "options" not in views[90] and "options" not in views[365]  # 200 days is neither
    assert views[30]["futures"]["change_vs_spot"] > 0
    assert "futures" not in views[365]  # beyond the curve: no extrapolation
    assert views[30]["experts"]["median"] == 87.0
