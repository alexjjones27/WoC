"""Fit and evaluate the forecast model (src/forecast_model.py).

Answers the question the rest of the repo's measurement kept circling: can
a prediction market's implied distribution be turned into a price forecast
that beats free public data? Everything is fitted on a chronological train
split and reported on held-out test events.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import btc_price_market_calibration as cal  # noqa: E402
import forecast_model as fm  # noqa: E402


def ensure_dataset(rebuild: bool) -> list:
    if not rebuild and fm.DATASET_PATH.exists():
        return fm.load_dataset()
    print("Building dataset (discovering events, fetching spot)...")
    events = cal.fetch_all_resolved_events(datetime.now(timezone.utc).date())
    spot = cal.fetch_spot_series(
        min(e.date for e in events) - timedelta(days=40),
        max(e.date for e in events) + timedelta(days=2),
    )
    print(f"  {len(events)} events, {len(spot)} spot points from {cal.SPOT_SOURCE_USED}")
    cases = fm.build_dataset(events, spot)
    fm.save_dataset(cases)
    return cases


def main():
    rebuild = "--rebuild" in sys.argv
    cases = ensure_dataset(rebuild)
    leads = sorted({c.lead_hours for c in cases}, reverse=True)
    print(f"\n{len(cases)} cases across {len({c.event_date for c in cases})} events, lead times {leads}")

    # --- 1. staged, per lead time: does each parameter earn its place? ---
    print("\n" + "=" * 78)
    print("STAGED EVALUATION -- fitted per lead time on train, scored on held-out test")
    print("=" * 78)
    staged = fm.evaluate_by_lead(cases)
    for e in staged:
        b = e["baselines"]
        print(f"\n  lead {e['lead_hours']}h   train {e['n_train']} / test {e['n_test']}")
        print(f"    {'naive spot (baseline)':34} {b['naive']['crps']:>8,.0f}")
        print(f"    {'random walk (baseline)':34} {b['random_walk']['crps']:>8,.0f}   cov {b['random_walk']['coverage68']:.0%}")
        print(f"    {'':34} {'TEST':>8}   {'cov':>4}   params")
        for s in e["stages"]:
            p = s["params"]
            flag = " <- beats RW" if s["test"]["crps"] < b["random_walk"]["crps"] else ""
            print(f"    {s['name']:34} {s['test']['crps']:>8,.0f}   {s['test']['coverage68']:>3.0%}   "
                  f"lam={p['lambda']:.2f} s={p['scale']:.2f} w={p['weight']:.2f}{flag}")

    # --- 2. one global parameterization, fitted across all horizons ---
    print("\n" + "=" * 78)
    print("GLOBAL MODEL -- one (lambda, scale) for every horizon")
    print("=" * 78)
    train, test = fm.split_chronologically(cases)

    def rel_obj(cs, params):
        """CRPS relative to each case's own random-walk CRPS, so the long
        horizons (whose dollar errors are several times larger) do not
        dominate a pooled fit."""
        r = []
        for c in cs:
            if not c.sigma:
                continue
            base = fm.crps_from_quantiles(fm.random_walk_quantiles(c.spot, c.sigma), c.realized)
            if base <= 0:
                continue
            q = fm.apply_model(np.array(c.q_market), c.spot, c.sigma, params)
            r.append(fm.crps_from_quantiles(q, c.realized) / base)
        return float(np.mean(r))

    best = None
    for lam in [round(x, 2) for x in np.arange(0.0, 1.01, 0.1)]:
        for sc in [round(x, 2) for x in np.arange(0.5, 1.31, 0.05)]:
            v = rel_obj(train, fm.ModelParams(lam, sc, 1.0))
            if best is None or v < best[0]:
                best = (v, lam, sc)
    fitted = fm.ModelParams(best[1], best[2], 1.0)
    shipped = fm.ModelParams(0.0, 0.65, 1.0)
    print(f"\n  fitted on train: lambda={fitted.lam:.2f}  scale={fitted.scale:.2f}")
    print(f"  shipped:         lambda={shipped.lam:.2f}  scale={shipped.scale:.2f}"
          f"   (see model.py for why the width differs from the CRPS optimum)")

    print(f"\n  Held-out test, CRPS ($):")
    print(f"    {'lead':>5} {'market raw':>11} {'model':>9} {'random walk':>12} {'naive':>9}   {'vs raw':>8} {'vs RW':>7}")
    rows = []
    for lead in leads:
        te = [c for c in test if c.lead_hours == lead]
        raw = fm.score(te, fm.ModelParams(), "").crps
        mod = fm.score(te, shipped, "")
        rw = fm.score_baseline_random_walk(te).crps
        nv = fm.score_baseline_naive(te).crps
        print(f"    {lead:>4}h {raw:>11,.0f} {mod.crps:>9,.0f} {rw:>12,.0f} {nv:>9,.0f}   "
              f"{(mod.crps/raw-1)*100:>+7.1f}% {(mod.crps/rw-1)*100:>+6.1f}%")
        rows.append({"lead_hours": lead, "n_test": len(te), "crps_raw": raw, "crps_model": mod.crps,
                     "crps_random_walk": rw, "crps_naive": nv,
                     "coverage_model": mod.coverage68,
                     "coverage_raw": fm.score(te, fm.ModelParams(), "").coverage68})

    print(f"\n  68% interval coverage (nominal 68%):")
    print(f"    {'lead':>5} {'market raw':>11} {'model':>9}")
    for r in rows:
        print(f"    {r['lead_hours']:>4}h {r['coverage_raw']:>11.0%} {r['coverage_model']:>9.0%}")

    # --- 3. the width parameter's trade-off, shown rather than asserted ---
    print(f"\n  Width parameter trade-off on held-out test (lambda=0):")
    print(f"    {'scale':>6} " + " ".join(f"{str(l)+'h CRPS':>10}" for l in leads)
          + "   " + " ".join(f"{str(l)+'h cov':>9}" for l in leads))
    sweep = []
    for sc in [0.55, 0.65, 0.75, 0.85, 0.95, 1.00]:
        P = fm.ModelParams(0.0, sc, 1.0)
        cr, cv = [], []
        for lead in leads:
            s = fm.score([c for c in test if c.lead_hours == lead], P, "")
            cr.append(s.crps); cv.append(s.coverage68)
        print(f"    {sc:>6.2f} " + " ".join(f"{v:>10,.0f}" for v in cr)
              + "   " + " ".join(f"{v:>9.0%}" for v in cv))
        sweep.append({"scale": sc, "crps": cr, "coverage": cv})

    out_dir = fm.RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "forecast_model_results.json"
    path.write_text(json.dumps({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "n_cases": len(cases),
        "n_events": len({c.event_date for c in cases}),
        "lead_hours": leads,
        "spot_source": cal.SPOT_SOURCE_USED,
        "staged_per_lead": staged,
        "global_fitted": fitted.as_dict(),
        "global_shipped": shipped.as_dict(),
        "held_out": rows,
        "width_sweep": sweep,
    }, indent=2, default=str))
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
