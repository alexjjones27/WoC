"""Run the BTC prediction-market calibration backtest (see
src/btc_price_market_calibration.py for the full method writeup) and print
+ save a report: by lead time before resolution, how far off was
Polymarket's "Bitcoin price on <date>" implied mean from the realized BTC
price, how often did its own 68% CI actually cover the outcome, and were
its stated bucket probabilities actually calibrated (Brier score +
reliability curve) against a naive "price doesn't move" baseline.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import btc_price_market_calibration as cal

from datetime import datetime, timezone


def main():
    today = datetime.now(timezone.utc).date()
    print(f"Discovering resolved events from {cal.EARLIEST_KNOWN_DATE} to {today} (exclusive)...")
    events = cal.fetch_all_resolved_events(today)
    print(f"  {len(events)} resolved events with usable bucket history")
    if not events:
        print("No usable events -- aborting.")
        return

    print("Fetching BTC-USD hourly spot series...")
    spot = cal.fetch_spot_series(events[0].date, today)
    print(f"  {len(spot)} hourly spot points from {cal.SPOT_SOURCE_USED}")

    print("\nBuilding calibration report by lead time...")
    reports = cal.build_calibration_report(events, spot)

    print(f"\n{'lead':>6}  {'n':>4}  {'mean_err':>10}  {'MAD':>9}  {'RMSE':>9}  {'naive_MAD':>10}  {'beats_naive':>11}  {'68%_cov':>8}  {'brier':>7}  {'log_loss':>8}")
    rows = []
    for r in reports:
        beats_naive = r.mad_error < r.naive_mad_error if r.naive_mad_error == r.naive_mad_error else None
        print(f"{r.lead_hours:>4}h  {r.n_events:>4}  {r.mean_error:>+10,.0f}  {r.mad_error:>9,.0f}  {r.rmse:>9,.0f}  "
              f"{r.naive_mad_error:>10,.0f}  {str(beats_naive):>11}  {r.ci68_coverage:>7.1%}  {r.brier_score:>7.4f}  {r.log_loss:>8.4f}")
        rows.append({
            "lead_hours": r.lead_hours, "n_events": r.n_events, "mean_error": r.mean_error,
            "mad_error": r.mad_error, "rmse": r.rmse, "naive_mad_error": r.naive_mad_error,
            "beats_naive": beats_naive, "ci68_coverage": r.ci68_coverage, "brier_score": r.brier_score,
            "log_loss": r.log_loss, "calibration_bins": r.calibration_bins,
            "median_mean_error": r.median_mean_error, "mad_median_error": r.mad_median_error,
            "crps_market": r.crps_market, "crps_naive": r.crps_naive,
            "crps_random_walk": r.crps_random_walk, "rw_ci68_coverage": r.rw_ci68_coverage,
            "n_random_walk": r.n_random_walk,
        })

    # Is the market's point estimate bad, or is our reconstruction of its
    # open tails bad? The median barely moves under tail conventions; the
    # mean does. If MAD(median) is much better than MAD(mean), the problem
    # is the reconstruction, not the market.
    print(f"\n{'lead':>6}  {'MAD(mean)':>10}  {'MAD(median)':>12}  {'naive_MAD':>10}  {'median beats naive':>19}")
    for r in reports:
        beats = r.mad_median_error < r.naive_mad_error if r.naive_mad_error == r.naive_mad_error else None
        print(f"{r.lead_hours:>4}h  {r.mad_error:>10,.0f}  {r.mad_median_error:>12,.0f}  "
              f"{r.naive_mad_error:>10,.0f}  {str(beats):>19}")

    # A distribution deserves a distributional score, against a
    # distributional baseline. CRPS is in dollars and reduces to |error|
    # for a point forecast, so all three columns are comparable.
    print(f"\n{'lead':>6}  {'CRPS market':>12}  {'CRPS naive':>11}  {'CRPS rand-walk':>15}  "
          f"{'market 68% cov':>15}  {'rand-walk 68% cov':>18}  {'n_rw':>5}")
    for r in reports:
        print(f"{r.lead_hours:>4}h  {r.crps_market:>12,.0f}  {r.crps_naive:>11,.0f}  {r.crps_random_walk:>15,.0f}  "
              f"{r.ci68_coverage:>14.1%}  {r.rw_ci68_coverage:>17.1%}  {r.n_random_walk:>5}")

    print("\nCalibration curves (predicted-probability bin -> empirical hit rate; perfect calibration = diagonal):")
    for r in reports:
        print(f"\n  lead={r.lead_hours}h:")
        for center, hit_rate, n in r.calibration_bins:
            bar = "#" * int(round(hit_rate * 40))
            diag = "#" * int(round(center * 40))
            print(f"    p~{center:4.2f} (n={n:4d})  actual={hit_rate:5.1%} {bar}")
            print(f"                              ideal ={center:5.1%} {diag}")

    RESULTS_DIR = cal.RESULTS_DIR
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "calibration_results.json"
    out_path.write_text(json.dumps({
        "n_events": len(events),
        "date_range": [str(events[0].date), str(events[-1].date)],
        "spot_source": cal.SPOT_SOURCE_USED,
        "realized_vol_window_hours": cal.REALIZED_VOL_WINDOW_HOURS,
        "lead_time_reports": rows,
    }, indent=2))
    print(f"\nsaved {out_path}")

    try:
        _plot(reports, RESULTS_DIR)
    except Exception as exc:  # noqa: BLE001 -- plotting is a nice-to-have, never block the report
        print(f"(plot skipped: {exc})")


def _plot(reports, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))

    ax = axes[0]
    leads = [r.lead_hours for r in reports]
    ax.plot(leads, [r.mad_error for r in reports], marker="o", label="market MAD ($)")
    ax.plot(leads, [r.naive_mad_error for r in reports], marker="o", label="naive (spot-at-lead) MAD ($)")
    ax.set_xlabel("lead time before resolution (hours)")
    ax.set_ylabel("mean absolute error ($)")
    ax.set_title("Forecast error by lead time")
    ax.legend()
    ax.invert_xaxis()

    ax = axes[1]
    for r in reports:
        if not r.calibration_bins:
            continue
        xs = [c for c, _, _ in r.calibration_bins]
        ys = [h for _, h, _ in r.calibration_bins]
        ax.plot(xs, ys, marker="o", label=f"{r.lead_hours}h")
    ax.plot([0, 1], [0, 1], linestyle="--", color="gray", label="perfect calibration")
    ax.set_xlabel("predicted probability")
    ax.set_ylabel("empirical hit rate")
    ax.set_title("Bucket-probability calibration")
    ax.legend(fontsize=8)

    # Panel 3: the distributional comparison. A prediction market emits a
    # distribution, so the honest benchmark is another distribution, not
    # just a point forecast.
    ax = axes[2]
    ax.plot(leads, [r.crps_market for r in reports], marker="o", label="market CRPS ($)")
    ax.plot(leads, [r.crps_naive for r in reports], marker="o", label="naive point CRPS ($)")
    ax.plot(leads, [r.crps_random_walk for r in reports], marker="o",
            label="random walk + realized vol CRPS ($)")
    ax.set_xlabel("lead time before resolution (hours)")
    ax.set_ylabel("CRPS ($, lower is better)")
    ax.set_title("Distributional accuracy by lead time")
    ax.legend(fontsize=8)
    ax.invert_xaxis()

    fig.tight_layout()
    path = out_dir / "calibration_plots.png"
    fig.savefig(path, dpi=130)
    print(f"saved {path}")


if __name__ == "__main__":
    main()
