"""The canonical per-analyst price-target record, and vendor-client plumbing.

Every source -- Benzinga, FMP, Finnhub, or the synthetic generator -- normalises
into :class:`PriceTargetRecord`. Nothing downstream of this module knows which
vendor a row came from except through the ``source`` string, which is carried
all the way into the store so that vendor-disagreement diagnostics remain
possible.

Two identifiers matter and they are not the same thing:

``natural_key``
    Identifies a *forecast event*: this firm, this analyst, this ticker, this
    action date, from this source. Two rows sharing a natural key are two
    observations of the same event -- typically an original publication and a
    later vendor restatement of it.

``payload_hash``
    Identifies the *content* of an observation. A restatement shares the
    natural key but differs in payload hash, which is exactly how the store
    detects that a vendor has quietly changed history.

The point-in-time reader (src/store/pit.py) resolves a natural key to its
EARLIEST-retrieved row, which is the anti-look-ahead rule the brief demands:
the value as originally published, never the restated one.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

# ---------------------------------------------------------------------------
# Rating normalisation
#
# Vendors use overlapping but inconsistent vocabularies. Mapping to a single
# 5-point scale at ingest keeps rating_prev -> rating transitions comparable
# across sources. Unrecognised strings are preserved verbatim in
# ``rating_raw`` and mapped to None rather than being guessed at.
# ---------------------------------------------------------------------------

_RATING_MAP = {
    # strong buy
    "strong buy": 5, "conviction buy": 5, "top pick": 5, "strong-buy": 5,
    # buy
    "buy": 4, "outperform": 4, "overweight": 4, "accumulate": 4, "add": 4,
    "positive": 4, "market outperform": 4, "sector outperform": 4,
    # hold
    "hold": 3, "neutral": 3, "market perform": 3, "equal-weight": 3,
    "equal weight": 3, "in-line": 3, "in line": 3, "sector perform": 3,
    "peer perform": 3, "perform": 3,
    # sell
    "sell": 2, "underperform": 2, "underweight": 2, "reduce": 2,
    "negative": 2, "market underperform": 2, "sector underperform": 2,
    # strong sell
    "strong sell": 1, "conviction sell": 1, "strong-sell": 1,
}


def normalise_rating(raw: str | None) -> int | None:
    """Map a vendor rating string onto a 1 (strong sell) .. 5 (strong buy) scale.

    Returns None for anything unrecognised; the caller keeps the raw string.
    """
    if raw is None:
        return None
    key = " ".join(str(raw).strip().lower().replace("_", " ").split())
    return _RATING_MAP.get(key)


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        for parser in (datetime.fromisoformat,):
            try:
                return parser(text).date()
            except ValueError:
                pass
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d"):
            try:
                return datetime.strptime(text[:10], fmt).date()
            except ValueError:
                continue
    raise ValueError(f"cannot parse date from {value!r}")


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


@dataclass(frozen=True)
class PriceTargetRecord:
    """One analyst action on one ticker, as originally published.

    Field names follow the brief's required schema. ``spot_at_action`` is the
    VENDOR's claim about the price when the action happened; it is deliberately
    kept separate from the spot the pipeline computes from its own price
    history, because a vendor that back-fills this field with the CURRENT price
    is a look-ahead bug that only shows up if you keep both and compare them
    (see src/diagnostics/data_quality.py).
    """

    ticker: str
    analyst_firm: str
    analyst_name: str | None
    action_date: date
    rating: int | None
    rating_prev: int | None
    price_target: float | None
    price_target_prev: float | None
    spot_at_action: float | None
    fiscal_year_covered: int | None
    source: str
    retrieved_at: datetime
    rating_raw: str | None = None
    rating_prev_raw: str | None = None
    #: When the VENDOR published this, if known. Distinct from action_date
    #: (when the analyst acted) and from retrieved_at (when we pulled it).
    #: Never used as the event timestamp -- kept only for staleness diagnostics.
    vendor_published_at: datetime | None = None
    currency: str = "USD"
    extra: dict = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not self.ticker:
            raise ValueError("ticker is required")
        if not self.analyst_firm:
            raise ValueError("analyst_firm is required")
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")

    @property
    def natural_key(self) -> str:
        """Stable id for the forecast EVENT (not for this observation of it)."""
        parts = [
            self.source,
            self.ticker.upper(),
            self.analyst_firm.strip().lower(),
            (self.analyst_name or "").strip().lower(),
            self.action_date.isoformat(),
        ]
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]

    @property
    def payload_hash(self) -> str:
        """Stable id for the CONTENT of this observation."""
        parts = [
            self.natural_key,
            f"{self.price_target:.6f}" if self.price_target is not None else "-",
            f"{self.price_target_prev:.6f}" if self.price_target_prev is not None else "-",
            f"{self.spot_at_action:.6f}" if self.spot_at_action is not None else "-",
            str(self.rating), str(self.rating_prev),
            str(self.fiscal_year_covered), self.currency,
        ]
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]

    @classmethod
    def build(
        cls,
        *,
        ticker: str,
        analyst_firm: str,
        action_date,
        source: str,
        retrieved_at: datetime | None = None,
        analyst_name=None,
        rating=None,
        rating_prev=None,
        price_target=None,
        price_target_prev=None,
        spot_at_action=None,
        fiscal_year_covered=None,
        vendor_published_at=None,
        currency: str = "USD",
        **extra,
    ) -> "PriceTargetRecord":
        """Coerce loosely-typed vendor fields into a validated record."""
        fy = _as_float(fiscal_year_covered)
        return cls(
            ticker=str(ticker).upper().strip(),
            analyst_firm=str(analyst_firm).strip(),
            analyst_name=(str(analyst_name).strip() or None) if analyst_name else None,
            action_date=_as_date(action_date),
            rating=normalise_rating(rating),
            rating_prev=normalise_rating(rating_prev),
            price_target=_as_float(price_target),
            price_target_prev=_as_float(price_target_prev),
            spot_at_action=_as_float(spot_at_action),
            fiscal_year_covered=int(fy) if fy is not None else None,
            source=source,
            retrieved_at=retrieved_at or datetime.now(timezone.utc),
            rating_raw=str(rating) if rating is not None else None,
            rating_prev_raw=str(rating_prev) if rating_prev is not None else None,
            vendor_published_at=vendor_published_at,
            currency=currency,
            extra=extra,
        )


class VendorClient:
    """Base class for a per-analyst price-target source.

    Subclasses implement :meth:`fetch` and return canonical records. They must
    never return a pre-aggregated consensus: consuming a vendor's consensus
    imports that vendor's staleness window and panel-inclusion rules silently,
    which is the failure mode the whole design is built to avoid.
    """

    #: Registry name; also the value written to ``source`` on every record.
    name: str = "base"
    #: False when the client fabricates data. Checked by the store so synthetic
    #: rows can never be silently mixed into a real-data analysis.
    is_real_data: bool = True
    #: True when the client's response parsing has been exercised against a
    #: live API response. False means written-to-documentation but unverified.
    response_shape_verified: bool = False

    def fetch(self, ticker: str, start: date | None = None, end: date | None = None):
        raise NotImplementedError

    def available(self) -> tuple[bool, str]:
        """Return ``(usable, reason)`` -- e.g. missing credentials."""
        return True, "ok"
