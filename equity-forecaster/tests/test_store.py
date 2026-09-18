"""Append-only store and point-in-time reads.

These tests encode the brief's anti-look-ahead rules as executable assertions,
because "we never overwrite history" is the kind of claim that silently stops
being true.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.ingest.base import PriceTargetRecord
from src.store import pit, writer

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)


def rec(pt=120.0, retrieved=NOW, firm="Acme", d=date(2025, 6, 2), source="vendorX"):
    return PriceTargetRecord.build(
        ticker="TEST", analyst_firm=firm, analyst_name="A Person",
        action_date=d, rating="Buy", price_target=pt, spot_at_action=100.0,
        source=source, retrieved_at=retrieved,
    )


def test_identical_rows_are_deduped_not_duplicated(con):
    r = rec()
    first = writer.append_price_targets(con, [r])
    second = writer.append_price_targets(con, [r])
    assert first["inserted"] == 1
    assert second["inserted"] == 0 and second["duplicate"] == 1
    assert con.execute("SELECT COUNT(*) FROM price_target_raw").fetchone()[0] == 1


def test_restatement_adds_a_row_and_keeps_the_original(con):
    original = rec(pt=120.0, retrieved=NOW)
    restated = rec(pt=145.0, retrieved=NOW + timedelta(days=30))
    writer.append_price_targets(con, [original])
    stats = writer.append_price_targets(con, [restated])

    assert stats["restated"] == 1
    assert con.execute("SELECT COUNT(*) FROM price_target_raw").fetchone()[0] == 2
    assert original.natural_key == restated.natural_key
    assert original.payload_hash != restated.payload_hash


def test_pit_returns_the_original_value_never_the_restatement(con):
    writer.append_price_targets(con, [rec(pt=120.0, retrieved=NOW)])
    writer.append_price_targets(con, [rec(pt=145.0, retrieved=NOW + timedelta(days=30))])

    df = pit.price_targets_asof(con, "TEST", date(2026, 3, 1),
                                pit_mode=pit.ASSUME_VENDOR_HISTORY)
    assert len(df) == 1
    assert df.iloc[0]["price_target"] == 120.0      # the originally published value
    assert bool(df.iloc[0]["was_restated"]) is True  # and we know it was changed


def test_strict_mode_hides_rows_retrieved_after_the_asof_date(con):
    writer.append_price_targets(con, [rec(retrieved=NOW)])
    before = pit.price_targets_asof(con, "TEST", NOW.date() - timedelta(days=1),
                                    pit_mode=pit.STRICT)
    after = pit.price_targets_asof(con, "TEST", NOW.date() + timedelta(days=1),
                                   pit_mode=pit.STRICT)
    assert before.empty, "a row pulled today was not knowable yesterday"
    assert len(after) == 1


def test_assume_vendor_history_ignores_retrieved_at_but_still_bounds_action_date(con):
    writer.append_price_targets(con, [rec(d=date(2025, 6, 2), retrieved=NOW)])
    writer.append_price_targets(con, [rec(d=date(2025, 12, 2), retrieved=NOW, firm="Beta")])
    df = pit.price_targets_asof(con, "TEST", date(2025, 8, 1),
                                pit_mode=pit.ASSUME_VENDOR_HISTORY)
    assert len(df) == 1 and df.iloc[0]["action_date"] == date(2025, 6, 2)


def test_pit_mode_must_be_a_known_mode(con):
    with pytest.raises(ValueError, match="pit_mode"):
        pit.price_targets_asof(con, "TEST", date(2026, 1, 1), pit_mode="trust_me")


def test_refuses_to_blend_synthetic_and_real_rows(con):
    real = rec(firm="Acme", source="vendorX")
    fake = PriceTargetRecord.build(
        ticker="TEST", analyst_firm="SYNTH-Alder", action_date=date(2025, 6, 2),
        price_target=130.0, source="synthetic:v1", retrieved_at=NOW, synthetic=True,
    )
    writer.append_price_targets(con, [real, fake])
    with pytest.raises(ValueError, match="Refusing to blend"):
        pit.price_targets_asof(con, "TEST", date(2026, 3, 1),
                               pit_mode=pit.ASSUME_VENDOR_HISTORY)
    only_real = pit.price_targets_asof(con, "TEST", date(2026, 3, 1),
                                       pit_mode=pit.ASSUME_VENDOR_HISTORY,
                                       include_synthetic=False)
    assert len(only_real) == 1 and not bool(only_real.iloc[0]["is_synthetic"])


def test_price_history_asof_truncates_prices_but_keeps_splits(con):
    from conftest import make_history

    h = make_history("TEST", splits=((date(2020, 6, 1), 4.0),))
    writer.append_price_history(con, h)
    view = pit.price_history_asof(con, "TEST", date(2019, 6, 1))
    assert view.end <= date(2019, 6, 1)
    # The split is a units conversion, not information: dropping it would leave
    # pre-split targets divided by post-split prices.
    assert view.split_factor_after(date(2019, 1, 1)) == 4.0


def test_provenance_counts_restated_events(con):
    writer.append_price_targets(con, [rec(pt=120.0, retrieved=NOW)])
    writer.append_price_targets(con, [rec(pt=145.0, retrieved=NOW + timedelta(days=5))])
    p = pit.provenance(con, "TEST")
    assert p["n_rows"] == 2 and p["n_events"] == 1 and p["n_restated_events"] == 1
