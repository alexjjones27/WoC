"""The capstone: bring prediction markets (the original wisdom-dashboard),
options-implied distributions, and retail attention together for BTC and
oil -- closing the loop between the two halves of this project.

Usage: python3 scripts/run_btc_oil_unification.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from btc_oil_wisdom_combination import (  # noqa: E402
    ASSET_CONFIG,
    cross_check_pm_vs_options,
    fetch_real_spot,
    get_options_return_distribution,
    get_prediction_market_signal,
    get_retail_attention,
)

RESULTS_DIR = REPO_ROOT / "results" / "btc_oil_unification"


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for asset in ["BTC", "OIL"]:
        print(f"Processing {asset}...")
        options = get_options_return_distribution(asset)
        if "error" in options:
            print(f"  options failed: {options['error']}")
            out[asset] = {"error": options["error"]}
            continue
        target_hours = options["t_years"] * 365.25 * 24
        pm = get_prediction_market_signal(asset, target_lead_hours=target_hours)
        attn = get_retail_attention(asset)
        real_spot = fetch_real_spot(ASSET_CONFIG[asset]["real_spot_ticker"])
        xcheck = cross_check_pm_vs_options(pm, options, real_spot) if "error" not in pm else None

        pm_implied_return = (pm["median"] / real_spot - 1) if "error" not in pm and real_spot else None

        out[asset] = {
            "real_spot": real_spot,
            "prediction_market": {k: v for k, v in pm.items() if k not in ("returns", "probs")} if "error" not in pm else pm,
            "pm_implied_return": pm_implied_return,
            "options": {k: v for k, v in options.items() if k not in ("returns", "probs")},
            "retail_attention": attn,
            "cross_check_p_options_exceed_pm_median": xcheck,
        }
        print(f"  done. PM implied return: {pm_implied_return}, cross-check: {xcheck}")

    with open(RESULTS_DIR / "unification.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nWrote {RESULTS_DIR / 'unification.json'}")


if __name__ == "__main__":
    main()
