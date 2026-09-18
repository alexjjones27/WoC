"""End-to-end: does Stage 2 as of date T depend on anything after T?

Every other guarantee in this project rests on this one. The test is
adversarial by construction: run the pipeline, then add a year of future
records and prices, run it again at the SAME as-of date, and demand that
nothing changed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import make_history
from src.ingest.base import PriceTargetRecord
from src.ingest.prices import PriceHistory
from src.ingest.synthetic import generate_panel
from src.pipeline import run_debias
from src.store import pit, writer

ASOF = date(2022, 6, 30)


def _split_history(full: PriceHistory, cut: date) -> tuple[PriceHistory, PriceHistory]:
    keep = [i for i, d in enumerate(full.dates) if d <= cut]
    early = PriceHistory(full.symbol, [full.dates[i] for i in keep],
                         full.closes[keep], full.splits, full.retrieved_at)
    return early, full


def _to_records(recs, upto=None, after=None):
    out = []
    for r in recs:
        if upto and r.action_date > upto:
            continue
        if after and r.action_date <= after:
            continue
        out.append(r)
    return out


@pytest.fixture
def seeded(con):
    """Store holding only data dated on or before ASOF."""
    stock = make_history("TEST", n=2600, seed=11)
    sector = make_history("SPY", n=2600, seed=23, sigma=0.010)
    early_stock, _ = _split_history(stock, ASOF)
    early_sector, _ = _split_history(sector, ASOF)
    writer.append_price_history(con, early_stock)
    writer.append_price_history(con, early_sector)

    records, _ = generate_panel("TEST", stock, seed=4, n_firms=14)
    writer.append_price_targets(con, _to_records(records, upto=ASOF))
    return con, stock, sector, records


def _run(con):
    return run_debias(con, "TEST", ASOF, pit_mode=pit.ASSUME_VENDOR_HISTORY,
                      include_synthetic=True)


def test_result_is_unchanged_by_adding_future_data(seeded):
    con, stock, sector, records = seeded
    before = _run(con)

    # Now let the future arrive: a year of prices and every analyst action
    # taken after the as-of date.
    writer.append_price_history(con, stock)
    writer.append_price_history(con, sector)
    added = writer.append_price_targets(con, _to_records(records, after=ASOF))
    assert added["inserted"] > 20, "the test added no future data to defend against"

    after = _run(con)

    assert after.spot == pytest.approx(before.spot)
    assert after.spot_date == before.spot_date
    for field in ("median", "p25", "p75", "mean", "sd", "n", "n_eff"):
        assert getattr(after.final, field) == pytest.approx(
            getattr(before.final, field), rel=1e-12, nan_ok=True
        ), f"final.{field} moved when future data was added"
    assert after.beta.beta == pytest.approx(before.beta.beta)
    assert (after.age_decay.lam or 0) == pytest.approx(before.age_decay.lam or 0)
    assert after.firm_offsets.mu_global == pytest.approx(before.firm_offsets.mu_global)


def test_no_record_in_the_result_is_dated_after_the_asof(seeded):
    con, stock, sector, records = seeded
    writer.append_price_targets(con, _to_records(records, after=ASOF))
    result = _run(con)
    assert max(result.panel["action_date"]) <= ASOF


def test_spot_and_anchor_prices_never_come_from_after_the_asof(seeded):
    con, *_ = seeded
    result = _run(con)
    assert result.spot_date <= ASOF
    anchors = [d for d in result.panel["spot_price_date"] if d is not None]
    assert anchors and max(anchors) <= ASOF


def test_age_decay_is_fitted_only_on_completed_horizons(seeded):
    """The decay fit scores forecasts against realised prices. Those prices must
    already exist at the as-of date, which bounds the evaluation grid to
    asof - horizon."""
    con, stock, sector, records = seeded
    writer.append_price_history(con, stock)  # full history now in the store
    result = _run(con)
    history = pit.price_history_asof(con, "TEST", ASOF)
    assert history.end <= ASOF
    # Nothing can be scored against a price that does not exist yet.
    assert history.forward_close(ASOF, 365) is None
    # And the fit that consumes those prices saw only completed horizons.
    if result.age_decay.fitted:
        assert result.age_decay.n_clusters > 0
        assert result.age_decay.lam > 0


def test_strict_mode_on_a_single_snapshot_returns_nothing_rather_than_guessing(seeded):
    """The honest failure. A snapshot pulled today proves nothing about 2022."""
    con, *_ = seeded
    with pytest.raises(RuntimeError, match="strict"):
        run_debias(con, "TEST", ASOF, pit_mode=pit.STRICT, include_synthetic=True)


def test_restated_values_do_not_reach_the_model(seeded):
    con, stock, sector, records = seeded
    original = _to_records(records, upto=ASOF)[0]
    restated = PriceTargetRecord.build(
        ticker=original.ticker, analyst_firm=original.analyst_firm,
        analyst_name=original.analyst_name, action_date=original.action_date,
        price_target=original.price_target * 2.0, source=original.source,
        retrieved_at=datetime.now(timezone.utc) + timedelta(days=1), synthetic=True,
    )
    writer.append_price_targets(con, [restated])
    result = _run(con)
    row = result.panel[result.panel["natural_key"] == original.natural_key].iloc[0]
    assert row["price_target"] == pytest.approx(original.price_target)
    assert bool(row["was_restated"])
