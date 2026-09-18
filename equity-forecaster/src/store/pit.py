"""Point-in-time query layer.

Reading this store as-of a date is the whole anti-look-ahead apparatus, so the
rules are stated here rather than scattered through the models.

**Rule 1 -- original publication wins.** A forecast event can have several rows
(an original and one or more vendor restatements). Every read resolves an event
to its EARLIEST-retrieved row. The restated value is never returned, only
counted.

**Rule 2 -- nothing dated after the as-of date.** ``action_date <= asof``, always.

**Rule 3 -- and nothing KNOWN after the as-of date.** ``retrieved_at <= asof``.

Rule 3 is the one that bites, and pretending otherwise is how backtests get
manufactured. If the panel was pulled in a single snapshot today, then every
row's ``retrieved_at`` is today, and a strict read as of any past date returns
NOTHING -- which is the truthful answer: a single snapshot of a vendor's
back-history is not evidence about what was knowable in 2022.

Backtesting from one snapshot therefore requires an explicit assumption, and
this module makes you name it:

``pit_mode="strict"``
    All three rules. The only mode whose output is safe to publish as evidence.

``pit_mode="assume_vendor_history"``
    Rules 1 and 2 only. Asserts that the vendor's back-history equals what was
    actually published at the time -- no silent deletions of analysts who left,
    no back-filled coverage, no restated targets. This assumption is usually
    FALSE in some degree and is the single most common source of phantom alpha
    in this kind of research. It is never the default, it is stamped onto every
    frame it produces, and every report that consumes such a frame prints it.
"""
from __future__ import annotations

from datetime import datetime, time, timezone

import pandas as pd

from ..ingest.base import canonical_firm
from ..ingest.prices import PriceHistory, Split

STRICT = "strict"
ASSUME_VENDOR_HISTORY = "assume_vendor_history"
PIT_MODES = (STRICT, ASSUME_VENDOR_HISTORY)


def _asof_ts(asof) -> datetime:
    if isinstance(asof, datetime):
        return asof if asof.tzinfo else asof.replace(tzinfo=timezone.utc)
    return datetime.combine(asof, time(23, 59, 59), tzinfo=timezone.utc)


def price_targets_asof(
    con,
    ticker: str,
    asof,
    *,
    pit_mode: str = STRICT,
    sources: list[str] | None = None,
    include_synthetic: bool | None = None,
) -> pd.DataFrame:
    """Return the panel of analyst actions visible for ``ticker`` at ``asof``.

    One row per forecast event, carrying the ORIGINALLY published values plus
    ``n_observations`` and ``was_restated`` so that vendor revisionism stays
    visible instead of being silently resolved away.

    ``include_synthetic``: None means "infer" -- if the store holds only
    simulated rows for this ticker, they are returned and the frame is flagged.
    Real and synthetic rows are never blended: asking for a mixed read raises.
    """
    if pit_mode not in PIT_MODES:
        raise ValueError(f"pit_mode must be one of {PIT_MODES}, got {pit_mode!r}")
    asof_date = asof.date() if isinstance(asof, datetime) else asof
    ts = _asof_ts(asof)

    where = ["ticker = ?", "action_date <= ?"]
    params: list = [ticker.upper(), asof_date]
    if pit_mode == STRICT:
        where.append("retrieved_at <= ?")
        params.append(ts)
    if sources:
        where.append(f"source IN ({','.join('?' * len(sources))})")
        params.extend(sources)

    sql = f"""
        WITH visible AS (
            SELECT * FROM price_target_raw WHERE {' AND '.join(where)}
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY natural_key
                       ORDER BY retrieved_at ASC, row_id ASC
                   ) AS rn,
                   COUNT(*)              OVER (PARTITION BY natural_key) AS n_observations,
                   COUNT(DISTINCT payload_hash)
                                         OVER (PARTITION BY natural_key) AS n_versions
              FROM visible
        )
        SELECT natural_key, ticker, analyst_firm, analyst_name, action_date,
               rating, rating_prev, rating_raw, price_target, price_target_prev,
               spot_at_action AS vendor_spot_at_action, fiscal_year_covered,
               currency, source, is_synthetic, vendor_published_at,
               retrieved_at, n_observations,
               (n_versions > 1) AS was_restated
          FROM ranked
         WHERE rn = 1
         ORDER BY action_date, analyst_firm
    """
    df = con.execute(sql, params).df()

    if not df.empty:
        # Normalise the event date to datetime.date at the boundary. DuckDB
        # hands back pandas Timestamps, and a panel where action_date is
        # sometimes a Timestamp and sometimes a date is a standing invitation
        # for a comparison to silently do the wrong thing downstream.
        df["action_date"] = pd.to_datetime(df["action_date"]).dt.date
        # Derived at read time, never stored: see base.canonical_firm. The store
        # keeps what each vendor published; this is our opinion about which
        # spellings are the same firm, and opinions belong in the read path.
        df["analyst_firm_canonical"] = df["analyst_firm"].map(canonical_firm)
        n_syn = int(df["is_synthetic"].sum())
        if 0 < n_syn < len(df) and include_synthetic is None:
            raise ValueError(
                f"{ticker}: store holds {n_syn} synthetic and {len(df) - n_syn} real "
                "rows. Refusing to blend simulated and real data -- pass "
                "include_synthetic=True/False to choose explicitly."
            )
        if include_synthetic is True:
            df = df[df["is_synthetic"]]
        elif include_synthetic is False:
            df = df[~df["is_synthetic"]]

    df.attrs.update(
        {
            "ticker": ticker.upper(),
            "asof": asof_date,
            "pit_mode": pit_mode,
            "is_synthetic": bool(df["is_synthetic"].all()) if len(df) else False,
            "sources": sorted(df["source"].unique().tolist()) if len(df) else [],
        }
    )
    return df


