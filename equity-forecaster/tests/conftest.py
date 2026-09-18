"""Offline fixtures. No test in this suite touches the network."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingest.prices import PriceHistory, Split  # noqa: E402
from src.store import writer  # noqa: E402


def _business_days(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def make_history(
    symbol: str = "TEST",
    start: date = date(2015, 1, 5),
    n: int = 2600,
    mu: float = 0.0003,
    sigma: float = 0.016,
    s0: float = 100.0,
    seed: int = 11,
    splits: tuple = (),
) -> PriceHistory:
    """A deterministic geometric random walk, back-adjusted like a real feed."""
    rng = np.random.default_rng(seed)
    dates = _business_days(start, n)
    steps = rng.normal(mu, sigma, n)
    closes = s0 * np.exp(np.cumsum(steps))
    return PriceHistory(
        symbol, dates, closes,
        [Split(effective=d, ratio=r) for d, r in splits],
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def history() -> PriceHistory:
    return make_history()


@pytest.fixture
def sector_history() -> PriceHistory:
    return make_history("SECT", sigma=0.011, seed=23)


@pytest.fixture
def con(tmp_path):
    c = writer.connect(tmp_path / "test.duckdb")
    yield c
    c.close()
