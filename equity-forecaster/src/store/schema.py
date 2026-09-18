"""DuckDB schema. Append-only by construction, not by convention.

The brief's rule -- "never overwrite a historical record when a vendor restates
it; insert a new row and keep both, because restatement is itself look-ahead
contamination" -- is enforced here rather than left to discipline:

* no table has a primary key that a restatement would collide with;
* the writers in ``writer.py`` issue only INSERTs, never UPDATE or DELETE;
* ``retrieved_at`` is NOT NULL everywhere, so a row with no provenance cannot
  physically exist;
* the point-in-time reader resolves duplicates by taking the EARLIEST retrieval
  of each forecast event, so the original value is what a backtest sees.

Tables
------
``price_target_raw``  every observation of every analyst action, as pulled.
``price_daily``       split-adjusted daily closes, per symbol, as pulled.
``split_event``       corporate actions, needed to reconcile as-quoted targets.
``consensus_snapshot``vendor consensus, stored ONLY as a benchmark to beat.
``synthetic_truth``   generative parameters when the panel is simulated, so a
                      test can check recovery and a reader can see at a glance
                      that the ticker's panel is not real.
``ingest_run``        one row per ingest invocation: what was asked for, what
                      came back, which code version wrote it.
"""
from __future__ import annotations

SCHEMA_VERSION = 1

DDL = [
    "CREATE SEQUENCE IF NOT EXISTS seq_price_target_raw START 1",
    "CREATE SEQUENCE IF NOT EXISTS seq_ingest_run START 1",
    """
    CREATE TABLE IF NOT EXISTS price_target_raw (
        row_id              BIGINT      NOT NULL,
        natural_key         VARCHAR     NOT NULL,
        payload_hash        VARCHAR     NOT NULL,
        ticker              VARCHAR     NOT NULL,
        analyst_firm        VARCHAR     NOT NULL,
        analyst_name        VARCHAR,
        action_date         DATE        NOT NULL,
        rating              INTEGER,
        rating_prev         INTEGER,
        rating_raw          VARCHAR,
        rating_prev_raw     VARCHAR,
        price_target        DOUBLE,
        price_target_prev   DOUBLE,
        spot_at_action      DOUBLE,      -- the VENDOR's claim; verified, not trusted
        fiscal_year_covered INTEGER,
        currency            VARCHAR      NOT NULL DEFAULT 'USD',
        source              VARCHAR      NOT NULL,
        is_synthetic        BOOLEAN      NOT NULL DEFAULT FALSE,
        vendor_published_at TIMESTAMPTZ,
        retrieved_at        TIMESTAMPTZ  NOT NULL,
        ingest_run_id       BIGINT,
        extra_json          VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS price_daily (
        symbol        VARCHAR     NOT NULL,
        price_date    DATE        NOT NULL,
        close_adj     DOUBLE,      -- split-adjusted close; dividends NOT applied
        source        VARCHAR     NOT NULL,
        retrieved_at  TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS split_event (
        symbol        VARCHAR     NOT NULL,
        effective     DATE        NOT NULL,
        ratio         DOUBLE      NOT NULL,
        source        VARCHAR     NOT NULL,
        retrieved_at  TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS consensus_snapshot (
        ticker         VARCHAR     NOT NULL,
        as_of          DATE        NOT NULL,
        target_mean    DOUBLE,
        target_median  DOUBLE,
        target_high    DOUBLE,
        target_low     DOUBLE,
        n_analysts     INTEGER,
        source         VARCHAR     NOT NULL,
        retrieved_at   TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS synthetic_truth (
        ticker        VARCHAR     NOT NULL,
        seed          BIGINT      NOT NULL,
        analyst_firm  VARCHAR     NOT NULL,
        mu_f          DOUBLE,
        sigma_f       DOUBLE,
        phi_f         DOUBLE,
        alpha_f       DOUBLE,
        street_load   DOUBLE,
        cluster       INTEGER,
        retrieved_at  TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingest_run (
        run_id          BIGINT      NOT NULL,
        started_at      TIMESTAMPTZ NOT NULL,
        finished_at     TIMESTAMPTZ,
        ticker          VARCHAR,
        source          VARCHAR,
        rows_offered    BIGINT,
        rows_inserted   BIGINT,
        rows_duplicate  BIGINT,
        rows_restated   BIGINT,
        schema_version  INTEGER,
        note            VARCHAR
    )
    """,
    # Indexes are advisory in DuckDB but keep the PIT scans honest on bigger panels.
    "CREATE INDEX IF NOT EXISTS ix_ptr_ticker ON price_target_raw (ticker)",
    "CREATE INDEX IF NOT EXISTS ix_ptr_natkey ON price_target_raw (natural_key)",
    "CREATE INDEX IF NOT EXISTS ix_price_symbol ON price_daily (symbol)",
]


def create_schema(con) -> None:
    """Create all tables and sequences if absent. Safe to call repeatedly."""
    for stmt in DDL:
        con.execute(stmt)