def price_history_asof(con, symbol: str, asof, source: str = "yahoo") -> PriceHistory:
    """Rebuild a :class:`PriceHistory` from the store, truncated at ``asof``.

    Prices are truncated at ``asof``; splits deliberately are NOT.

    That asymmetry is the correct one, and getting it backwards is a real bug.
    The stored close series is Yahoo's BACK-ADJUSTED series: one vintage,
    expressed end to end in today's share units. A back-adjusted price is not a
    point-in-time observable at all -- it is a re-expression of history -- so
    truncating it by date changes which days you can see but never changes the
    units. The split list is used for exactly one thing: converting a vendor's
    as-quoted price target into the same units as that series. Dropping a split
    dated after ``asof`` would leave a pre-split target being divided by a
    post-split price, i.e. an implied return wrong by the split ratio.

    No look-ahead is introduced, because the conversion is a pure change of
    units: every ratio the pipeline computes (implied return, realised return)
    is invariant to it. A split is never used as a feature.
    """
    asof_date = asof.date() if isinstance(asof, datetime) else asof
    rows = con.execute(
        "SELECT price_date, close_adj, retrieved_at FROM price_daily"
        " WHERE symbol = ? AND source = ? AND price_date <= ? ORDER BY price_date",
        [symbol.upper(), source, asof_date],
    ).fetchall()
    splits = con.execute(
        "SELECT effective, ratio FROM split_event WHERE symbol = ? ORDER BY effective",
        [symbol.upper()],
    ).fetchall()
    return PriceHistory(
        symbol,
        [r[0] for r in rows],
        [r[1] for r in rows],
        [Split(effective=s[0], ratio=float(s[1])) for s in splits],
        retrieved_at=max((r[2] for r in rows), default=None),
    )


def provenance(con, ticker: str) -> dict:
    """What is actually in the store for this ticker, and where it came from."""
    row = con.execute(
        """
        SELECT COUNT(*) AS n_rows,
               COUNT(DISTINCT natural_key) AS n_events,
               COUNT(DISTINCT analyst_firm) AS n_firms,
               MIN(action_date) AS first_action,
               MAX(action_date) AS last_action,
               MIN(retrieved_at) AS first_pull,
               MAX(retrieved_at) AS last_pull,
               SUM(CASE WHEN is_synthetic THEN 1 ELSE 0 END) AS n_synthetic
          FROM price_target_raw WHERE ticker = ?
        """,
        [ticker.upper()],
    ).df()
    out = {} if row.empty else row.iloc[0].to_dict()
    out["sources"] = [
        r[0] for r in con.execute(
            "SELECT DISTINCT source FROM price_target_raw WHERE ticker = ? ORDER BY 1",
            [ticker.upper()],
        ).fetchall()
    ]
    out["n_restated_events"] = con.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT natural_key FROM price_target_raw WHERE ticker = ?
             GROUP BY natural_key HAVING COUNT(DISTINCT payload_hash) > 1
        )
        """,
        [ticker.upper()],
    ).fetchone()[0]
    return out


def distinct_tickers(con) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT ticker FROM price_target_raw ORDER BY 1"
    ).fetchall()]


def tickers_with_panel(con, asof, *, pit_mode: str = STRICT) -> list[str]:
    """Tickers holding at least one visible forecast event at ``asof``."""
    asof_date = asof.date() if isinstance(asof, datetime) else asof
    where = ["action_date <= ?"]
    params: list = [asof_date]
    if pit_mode == STRICT:
        where.append("retrieved_at <= ?")
        params.append(_asof_ts(asof))
    return [
        r[0] for r in con.execute(
            f"SELECT DISTINCT ticker FROM price_target_raw"
            f" WHERE {' AND '.join(where)} ORDER BY 1", params
        ).fetchall()
    ]
