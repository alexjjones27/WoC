"""Run the prediction-market trader-skill check (see
src/prediction_market_trader_skill.py for the full method writeup):
score every individual trade in ~101 resolved BTC daily events and ~16
resolved OIL touch events against what actually happened, build a
leaderboard, and run the split-sample persistence test to check whether
any apparent skill is real or just noise from looking at hundreds of
wallets.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import prediction_market_trader_skill as ts


def report_asset(name: str, trades: list) -> dict:
    df = ts.trades_to_frame(trades)
    print(f"\n=== {name}: {len(df)} scored trades, {df['wallet'].nunique()} unique wallets, "
          f"{df['event_date'].nunique()} events, {df['event_date'].min()} to {df['event_date'].max()} ===")

    lb = ts.leaderboard(df, min_trades=5)
    print(f"\n{name} leaderboard (min 5 trades), top 10 by edge:")
    print(f"{'wallet':44} {'edge':>8} {'n_trades':>9} {'n_events':>9} {'total_pnl':>12}")
    for wallet, row in lb.head(10).iterrows():
        print(f"{wallet:44} {row['edge']:>8.3f} {row['n_trades']:>9.0f} {row['n_events']:>9.0f} {row['total_pnl']:>12,.0f}")
    print(f"\n{name} leaderboard, bottom 10 by edge:")
    for wallet, row in lb.tail(10).iterrows():
        print(f"{wallet:44} {row['edge']:>8.3f} {row['n_trades']:>9.0f} {row['n_events']:>9.0f} {row['total_pnl']:>12,.0f}")

    print(f"\n{name} population edge distribution: mean={lb['edge'].mean():.4f} median={lb['edge'].median():.4f} "
          f"std={lb['edge'].std():.4f} n_wallets={len(lb)}")

    curve = ts.price_calibration_curve(df)
    print(f"\n{name} market calibration by trade price (the favorite-longshot check):")
    print(f"{'mean price':>11} {'realized q':>11} {'q/price':>9} {'size':>14} {'trades':>9}")
    for _, r in curve.iterrows():
        ratio = r["q"] / r["mean_price"] if r["mean_price"] else float("nan")
        print(f"{r['mean_price']:>11.4f} {r['q']:>11.4f} {ratio:>9.2f} {r['size']:>14,.0f} {r['n_trades']:>9,.0f}")

    dates = sorted(df["event_date"].unique())
    split_date = dates[len(dates) // 2]
    persistence = ts.split_sample_persistence(df, split_date)
    print(f"\n{name} split-sample persistence test (split at {split_date}, "
          f"n_wallets={persistence.get('n_wallets')}):")

    if "correlations" in persistence:
        print(f"  {'':22} {'r':>8} {'perm p':>8}")
        for kind, c in persistence["correlations"].items():
            print(f"  {kind:22} {c['r']:>8.3f} {c['p']:>8.3f}")

        sens = persistence["outlier_sensitivity"]
        if sens:
            print(f"  -- outlier sensitivity (raw Pearson) --")
            print(f"  drop most influential wallet : {sens['pearson_without_most_influential_wallet']:.3f} "
                  f"(swing {sens['largest_single_wallet_swing']:+.3f})")
            print(f"  drop top 1% most influential : {sens['pearson_without_top_1pct_most_influential']:.3f}")

        lc = persistence["longshot_control"]
        print(f"  -- longshot control: is this skill, or harvesting the price-level bias? --")
        print(f"  share of population P&L explained by price level : "
              f"{lc['population_bias_explained_share_of_pnl']:.3f}")
        print(f"  corr(P1 edge, P1 mean trade price)              : "
              f"{lc['corr_edge1_vs_mean_trade_price1']:.3f}")
        print(f"  persistence AFTER removing the price-level component:")
        for kind, c in lc["residual_correlations"].items():
            print(f"    {kind:20} {c['r']:>8.3f} {c['p']:>8.3f}")
    else:
        print(f"  {persistence.get('note')}")

    for k in ("top_decile_p1_edge_mean", "top_decile_p2_edge_mean",
              "bottom_decile_p1_edge_mean", "bottom_decile_p2_edge_mean",
              "population_p2_edge_mean"):
        if k in persistence:
            print(f"  {k}: {persistence[k]:.4f}")

    return {
        "n_trades": len(df), "n_wallets": int(df["wallet"].nunique()), "n_events": int(df["event_date"].nunique()),
        "first_event_date": str(df["event_date"].min()), "last_event_date": str(df["event_date"].max()),
        "leaderboard_top10": lb.head(10).reset_index().to_dict(orient="records"),
        "leaderboard_bottom10": lb.tail(10).reset_index().to_dict(orient="records"),
        "population_edge_mean": float(lb["edge"].mean()), "population_edge_median": float(lb["edge"].median()),
        "population_edge_std": float(lb["edge"].std()),
        "price_calibration_curve": curve.drop(columns=["price_bin"]).to_dict(orient="records"),
        "persistence": persistence,
    }


def main():
    today = datetime.now(timezone.utc).date()

    print("Collecting BTC trades (101 resolved daily events)...")
    btc_trades = ts.collect_btc_trades(today)
    print("Collecting OIL trades (16 most recent resolved touch events)...")
    oil_trades = ts.collect_oil_trades(max_events=16)

    results = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "BTC": report_asset("BTC", btc_trades),
        "OIL": report_asset("OIL", oil_trades),
    }
    # Record which ground truth the OIL half actually used -- realized WTI
    # where it was reachable, the markets' own settlement where it wasn't.
    results["OIL"]["ground_truth"] = ts.oil_ground_truth_used()
    print(f"\nOIL ground truth used: {results['OIL']['ground_truth']}")

    out_dir = ts.RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "trader_skill_results.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nsaved {out_path}")


if __name__ == "__main__":
    main()
