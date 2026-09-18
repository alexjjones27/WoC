"""A SIMULATED per-analyst panel. Not data. Never a research finding.

No credential for Benzinga, FMP, or any I/B/E/S-grade feed was available in the
environment this was built in, and a pipeline whose only code path requires a
key nobody has is not a pipeline you can trust. So this module generates a
panel with the statistical pathologies the design brief is built around, anchored
to REAL split-adjusted prices for the ticker, so every stage downstream can be
run, inspected, and unit-tested end to end.

Three guardrails keep it from being mistaken for data:

* every row is stamped ``source="synthetic:v1"``, which the store records and
  every report prints;
* every firm is named ``SYNTH-<x>``, so a synthetic firm cannot be confused for
  a real one in any output;
* the store refuses to mix synthetic and real rows in one analysis without an
  explicit override.

What is deliberately injected, because the brief says these are the properties
that break naive aggregation:

``mu_f``      a persistent per-firm anchoring offset -- the target is set at a
              roughly stable multiple of prevailing price, mostly above it.
``phi_f``     a loading on the trailing 20-day return: the analyst revises
              AFTER the stock moves, so their "forecast" partly restates the
              recent past. High phi = follower = near-zero independent content.
``street_t``  a persistent common shock shared across the panel on any given
              date, so errors are CORRELATED and simple averaging does not
              cancel them.
``clusters``  groups of firms whose idiosyncratic noise is mostly copied from a
              cluster leader -- five analysts who count as roughly one opinion.
``alpha_f``   a small genuine loading on the ticker's realised 12-month
              forward return. Deliberately small: the brief's prior is
              that the LEVEL is close to uninformative, and the simulation is
              built to honour that prior rather than to flatter the model.
``staleness`` some firms stop updating and leave a target sitting in the panel.

:func:`generate_panel` returns the records alongside a ground-truth table, so
tests can assert that the Stage 2 de-biaser recovers the injected ``mu_f``
rather than merely producing a plausible-looking number.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .base import PriceTargetRecord
from .prices import PriceHistory

SOURCE = "synthetic:v1"

_FIRM_STEMS = [
    "Alder", "Birch", "Cedar", "Dogwood", "Elm", "Fir", "Ginkgo", "Hazel",
    "Ironwood", "Juniper", "Katsu", "Larch", "Maple", "Nyssa", "Oak",
    "Poplar", "Quince", "Rowan", "Sycamore", "Teak", "Umbra", "Vitex",
]

#: Firm-level anchoring offset prior: most of the street sits above spot.
MU_MEAN, MU_SD = 0.11, 0.07
#: Loading on the common "street" shock -- the reason averaging does not cancel.
STREET_SD, STREET_AR = 0.035, 0.94


def _firm_names(n: int) -> list[str]:
    return [f"SYNTH-{_FIRM_STEMS[i % len(_FIRM_STEMS)]}" for i in range(n)]


def _round_target(price: float) -> float:
    """Analysts publish round numbers, and that rounding is itself a data quirk."""
    if price >= 100:
        return float(round(price / 5.0) * 5.0)
    if price >= 20:
        return float(round(price))
    return float(round(price * 2) / 2)


def _rating_from_implied(r: float) -> str:
    if r > 0.22:
        return "Strong Buy"
    if r > 0.07:
        return "Buy"
    if r > -0.05:
        return "Hold"
    if r > -0.15:
        return "Underperform"
    return "Sell"


@dataclass
class SyntheticTruth:
    """The generative parameters, for tests to check recovery against."""

    firms: pd.DataFrame
    street: pd.Series
    seed: int


def generate_panel(
    ticker: str,
    history: PriceHistory,
    *,
    n_firms: int = 18,
    start: date | None = None,
    end: date | None = None,
    seed: int | None = None,
    horizon_days: int = 365,
    restatement_rate: float = 0.03,
    as_quoted_targets: bool = False,
    retrieved_at: datetime | None = None,
) -> tuple[list[PriceTargetRecord], SyntheticTruth]:
    """Generate a simulated analyst panel for ``ticker`` on real price history.

    Parameters
    ----------
    as_quoted_targets:
        When True, targets set before a stock split are emitted AS QUOTED at
        the time (i.e. un-split-adjusted), which is how several real vendors
        store history. Off by default so the demo panel is clean; turned on in
        the tests to prove the split guard actually fires.
    restatement_rate:
        Fraction of events that get a second, later-retrieved row with a
        changed target -- a vendor quietly rewriting history. The store keeps
        both and the point-in-time reader must return the first.
    """
    if seed is None:
        seed = abs(hash(ticker.upper())) % (2**31)
    rng = np.random.default_rng(seed)
    retrieved_at = retrieved_at or datetime.now(timezone.utc)

    dates = [d for d, c in zip(history.dates, history.closes) if np.isfinite(c)]
    closes = np.array([c for c in history.closes if np.isfinite(c)], dtype=float)
    if start:
        keep = [i for i, d in enumerate(dates) if d >= start]
        dates, closes = [dates[i] for i in keep], closes[keep]
    if end:
        keep = [i for i, d in enumerate(dates) if d <= end]
        dates, closes = [dates[i] for i in keep], closes[keep]
    if len(dates) < 300:
        raise ValueError(f"need >=300 trading days of history, got {len(dates)}")

    # Trailing 20-day return, the thing followers chase.
    trail20 = np.full(len(closes), np.nan)
    trail20[20:] = closes[20:] / closes[:-20] - 1.0

    # Realised sector-adjusted forward return: the only genuinely predictive
    # quantity in the simulation, and only a sliver of it is given to anyone.
    fwd = np.full(len(closes), np.nan)
    for i, d in enumerate(dates):
        hit = history.forward_close(d, horizon_days)
        if hit is not None:
            fwd[i] = hit[1] / closes[i] - 1.0

    # Persistent common shock across the whole panel.
    street = np.zeros(len(dates))
    for i in range(1, len(dates)):
        street[i] = STREET_AR * street[i - 1] + rng.normal(0, STREET_SD)

    names = _firm_names(n_firms)
    n_clusters = max(2, n_firms // 4)
    cluster_of = rng.integers(0, n_clusters, size=n_firms)
    # One leader per cluster; followers copy most of the leader's idio noise.
    leader_of_cluster = {c: int(np.where(cluster_of == c)[0][0]) for c in set(cluster_of.tolist())}

    firms = pd.DataFrame(
        {
            "analyst_firm": names,
            "mu_f": np.clip(rng.normal(MU_MEAN, MU_SD, n_firms), -0.06, 0.34),
            "sigma_f": rng.uniform(0.035, 0.110, n_firms),
            "phi_f": rng.uniform(0.0, 0.80, n_firms),
            "alpha_f": rng.normal(0.0, 0.120, n_firms),
            "street_load": rng.uniform(0.3, 1.0, n_firms),
            "cadence_days": rng.integers(55, 110, n_firms),
            "cluster": cluster_of,
            "copy_weight": rng.uniform(0.55, 0.85, n_firms),
        }
    ).set_index("analyst_firm")

    # Per-date idiosyncratic draws for the cluster leaders, so followers can
    # copy them and produce the correlated-residual structure.
    leader_noise = {
        c: rng.normal(0, 1.0, len(dates)) for c in leader_of_cluster
    }

    records: list[PriceTargetRecord] = []
    for fi, firm in enumerate(names):
        p = firms.loc[firm]
        # Coverage window: some firms initiate late, some go stale and never
        # update again, leaving an old target sitting in the panel.
        first_i = int(rng.integers(0, max(1, len(dates) // 3)))
        stale_i = len(dates)
        if rng.random() < 0.20:
            stale_i = int(rng.integers(len(dates) // 2, len(dates)))

        analyst = f"{firm.split('-')[1]} Analyst {fi % 3 + 1}"
        prev_pt: float | None = None
        prev_rating_raw: str | None = None
        i = first_i
        while i < min(stale_i, len(dates)):
            d = dates[i]
            t20 = trail20[i] if np.isfinite(trail20[i]) else 0.0
            fwd_i = fwd[i] if np.isfinite(fwd[i]) else 0.0

            leader = leader_of_cluster[int(p["cluster"])]
            shared = leader_noise[int(p["cluster"])][i]
            own = rng.normal(0, 1.0)
            w = 0.0 if fi == leader else float(p["copy_weight"])
            idio = p["sigma_f"] * (w * shared + (1 - w) * own)

            implied = (
                float(p["mu_f"])
                + float(p["phi_f"]) * t20
                + float(p["alpha_f"]) * fwd_i  # genuine skill: a loading on the realised move
                + float(p["street_load"]) * street[i]
                + idio
            )
            implied = float(np.clip(implied, -0.55, 1.20))

            anchor = float(closes[i])
            target = _round_target(anchor * (1.0 + implied))
            if as_quoted_targets:
                # Emit the target in the money-of-the-day, i.e. multiplied back
                # up by any split that happened afterwards.
                target = _round_target(target * history.split_factor_after(d))

            rating_raw = _rating_from_implied(implied)
            rec = PriceTargetRecord.build(
                ticker=ticker,
                analyst_firm=firm,
                analyst_name=analyst,
                action_date=d,
                rating=rating_raw,
                rating_prev=prev_rating_raw,
                price_target=target,
                price_target_prev=prev_pt,
                # The simulated vendor does not publish a prevailing price.
                spot_at_action=None,
                fiscal_year_covered=d.year + 1,
                source=SOURCE,
                retrieved_at=retrieved_at,
                synthetic=True,
            )
            records.append(rec)

            if rng.random() < restatement_rate:
                # A vendor restatement: same event, changed number, later pull.
                records.append(
                    PriceTargetRecord.build(
                        ticker=ticker,
                        analyst_firm=firm,
                        analyst_name=analyst,
                        action_date=d,
                        rating=rating_raw,
                        rating_prev=prev_rating_raw,
                        price_target=_round_target(target * (1 + rng.normal(0, 0.03))),
                        price_target_prev=prev_pt,
                        spot_at_action=None,
                        fiscal_year_covered=d.year + 1,
                        source=SOURCE,
                        retrieved_at=retrieved_at + timedelta(days=1),
                        synthetic=True,
                        restatement=True,
                    )
                )

            prev_pt, prev_rating_raw = target, rating_raw

            # Next action: regular cadence, pulled forward by a big move.
            step = int(rng.normal(float(p["cadence_days"]), 12))
            nxt = i + max(5, step)
            for j in range(i + 5, min(nxt, len(dates))):
                if np.isfinite(trail20[j]) and abs(trail20[j]) > 0.14 and rng.random() < 0.35:
                    nxt = j
                    break
            i = nxt

    truth = SyntheticTruth(
        firms=firms.reset_index(),
        street=pd.Series(street, index=pd.Index(dates, name="date"), name="street"),
        seed=seed,
    )
    return records, truth
