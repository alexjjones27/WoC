"""Command line entry point.

    python -m src.cli sources
    python -m src.cli ingest AAPL --allow-synthetic
    python -m src.cli quality AAPL
    python -m src.cli anchoring AAPL
    python -m src.cli debias AAPL
    python -m src.cli demo AAPL          # ingest + quality + debias, one shot
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta

from . import config, report
from .pipeline import available_panel_sources, ingest_ticker, run_debias
from .store import pit, writer


def _parse_date(s: str | None, default: date) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date() if s else default


def _open(args, read_only: bool = False):
    return writer.connect(args.db, read_only=read_only)


def cmd_sources(args) -> int:
    print("per-analyst panel sources:")
    any_ok = False
    for name, (ok, reason) in available_panel_sources().items():
        print(f"  {name:<12} {'AVAILABLE' if ok else 'unavailable'}   {reason}")
        any_ok |= ok
    print("\nconsensus-only sources (benchmark, never an input):")
    from .ingest.finnhub import FinnhubConsensusClient

    ok, reason = FinnhubConsensusClient().available()
    print(f"  {'finnhub':<12} {'AVAILABLE' if ok else 'unavailable'}   {reason}")
    print("\nprice / corporate actions:")
    print(f"  {'yahoo':<12} AVAILABLE    no credential required")
    if not any_ok:
        print("\nNo real per-analyst source is configured. `ingest --allow-synthetic`")
        print("runs the pipeline on a SIMULATED panel; its output is not evidence.")
    return 0


def cmd_ingest(args) -> int:
    con = _open(args)
    start = _parse_date(args.start, date.today() - timedelta(days=365 * 8))
    end = _parse_date(args.end, date.today())
    for ticker in args.tickers:
        print(f"\ningesting {ticker.upper()} ({start} .. {end})")
        s = ingest_ticker(
            con, ticker, start=start, end=end,
            sources=args.sources, allow_synthetic=args.allow_synthetic,
            synthetic_seed=args.seed,
        )
        print(f"  prices     {s.price_rows:>7} rows, {s.split_rows} split events, "
              f"{s.history_start} .. {s.history_end}")
        print(f"  targets    offered {s.targets['offered']}, inserted "
              f"{s.targets['inserted']}, duplicate {s.targets['duplicate']}, "
              f"restated {s.targets['restated']}")
        print(f"  sources    used: {', '.join(s.sources_used)}")
        for name, why in s.sources_skipped.items():
            print(f"             skipped {name}: {why}")
        if s.synthetic:
            print("  *** SIMULATED PANEL -- output is not evidence about this stock ***")
    con.close()
    return 0


def cmd_quality(args) -> int:
    from .diagnostics.data_quality import render_quality_report, run_quality_checks

    con = _open(args, read_only=True)
    asof = _parse_date(args.asof, date.today())
    rep = run_quality_checks(con, args.ticker, asof, pit_mode=args.pit_mode)
    print(render_quality_report(rep))
    con.close()
    return 0 if rep.passed else 1


def cmd_anchoring(args) -> int:
    from .diagnostics.anchoring import render_anchoring_report, run_anchoring_study

    con = _open(args, read_only=True)
    asof = _parse_date(args.asof, date.today())
    study = run_anchoring_study(con, args.ticker, asof, pit_mode=args.pit_mode)
    print(render_anchoring_report(study))
    con.close()
    return 0


def cmd_debias(args) -> int:
    con = _open(args, read_only=True)
    asof = _parse_date(args.asof, date.today())
    result = run_debias(con, args.ticker, asof, pit_mode=args.pit_mode,
                        fit_decay=not args.no_decay_fit)
    print(report.render(result))
    if args.csv:
        result.panel.to_csv(args.csv, index=False)
        print(f"\nper-record output written to {args.csv}")
    con.close()
    return 0


def cmd_demo(args) -> int:
    from .diagnostics.anchoring import render_anchoring_report, run_anchoring_study
    from .diagnostics.data_quality import render_quality_report, run_quality_checks

    con = _open(args)
    ticker = args.ticker.upper()
    start = _parse_date(args.start, date.today() - timedelta(days=365 * 8))
    asof = _parse_date(args.asof, date.today())

    print(f"[1/4] ingest {ticker}")
    s = ingest_ticker(con, ticker, start=start, end=asof,
                      allow_synthetic=args.allow_synthetic, synthetic_seed=args.seed)
    print(f"      sources used: {', '.join(s.sources_used)}; "
          f"{s.targets['inserted']} rows inserted")
    mode = pit.ASSUME_VENDOR_HISTORY if s.synthetic else args.pit_mode

    print("\n[2/4] data quality")
    q = run_quality_checks(con, ticker, asof, pit_mode=mode)
    print(render_quality_report(q))

    print("\n[3/4] anchoring evidence")
    print(render_anchoring_report(run_anchoring_study(con, ticker, asof, pit_mode=mode)))

    print("\n[4/4] Stage 2 de-biasing")
    if not q.passed:
        print("  data-quality gate FAILED. The brief says not to build the composite")
        print("  signal on a panel that has not passed data quality; the Stage 2")
        print("  distribution below is printed as a diagnostic only.")
    print(report.render(run_debias(con, ticker, asof, pit_mode=mode)))
    con.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="equity-forecaster",
        description="Crowd-aggregated equity price forecaster (Stages 1-2).",
    )
    p.add_argument("--db", default=str(config.DB_PATH), help="DuckDB store path")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("sources", help="which data sources are usable")
    sp.set_defaults(func=cmd_sources)

    sp = sub.add_parser("ingest", help="pull prices and analyst panel into the store")
    sp.add_argument("tickers", nargs="+")
    sp.add_argument("--start")
    sp.add_argument("--end")
    sp.add_argument("--sources", nargs="*")
    sp.add_argument("--allow-synthetic", action="store_true",
                    help="fall back to a SIMULATED panel when no vendor key exists")
    sp.add_argument("--seed", type=int)
    sp.set_defaults(func=cmd_ingest)

    for name, fn, helptext in (
        ("quality", cmd_quality, "data-quality report (notebook 01)"),
        ("anchoring", cmd_anchoring, "anchoring evidence (notebook 02)"),
        ("debias", cmd_debias, "Stage 2: raw vs de-biased distribution"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("ticker")
        sp.add_argument("--asof")
        sp.add_argument("--pit-mode", default=pit.STRICT, choices=list(pit.PIT_MODES))
        if name == "debias":
            sp.add_argument("--csv", help="write the per-record frame here")
            sp.add_argument("--no-decay-fit", action="store_true")
        sp.set_defaults(func=fn)

    sp = sub.add_parser("demo", help="ingest + quality + anchoring + de-bias, one ticker")
    sp.add_argument("ticker")
    sp.add_argument("--start")
    sp.add_argument("--asof")
    sp.add_argument("--seed", type=int)
    sp.add_argument("--allow-synthetic", action="store_true")
    sp.add_argument("--pit-mode", default=pit.STRICT, choices=list(pit.PIT_MODES))
    sp.set_defaults(func=cmd_demo)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
