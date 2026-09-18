"""Revision features: the part the brief expects to carry the signal."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from conftest import make_history
from src.model.revisions import extract_revisions, monthly_snapshots, revision_features


def _panel(rows):
    return pd.DataFrame(rows)


def test_vendor_prior_target_is_preferred_over_reconstruction():
    df = extract_revisions(_panel([
        {"analyst_firm": "A", "action_date": date(2025, 1, 6), "price_target": 100.0,
         "price_target_prev": None},
        {"analyst_firm": "A", "action_date": date(2025, 3, 6), "price_target": 120.0,
         "price_target_prev": 110.0},
    ]))
    assert list(df["prev_source"]) == ["none", "vendor"]
    assert df.iloc[1]["pt_prev_used"] == 110.0
    assert df.iloc[1]["revision_log"] == pytest.approx(np.log(120 / 110))


def test_prior_target_falls_back_to_the_firms_own_previous_action():
    df = extract_revisions(_panel([
        {"analyst_firm": "A", "action_date": date(2025, 1, 6), "price_target": 100.0,
         "price_target_prev": None},
        {"analyst_firm": "A", "action_date": date(2025, 3, 6), "price_target": 120.0,
         "price_target_prev": None},
    ]))
    assert list(df["prev_source"]) == ["none", "panel"]
    assert df.iloc[1]["revision_log"] == pytest.approx(np.log(1.2))


def test_prior_targets_do_not_leak_between_firms():
    df = extract_revisions(_panel([
        {"analyst_firm": "A", "action_date": date(2025, 1, 6), "price_target": 100.0,
         "price_target_prev": None},
        {"analyst_firm": "B", "action_date": date(2025, 3, 6), "price_target": 500.0,
         "price_target_prev": None},
    ]))
    assert df[df["analyst_firm"] == "B"].iloc[0]["prev_source"] == "none"


def test_breadth_is_plus_one_when_every_revision_is_upward():
    asof = date(2025, 6, 30)
    rows = []
    for i, firm in enumerate("ABCD"):
        rows.append({"analyst_firm": firm, "action_date": asof - timedelta(days=200),
                     "price_target": 100.0, "price_target_prev": None})
        rows.append({"analyst_firm": firm, "action_date": asof - timedelta(days=10),
                     "price_target": 120.0, "price_target_prev": 100.0})
    rf = revision_features(_panel(rows), asof, window_days=60)
    assert rf.n_up == 4 and rf.n_down == 0
    assert rf.revision_breadth == pytest.approx(1.0)
    assert rf.revision_rate == pytest.approx(1.0)
    assert rf.revision_magnitude == pytest.approx(np.log(1.2))


def test_reprinted_unchanged_targets_are_not_counted_as_revisions():
    """Vendors reprint the same target on every note; counting those inflates
    the rate and pulls breadth toward zero."""
    asof = date(2025, 6, 30)
    rows = [
        {"analyst_firm": "A", "action_date": asof - timedelta(days=40),
         "price_target": 100.0, "price_target_prev": None},
        {"analyst_firm": "A", "action_date": asof - timedelta(days=20),
         "price_target": 100.0, "price_target_prev": 100.0},
        {"analyst_firm": "A", "action_date": asof - timedelta(days=5),
         "price_target": 130.0, "price_target_prev": 100.0},
    ]
    rf = revision_features(_panel(rows), asof, window_days=60)
    assert rf.n_flat == 1 and rf.n_revisions == 1 and rf.n_up == 1


def test_revision_features_respect_the_asof_date():
    asof = date(2025, 6, 30)
    rows = [
        {"analyst_firm": "A", "action_date": asof - timedelta(days=10),
         "price_target": 110.0, "price_target_prev": 100.0},
        {"analyst_firm": "A", "action_date": asof + timedelta(days=10),
         "price_target": 400.0, "price_target_prev": 110.0},
    ]
    rf = revision_features(_panel(rows), asof, window_days=60)
    assert rf.n_revisions == 1 and rf.revision_magnitude == pytest.approx(np.log(1.1))


def test_monthly_snapshots_drop_incomplete_horizons():
    h = make_history(n=1200)
    rows = [{"ticker": "T", "analyst_firm": "A", "action_date": d,
             "price_target": float(h.close_on_or_before(d)[1] * 1.1),
             "price_target_prev": None, "usable": True}
            for d in h.dates[::40]]
    snaps = monthly_snapshots(_panel(rows), h, h.dates[-1], horizon_days=365)
    assert len(snaps) > 0
    # Every row must have a realised outcome a full horizon later.
    assert (pd.to_datetime(snaps["date"]).dt.date <=
            h.dates[-1] - timedelta(days=365)).all()
    assert snaps["fwd_return"].notna().all()
