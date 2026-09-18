"""Wiring: ingest -> append-only store -> point-in-time read -> Stage 2 de-bias.

Kept separate from the CLI so the same sequence can be driven from a notebook
or a test without shelling out.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from . import config
from .ingest import synthetic
from .ingest.base import VendorClient
from .ingest.benzinga import BenzingaClient
from .ingest.fmp import FMPClient
from .ingest.prices import fetch_price_history
from .model.debias import DebiasResult, debias
from .store import pit, writer

#: Sources that can supply PER-ANALYST records. Finnhub is deliberately absent:
#: it publishes only aggregated consensus, which the design forbids as an input.
PANEL_CLIENTS: dict[str, type[VendorClient]] = {
    "benzinga": BenzingaClient,
    "fmp": FMPClient,
}


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
        records = client.fetch(ticker, start, end)
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
    )


def run_debias(
    con,
    ticker: str,
    asof: date,
    *,
    pit_mode: str = pit.STRICT,
    include_synthetic: bool | None = None,
    fit_decay: bool = True,
) -> DebiasResult:
    """Read the panel point-in-time and run Stage 2 on it."""
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

    result = debias(
        panel, history, sector, ticker=ticker, asof=asof,
        sector_symbol=etf, is_real_sector=is_real, fit_decay=fit_decay,
    )
    return result
