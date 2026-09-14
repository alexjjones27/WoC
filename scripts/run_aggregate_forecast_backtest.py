"""Run the aggregate-vs-single-source backtest (see
src/aggregate_forecast_backtest.py for the method and for why the test
window is the final hour before resolution).

Answers the question the dashboard is built on and had never been measured:
does the volume-weighted mixture of Polymarket and Kalshi forecast BTC
better than either platform on its own?
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import aggregate_forecast_backtest as ab  # noqa: E402


def main():
    today = datetime.now(timezone.utc).date()
    max_dates = int(sys.argv[1]) if len(sys.argv) > 1 else None

    print(f"Running aggregate backtest up to {today}"
          + (f" (last {max_dates} dates)" if max_dates else " (all resolved dates)"))
    results, meta = ab.run_backtest(today, max_dates=max_dates)
    if not results:
        print("No scoreable (date, lead) pairs -- aborting.")
        return

    print("\n" + json.dumps(meta, indent=2, default=str))
    summary = ab.summarize(results)

    print(f"\n{'lead':>6}  {'n':>4}  " + "  ".join(f"{n:>13}" for n in ab.FORECAST_NAMES))
    print(f"{'':6}  {'':4}  " + "  ".join(f"{'CRPS ($)':>13}" for _ in ab.FORECAST_NAMES))
    for e in summary:
        cells = []
        for n in ab.FORECAST_NAMES:
            f = e["forecasts"].get(n)
            cells.append(f"{f['crps']:>13,.0f}" if f else f"{'--':>13}")
        print(f"{e['lead_minutes']:>4}m  {e['n_dates']:>4}  " + "  ".join(cells))

    print(f"\n{'lead':>6}  " + "  ".join(f"{n:>13}" for n in ab.FORECAST_NAMES))
    print(f"{'':6}  " + "  ".join(f"{'MAD mean ($)':>13}" for _ in ab.FORECAST_NAMES))
    for e in summary:
        cells = []
        for n in ab.FORECAST_NAMES:
            f = e["forecasts"].get(n)
            cells.append(f"{f['mad_mean']:>13,.0f}" if f else f"{'--':>13}")
        print(f"{e['lead_minutes']:>4}m  " + "  ".join(cells))

    print(f"\n{'lead':>6}  " + "  ".join(f"{n:>13}" for n in ab.FORECAST_NAMES))
    print(f"{'':6}  " + "  ".join(f"{'68% coverage':>13}" for _ in ab.FORECAST_NAMES))
    for e in summary:
        cells = []
        for n in ab.FORECAST_NAMES:
            f = e["forecasts"].get(n)
            cells.append(f"{f['ci68_coverage']:>12.1%} " if f else f"{'--':>13}")
        print(f"{e['lead_minutes']:>4}m  " + "  ".join(cells))

    # The actual thesis under test.
    print("\nDoes combining help? (CRPS, lower is better)")
    print(f"{'lead':>6}  {'n':>4}  {'aggregate':>11}  {'best single':>12}  {'beats best':>11}  "
          f"{'beats PM':>9}  {'beats Kalshi':>13}")
    for e in summary:
        c = e.get("aggregate_vs_best_single")
        if not c:
            continue
        print(f"{e['lead_minutes']:>4}m  {c['n']:>4}  {c['aggregate_crps']:>11,.0f}  "
              f"{c['best_single_crps']:>12,.0f}  {c['aggregate_beats_best_single_rate']:>10.1%}  "
              f"{c['aggregate_beats_polymarket_rate']:>8.1%}  {c['aggregate_beats_kalshi_rate']:>12.1%}")

    out_dir = ab.RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "aggregate_backtest_results.json"
    out_path.write_text(json.dumps({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "summary": summary,
        "per_date": [
            {
                "target_date": str(r.target_date), "lead_minutes": r.lead_minutes,
                "realized": r.realized, "spot_at_lead": r.spot_at_lead,
                "scores": {
                    n: {"crps": s.crps, "mad_mean": s.abs_error_mean,
                        "mad_median": s.abs_error_median, "ci68_hit": s.ci68_hit}
                    for n, s in r.scores.items()
                },
            }
            for r in results
        ],
    }, indent=2, default=str))
    print(f"\nsaved {out_path}")


if __name__ == "__main__":
    main()
