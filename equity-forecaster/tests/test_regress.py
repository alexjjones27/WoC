"""Standard errors. The whole point is that the naive ones are wrong here."""
from __future__ import annotations

import numpy as np
import pytest

from src.model.regress import ols_cluster, ols_hac


def test_ols_recovers_a_known_slope():
    rng = np.random.default_rng(0)
    x = rng.normal(size=800)
    y = 2.0 + 0.75 * x + rng.normal(0, 0.2, 800)
    res = ols_cluster(y, x, clusters=np.arange(800))
    assert res.params[0] == pytest.approx(2.0, abs=0.05)
    assert res.params[1] == pytest.approx(0.75, abs=0.05)
    assert res.r2 > 0.9


def test_clustered_errors_inflate_the_standard_error():
    """With a shared shock per cluster, treating observations as independent
    understates the standard error several-fold. This is exactly the structure
    of analyst records scored against a common realised price."""
    rng = np.random.default_rng(1)
    g, per = 30, 40
    clusters = np.repeat(np.arange(g), per)
    shock = np.repeat(rng.normal(0, 1.0, g), per)
    x = np.repeat(rng.normal(0, 1.0, g), per) + rng.normal(0, 0.1, g * per)
    y = 0.3 * x + shock + rng.normal(0, 0.2, g * per)

    clustered = ols_cluster(y, x, clusters)
    naive = ols_cluster(y, x, np.arange(len(y)))
    assert clustered.se[1] > 3 * naive.se[1]
    assert clustered.n_clusters == g


def test_hac_inflates_the_standard_error_under_serial_correlation():
    """Overlapping horizons induce serial correlation; Newey-West is the fix."""
    rng = np.random.default_rng(2)
    n = 600
    e = rng.normal(size=n + 20)
    # 20-period moving average: the same shape as overlapping annual returns.
    y = np.array([e[i:i + 20].mean() for i in range(n)])
    x = rng.normal(size=n)
    hac = ols_hac(y, x, lags=25)
    plain = ols_hac(y, x, lags=0)
    assert hac.se[0] > 2 * plain.se[0]
    assert "HAC" in hac.cov_type


def test_regression_refuses_an_underdetermined_system():
    with pytest.raises(ValueError, match="not enough observations"):
        ols_cluster([1.0], [[2.0, 3.0]], clusters=[0])
