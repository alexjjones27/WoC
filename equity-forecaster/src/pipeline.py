"""Wiring: ingest -> append-only store -> point-in-time read -> Stage 2 de-bias.

Kept separate from the CLI so the same sequence can be driven from a notebook
or a test without shelling out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pandas as pd

from . import config
from .ingest import synthetic
from .ingest.base import VendorClient
from .ingest.benzinga import BenzingaClient
from .ingest.finviz import FinvizClient
from .ingest.fmp import FMPClient
from .ingest.nasdaq import NasdaqConsensusClient
from .ingest.prices import fetch_price_history
from .ingest.stockanalysis import StockAnalysisClient
from .model.debias import DebiasResult, attach_implied_returns, debias
from .store import pit, writer

#: Sources that can supply PER-ANALYST records, best first.
#:
#: Finnhub and Nasdaq are deliberately absent: both publish only aggregated
#: consensus, which the design forbids as an input. They are ingested separately
#: as benchmarks (see CONSENSUS_CLIENTS).
#:
#: finviz and stockanalysis need no credential and complement each other --
#: finviz reaches back about sixteen months with firm-level records, while
#: stockanalysis returns only the eight most recent actions but names the
#: individual analyst. Running both is also what makes the data-quality gate's
#: cross-vendor disagreement check measurable at all.
PANEL_CLIENTS: dict[str, type[VendorClient]] = {
    "benzinga": BenzingaClient,
    "fmp": FMPClient,
    "finviz": FinvizClient,
    "stockanalysis": StockAnalysisClient,
}

#: Aggregated sources, stored only so Stage 8's consensus test has something to
#: beat. Never read as a panel input.
CONSENSUS_CLIENTS = {"nasdaq": NasdaqConsensusClient}


@dataclass
class IngestSummary:
    ticker: str
    price_rows: int
    split_rows: int
    sources_used: list[str]
    sources_skipped: dict[str, str]
    targets: dict
    synthetic: bool
    history_start: date | None
    history_end: date | None
    consensus_rows: int = 0
    errors: dict = field(default_factory=dict)


def available_panel_sources() -> dict[str, tuple[bool, str]]:
    """Which real per-analyst sources are usable right now, and why not."""
    out = {}
    for name, cls in PANEL_CLIENTS.items():
        out[name] = cls().available()
    return out


def ingest_ticker(
    con,
    ticker: str,
    *,
    start: date,
    end: date | None = None,
    sources: list[str] | None = None,
    allow_synthetic: bool = False,
    synthetic_seed: int | None = None,
    fetch_sector: bool = True,
) -> IngestSummary:
    """Pull prices and the analyst panel for one ticker into the store.

    Real vendors are tried first. The synthetic generator runs ONLY when no
    real source is usable AND the caller has explicitly allowed it, so a
    simulated panel can never appear in place of a failed real pull that the
    caller thought had succeeded.
    """
    ticker = ticker.upper()
    end = end or datetime.now(timezone.utc).date()

    history = fetch_price_history(ticker, start, end)
    price_stats = writer.append_price_history(con, history)

    if fetch_sector:
        etf, _ = config.sector_etf(ticker)
        try:
            sector_hist = fetch_price_history(etf, start, end)
            writer.append_price_history(con, sector_hist)
        except Exception as exc:  # sector data is a nice-to-have, not fatal
            print(f"  warning: sector proxy {etf} unavailable ({exc})")

    wanted = sources or list(PANEL_CLIENTS)
    used: list[str] = []
    skipped: dict[str, str] = {}
    errors: dict[str, str] = {}
    totals = {"offered": 0, "inserted": 0, "duplicate": 0, "restated": 0}

    for name in wanted:
        cls = PANEL_CLIENTS.get(name)
        if cls is None:
            skipped[name] = "not a per-analyst source"
            continue
        client = cls()
        ok, reason = client.available()
        if not ok:
            skipped[name] = reason
            continue
        run_id = writer.start_run(con, ticker, name)
        try:
            records = client.fetch(ticker, start, end)
        except Exception as exc:
            # One source failing must not lose the others' rows, but it must not
            # be silent either: a quietly-empty panel looks identical to a stock
            # nobody covers.
            errors[name] = f"{type(exc).__name__}: {exc}"
            writer.finish_run(con, run_id, 0, 0, 0, 0)
            continue
        stats = writer.append_price_targets(con, records, run_id)
        writer.finish_run(con, run_id, stats["offered"], stats["inserted"],
                          stats["duplicate"], stats["restated"])
        for k in totals:
            totals[k] += stats[k]
        used.append(name)

    is_synth = False
    if not used:
        if not allow_synthetic:
            raise RuntimeError(
                "no real per-analyst source is usable "
                f"({skipped}); re-run with allow_synthetic=True to exercise the "
                "pipeline on a SIMULATED panel, understanding that nothing it "
                "produces is evidence about any real stock"
            )
        records, truth = synthetic.generate_panel(
            ticker, history, seed=synthetic_seed, end=end
        )
        run_id = writer.start_run(con, ticker, synthetic.SOURCE, note="SIMULATED DATA")
        stats = writer.append_price_targets(con, records, run_id)
        writer.finish_run(con, run_id, stats["offered"], stats["inserted"],
                          stats["duplicate"], stats["restated"])
        writer.append_synthetic_truth(con, ticker, truth)
        for k in totals:
            totals[k] += stats[k]
        used.append(synthetic.SOURCE)
        is_synth = True

    # Consensus benchmarks. Stored in their own table, never in the panel.
    consensus_rows = 0
    for name, cls in CONSENSUS_CLIENTS.items():
        client = cls()
        ok, _ = client.available()
        if not ok:
            continue
        try:
            current, history_points = client.fetch_consensus(ticker)
        except Exception as exc:
            errors[name] = f"{type(exc).__name__}: {exc}"
            continue
        for snap in [current, *history_points]:
            writer.append_consensus(con, snap)
            consensus_rows += 1

    return IngestSummary(
        ticker=ticker,
        price_rows=price_stats["prices_inserted"],
        split_rows=price_stats["splits_inserted"],
        sources_used=used,
        sources_skipped=skipped,
        targets=totals,
        synthetic=is_synth,
        history_start=history.start,
        history_end=history.end,
        consensus_rows=consensus_rows,
        errors=errors,
    )


def build_coverage_panel(
    con,
    asof: date,
    *,
    pit_mode: str = pit.STRICT,
    include_synthetic: bool | None = None,
    tickers: list[str] | None = None,
):
    """Implied returns for EVERY covered ticker, for cross-ticker firm offsets.

    The brief says a firm's anchoring offset should be estimated "across all
    coverage", and the reason is not thoroughness. On a single ticker a firm's
    habitual multiple of spot cannot be separated from that firm's genuine view
    on that one stock, so subtracting it removes signal along with bias. Across
    dozens of names the firm effect is what survives averaging over stocks, and
    the stock-specific view is what does not.

    Each ticker's implied returns are computed against ITS OWN point-in-time
    price history, then concatenated. Returns an empty frame if nothing is
    visible.
    """
    names = tickers or pit.tickers_with_panel(con, asof, pit_mode=pit_mode)
    frames = []
    for name in names:
        try:
            panel = pit.price_targets_asof(
                con, name, asof, pit_mode=pit_mode,
                include_synthetic=include_synthetic,
            )
        except ValueError:
            continue           # mixed real/synthetic for this ticker; skip it
        if panel.empty:
            continue
        history = pit.price_history_asof(con, name, asof)
        if len(history) == 0:
            continue
        frames.append(attach_implied_returns(panel, history))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def run_debias(
    con,
    ticker: str,
    asof: date,
    *,
    pit_mode: str = pit.STRICT,
    include_synthetic: bool | None = None,
    fit_decay: bool = True,
    cross_ticker_offsets: bool = True,
) -> DebiasResult:
    """Read the panel point-in-time and run Stage 2 on it.

    ``cross_ticker_offsets`` estimates the firm anchoring offsets over the whole
    covered universe in the store rather than over this ticker alone. On by
    default because the single-ticker estimate conflates a firm's bias with its
    view on the stock; turn it off only to reproduce a single-ticker result.
    """
    ticker = ticker.upper()
    panel = pit.price_targets_asof(
        con, ticker, asof, pit_mode=pit_mode, include_synthetic=include_synthetic
    )
    if panel.empty:
        raise RuntimeError(
            f"no visible records for {ticker} as of {asof} under pit_mode="
            f"{pit_mode!r}. Under 'strict' this is the expected result when the "
            "store holds a single snapshot pulled after the as-of date."
        )
    history = pit.price_history_asof(con, ticker, asof)
    etf, is_real = config.sector_etf(ticker)
    sector = pit.price_history_asof(con, etf, asof)
    if len(sector) == 0:
        sector = None

    coverage = None
    if cross_ticker_offsets:
        coverage = build_coverage_panel(
            con, asof, pit_mode=pit_mode, include_synthetic=include_synthetic
        )
        if coverage.empty:
            coverage = None

    return debias(
        panel, history, sector, ticker=ticker, asof=asof,
        sector_symbol=etf, is_real_sector=is_real, fit_decay=fit_decay,
        coverage_panel=coverage,
    )
