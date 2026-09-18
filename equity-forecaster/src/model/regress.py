"""Small OLS helpers with honest standard errors.

Nothing in this pipeline gets to quote a t-stat from a plain OLS covariance.
Two dependence structures show up constantly in this data and both inflate
naive t-stats by a large factor:

* **Clustering by date.** Every analyst record evaluated at the same date is
  scored against the same realised price, so their errors share a common shock.
  :func:`ols_cluster` is the fix.
* **Overlapping horizons.** A 12-month forecast series sampled monthly has 11
  months of overlap between consecutive observations. :func:`ols_hac` (Newey-West)
  is the fix, and Stage 8 will lean on it.

Both are here rather than in the backtest package because Stage 2 already needs
them -- the age-decay fit is a regression over overlapping, date-clustered
observations, and reporting its slope with a naive standard error would be
exactly the mistake the brief warns about.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class OLSResult:
    params: np.ndarray
    se: np.ndarray
    tstat: np.ndarray
    n_obs: int
    n_clusters: int | None
    r2: float
    cov_type: str

    def ci(self, index: int, level: float = 1.96) -> tuple[float, float]:
        return (
            float(self.params[index] - level * self.se[index]),
            float(self.params[index] + level * self.se[index]),
        )


def _prep(y, X):
    y = np.asarray(y, dtype=float).ravel()
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X[:, None]
    ok = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    return y[ok], X[ok], ok


def ols_cluster(y, X, clusters, add_const: bool = True) -> OLSResult:
    """OLS with cluster-robust (CR1) standard errors.

    ``clusters`` is any array of group labels, typically the evaluation date.
    With G clusters the usual finite-sample correction G/(G-1) * (n-1)/(n-k)
    is applied; with very few clusters the result is still optimistic and the
    caller should say so rather than quote it as decisive.
    """
    y, X, ok = _prep(y, X)
    clusters = np.asarray(clusters)[ok]
    if add_const:
        X = np.column_stack([np.ones(len(y)), X])
    n, k = X.shape
    if n <= k:
        raise ValueError(f"not enough observations ({n}) for {k} parameters")

    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ (X.T @ y)
    resid = y - X @ beta

    labels, idx = np.unique(clusters, return_inverse=True)
    g = len(labels)
    meat = np.zeros((k, k))
    for j in range(g):
        m = idx == j
        Xg, ug = X[m], resid[m]
        s = Xg.T @ ug
        meat += np.outer(s, s)
    scale = (g / max(g - 1, 1)) * ((n - 1) / max(n - k, 1))
    cov = scale * (XtX_inv @ meat @ XtX_inv)
    se = np.sqrt(np.clip(np.diag(cov), 0, None))

    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    with np.errstate(invalid="ignore", divide="ignore"):
        tstat = np.where(se > 0, beta / se, np.nan)
    return OLSResult(
        params=beta,
        se=se,
        tstat=tstat,
        n_obs=n,
        n_clusters=g,
        r2=1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan"),
        cov_type="cluster",
    )


def ols_hac(y, X, lags: int | None = None, add_const: bool = True) -> OLSResult:
    """OLS with Newey-West (Bartlett kernel) standard errors.

    Default lag length is the usual ``floor(4 * (n/100)^(2/9))`` rule; for
    overlapping h-period returns, pass ``lags=h-1`` or more instead, because
    the rule of thumb is far too short for that case.
    """
    y, X, _ = _prep(y, X)
    if add_const:
        X = np.column_stack([np.ones(len(y)), X])
    n, k = X.shape
    if n <= k:
        raise ValueError(f"not enough observations ({n}) for {k} parameters")
    if lags is None:
        lags = int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0)))

    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ (X.T @ y)
    resid = y - X @ beta
    u = X * resid[:, None]

    meat = u.T @ u
    for lag in range(1, lags + 1):
        w = 1.0 - lag / (lags + 1.0)
        gamma = u[lag:].T @ u[:-lag]
        meat += w * (gamma + gamma.T)
    cov = (n / max(n - k, 1)) * (XtX_inv @ meat @ XtX_inv)
    se = np.sqrt(np.clip(np.diag(cov), 0, None))

    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    with np.errstate(invalid="ignore", divide="ignore"):
        tstat = np.where(se > 0, beta / se, np.nan)
    return OLSResult(
        params=beta, se=se, tstat=tstat, n_obs=n, n_clusters=None,
        r2=1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan"),
        cov_type=f"HAC(nw,{lags})",
    )
