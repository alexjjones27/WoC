"""Append-only writers.

Every function here issues INSERTs only. There is intentionally no update path
and no upsert: a vendor restating a historical price target must produce a
second row, because the fact that they restated it is itself information, and
because a backtest that reads the restated value has read the future.

Exact duplicates ARE skipped. That is not an overwrite -- re-running an ingest
must not multiply the panel by the number of times it was run. The test is
(natural_key, payload_hash): identical content for the same event is the same
observation. Different content for the same event is a restatement and is kept.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import duckdb

from ..ingest.base import PriceTargetRecord
from ..ingest.prices import PriceHistory
from .schema import SCHEMA_VERSION, create_schema


def connect(path, read_only: bool = False):
    """Open (and if necessary create) the store."""
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        create_schema(con)
    return con


def start_run(con, ticker: str | None, source: str | None, note: str = "") -> int:
    run_id = con.execute("SELECT nextval('seq_ingest_run')").fetchone()[0]
    con.execute(
        """
        INSERT INTO ingest_run
          (run_id, started_at, ticker, source, schema_version, note)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [run_id, datetime.now(timezone.utc), ticker, source, SCHEMA_VERSION, note],
    )
    return run_id


def finish_run(con, run_id: int, offered: int, inserted: int, duplicate: int, restated: int) -> None:
    con.execute(
        """
        UPDATE ingest_run
           SET finished_at = ?, rows_offered = ?, rows_inserted = ?,
               rows_duplicate = ?, rows_restated = ?
         WHERE run_id = ?
        """,
        [datetime.now(timezone.utc), offered, inserted, duplicate, restated, run_id],
    )


def append_price_targets(
    con, records: list[PriceTargetRecord], run_id: int | None = None
) -> dict:
    """Insert records, skipping byte-identical repeats. Returns a count summary.

    ``restated`` counts rows that describe an event already in the store but
    with different content -- the number worth watching, since a vendor that
    restates often is a vendor whose history cannot be backtested naively.
    """
    if not records:
        return {"offered": 0, "inserted": 0, "duplicate": 0, "restated": 0}

    seen = con.execute(
        "SELECT natural_key, payload_hash FROM price_target_raw"
    ).fetchall()
    seen_pairs = {(nk, ph) for nk, ph in seen}
    seen_keys = {nk for nk, _ in seen}

    rows, inserted, duplicate, restated = [], 0, 0, 0
    for rec in records:
        nk, ph = rec.natural_key, rec.payload_hash
        if (nk, ph) in seen_pairs:
            duplicate += 1
            continue
        if nk in seen_keys:
            restated += 1
        seen_pairs.add((nk, ph))
        seen_keys.add(nk)
        inserted += 1
        rows.append(
            (
                nk, ph, rec.ticker, rec.analyst_firm, rec.analyst_name,
                rec.action_date, rec.rating, rec.rating_prev, rec.rating_raw,
                rec.rating_prev_raw, rec.price_target, rec.price_target_prev,
                rec.spot_at_action, rec.fiscal_year_covered, rec.currency,
                rec.source, bool(rec.extra.get("synthetic", False)),
                rec.vendor_published_at, rec.retrieved_at, run_id,
                json.dumps(rec.extra, default=str) if rec.extra else None,
            )
        )

    if rows:
        con.executemany(
            """
            INSERT INTO price_target_raw (
                row_id, natural_key, payload_hash, ticker, analyst_firm,
                analyst_name, action_date, rating, rating_prev, rating_raw,
                rating_prev_raw, price_target, price_target_prev,
                spot_at_action, fiscal_year_covered, currency, source,
                is_synthetic, vendor_published_at, retrieved_at, ingest_run_id,
                extra_json
            ) VALUES (
                nextval('seq_price_target_raw'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            rows,
        )
    return {
        "offered": len(records),
        "inserted": inserted,
        "duplicate": duplicate,
        "restated": restated,
    }


def append_price_history(con, history: PriceHistory, source: str = "yahoo") -> dict:
    """Insert daily closes and split events for one symbol.

    Prices are append-only too. A vendor silently re-adjusting a historical
    close (which happens on every split and every dividend restatement) would
    otherwise change the denominator of implied returns computed months ago.
    """
    existing = {
        d for (d,) in con.execute(
            "SELECT price_date FROM price_daily WHERE symbol = ? AND source = ?",
            [history.symbol, source],
        ).fetchall()
    }
    rows = [
        (history.symbol, d, float(c), source, history.retrieved_at)
        for d, c in zip(history.dates, history.closes)
        if d not in existing and c == c  # NaN-safe
    ]
    if rows:
        con.executemany(
            "INSERT INTO price_daily (symbol, price_date, close_adj, source, retrieved_at)"
            " VALUES (?, ?, ?, ?, ?)",
            rows,
        )

    have = {
        d for (d,) in con.execute(
            "SELECT effective FROM split_event WHERE symbol = ?", [history.symbol]
        ).fetchall()
    }
    split_rows = [
        (history.symbol, s.effective, s.ratio, source, history.retrieved_at)
        for s in history.splits
        if s.effective not in have
    ]
    if split_rows:
        con.executemany(
            "INSERT INTO split_event (symbol, effective, ratio, source, retrieved_at)"
            " VALUES (?, ?, ?, ?, ?)",
            split_rows,
        )
    return {"prices_inserted": len(rows), "splits_inserted": len(split_rows)}


def append_synthetic_truth(con, ticker: str, truth) -> int:
    """Record the generative parameters behind a simulated panel."""
    now = datetime.now(timezone.utc)
    con.execute("DELETE FROM synthetic_truth WHERE ticker = ? AND seed = ?",
                [ticker.upper(), int(truth.seed)])
    rows = [
        (
            ticker.upper(), int(truth.seed), r["analyst_firm"], float(r["mu_f"]),
            float(r["sigma_f"]), float(r["phi_f"]), float(r["alpha_f"]),
            float(r["street_load"]), int(r["cluster"]), now,
        )
        for _, r in truth.firms.iterrows()
    ]
    con.executemany(
        "INSERT INTO synthetic_truth (ticker, seed, analyst_firm, mu_f, sigma_f,"
        " phi_f, alpha_f, street_load, cluster, retrieved_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def append_consensus(con, snapshot) -> None:
    con.execute(
        "INSERT INTO consensus_snapshot (ticker, as_of, target_mean, target_median,"
        " target_high, target_low, n_analysts, source, retrieved_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            snapshot.ticker, snapshot.as_of, snapshot.target_mean,
            snapshot.target_median, snapshot.target_high, snapshot.target_low,
            snapshot.n_analysts, snapshot.source, snapshot.retrieved_at,
        ],
    )
