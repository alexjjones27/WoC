"""Price accessors: the place look-ahead gets in through the back door."""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from conftest import make_history
from src.ingest.prices import spot_at_action


def test_prior_close_is_strictly_before(history):
    d = history.dates[500]
    prior = history.close_strictly_before(d)
    same = history.close_on_or_before(d)
    assert prior[0] < d
    assert same[0] == d
    assert prior[1] != same[1]


def test_spot_at_action_conventions_differ(history):
    d = history.dates[500]
    pd_, pp, note = spot_at_action(history, d, "prior_close")
    ad, ap, note2 = spot_at_action(history, d, "action_close")
    assert note == "prior_close" and note2 == "action_close"
    assert pd_ < ad == d


def test_spot_at_action_rejects_unknown_convention(history):
    with pytest.raises(ValueError):
        spot_at_action(history, history.dates[10], "tomorrow_close")


def test_forward_close_returns_none_past_end(history):
    """A horizon that has not elapsed must be None, never the last price.

    Clamping to the last available close silently evaluates a 12-month forecast
    against a shorter outcome and is one of the most effective ways to
    manufacture a backtest.
    """
    assert history.forward_close(history.dates[-5], 365) is None
    hit = history.forward_close(history.dates[100], 365)
    assert hit is not None and hit[0] >= history.dates[100]


def test_split_factor_only_counts_splits_after_the_date():
    h = make_history(splits=((date(2020, 6, 1), 4.0), (date(2022, 6, 1), 2.0)))
    assert h.split_factor_after(date(2019, 1, 1)) == 8.0
    assert h.split_factor_after(date(2021, 1, 1)) == 2.0
    assert h.split_factor_after(date(2023, 1, 1)) == 1.0


def test_close_lookups_skip_nan_bars():
    h = make_history(n=300)
    h.closes[150] = np.nan
    hit = h.close_on_or_before(h.dates[150])
    assert hit[0] == h.dates[149] and np.isfinite(hit[1])
