"""Revision features -- first-class, because the level probably is not the signal.

The design brief's prior, taken from the literature, is that the LEVEL of
consensus implied return has near-zero predictive power, while the DERIVATIVE
-- how fast targets are being revised and how broadly -- is weakly informative.
So revisions are computed here as a primary output rather than being derived
ad hoc inside a composite later.

Three quantities, all point-in-time:

``revision_rate``
    Revisions per covering firm over the window. Measures how much the street
    is moving at all.
``revision_breadth``
    (upward - downward) / total over the window, in [-1, 1]. Measures agreement
    in the direction of movement, which is the part that is supposed to carry
    information independently of magnitude.
``revision_magnitude``
    Weighted mean log change in target over the window.

Prior target resolution has a fallback that matters: Benzinga publishes
``price_target_prev`` directly, FMP does not. Where it is absent the firm's own
previous action in the panel is used, which is only correct if the panel
contains that firm's full history -- so ``prev_source`` records which path was
taken and the data-quality report counts them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd


def extract_revisions(panel: pd.DataFrame) -> pd.DataFrame:
    """Attach per-record revision columns to a panel that has implied returns.

    Adds ``pt_prev_used``, ``prev_source`` ("vendor" | "panel" | "none"),
    ``revision_log`` (log change in target) and ``revision_dir`` (-1/0/+1).
    """
    df = panel.copy()
    df["action_date"] = pd.to_datetime(df["action_date"]).dt.date
    df = df.sort_values(["analyst_firm", "action_date"]).reset_index(drop=True)

    prev_used, prev_source = [], []
    last_by_firm: dict[str, float] = {}
    for _, row in df.iterrows():
        firm = row["analyst_firm"]
        vendor_prev = row.get("price_target_prev")
        panel_prev = last_by_firm.get(firm)
        if vendor_prev is not None and np.isfinite(vendor_prev) and vendor_prev > 0:
            prev_used.append(float(vendor_prev))
            prev_source.append("vendor")
        elif panel_prev is not None and np.isfinite(panel_prev) and panel_prev > 0:
            prev_used.append(float(panel_prev))
            prev_source.append("panel")
        else:
            prev_used.append(np.nan)
            prev_source.append("none")
        cur = row.get("price_target_used", row.get("price_target"))
        if cur is not None and np.isfinite(cur) and cur > 0:
            last_by_firm[firm] = float(cur)

    df["pt_prev_used"] = prev_used
    df["prev_source"] = prev_source
    cur = df.get("price_target_used", df.get("price_target")).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        df["revision_log"] = np.where(
            (df["pt_prev_used"] > 0) & (cur > 0),
            np.log(cur / df["pt_prev_used"]),
            np.nan,
        )
    df["revision_dir"] = np.sign(df["revision_log"]).fillna(0).astype(int)
    return df


@dataclass
class RevisionFeatures:
    asof: date
    window_days: int
    n_revisions: int
    n_firms_covering: int
    revision_rate: float
    revision_breadth: float
    revision_magnitude: float
    n_up: int
    n_down: int
    n_flat: int

    def as_dict(self) -> dict:
        return {
            "asof": self.asof, "window_days": self.window_days,
            "n_revisions": self.n_revisions, "n_firms_covering": self.n_firms_covering,
            "revision_rate": self.revision_rate,
            "revision_breadth": self.revision_breadth,
            "revision_magnitude": self.revision_magnitude,
            "n_up": self.n_up, "n_down": self.n_down, "n_flat": self.n_flat,
        }


def revision_features(
    panel: pd.DataFrame,
    asof: date,
    *,
    window_days: int = 60,
    coverage_days: int = 365,
    min_change: float = 0.005,
) -> RevisionFeatures:
    """Revision rate, breadth and magnitude over the trailing window at ``asof``.

    ``min_change`` treats a sub-0.5% move as no revision: vendors reprint an
    unchanged target on every note, and counting those as revisions inflates
    the rate and dilutes breadth toward zero.
    """
    df = panel if "revision_log" in panel else extract_revisions(panel)
    ad = pd.to_datetime(df["action_date"]).dt.date
    covering = df[(ad <= asof) & (ad > asof - timedelta(days=coverage_days))]
    n_firms = int(covering["analyst_firm"].nunique())

    win = df[(ad <= asof) & (ad > asof - timedelta(days=window_days))]
    win = win[win["revision_log"].notna()]
    changed = win[win["revision_log"].abs() >= min_change]
    n_up = int((changed["revision_log"] > 0).sum())
    n_down = int((changed["revision_log"] < 0).sum())
    n_flat = int(len(win) - len(changed))
    n_rev = n_up + n_down

    return RevisionFeatures(
        asof=asof,
        window_days=window_days,
        n_revisions=n_rev,
        n_firms_covering=n_firms,
        revision_rate=(n_rev / n_firms) if n_firms else float("nan"),
        revision_breadth=((n_up - n_down) / n_rev) if n_rev else float("nan"),
        revision_magnitude=float(changed["revision_log"].mean()) if n_rev else float("nan"),
        n_up=n_up, n_down=n_down, n_flat=n_flat,
    )


def monthly_snapshots(
    panel: pd.DataFrame,
    history,
    asof: date,
    *,
    horizon_days: int = 365,
    level_window: int = 180,
    revision_window: int = 60,
    freq: str = "ME",
) -> pd.DataFrame:
    """Month-end time series of panel level, revision features and the outcome.

    One row per month end: what the panel looked like then, and what the stock
    actually did over the following ``horizon_days``. Rows whose horizon has not
    completed are dropped rather than truncated -- a partially-elapsed horizon
    compared against a full one is how a 12-month test quietly becomes a
    3-month test.

    Consecutive rows overlap by ``horizon_days - 1`` days, so any regression on
    this frame needs HAC standard errors (see ``src/model/regress.py``).
    """
    df = panel if "revision_log" in panel else extract_revisions(panel)
    ad = pd.to_datetime(df["action_date"]).dt.date
    if df.empty:
        return pd.DataFrame()

    rows = []
    for ts in pd.date_range(min(ad), asof, freq=freq):
        t = ts.date()
        spot_hit = history.close_on_or_before(t)
        fwd = history.forward_close(t, horizon_days)
        if spot_hit is None or fwd is None or spot_hit[1] <= 0:
            continue
        window = df[(ad <= t) & (ad > t - timedelta(days=level_window))]
        window = window[window.get("usable", True)]
        rf = revision_features(df, t, window_days=revision_window)

        level_raw = level_deb = np.nan
        if len(window):
            pt = window.get("price_target_used", window.get("price_target")).astype(float)
            valid = pt[(pt > 0) & pt.notna()]
            if len(valid):
                level_raw = float(np.median(valid / spot_hit[1] - 1.0))
            if "resid_return" in window and window["resid_return"].notna().any():
                level_deb = float(window["resid_return"].median())

        rows.append(
            {
                "date": t,
                "spot": spot_hit[1],
                "n_records": int(len(window)),
                "n_firms": int(window["analyst_firm"].nunique()) if len(window) else 0,
                "level_raw": level_raw,
                "level_debiased": level_deb,
                "revision_rate": rf.revision_rate,
                "revision_breadth": rf.revision_breadth,
                "revision_magnitude": rf.revision_magnitude,
                "fwd_return": fwd[1] / spot_hit[1] - 1.0,
            }
        )
    return pd.DataFrame(rows)
