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

    dates = sorted(df["event_date"].unique())
    split_date = dates[len(dates) // 2]
    persistence = ts.split_sample_persistence(df, split_date)
    print(f"\n{name} split-sample persistence test (split at {split_date}):")
    for k, v in persistence.items():
        print(f"  {k}: {v}")

    return {
        "n_trades": len(df), "n_wallets": int(df["wallet"].nunique()), "n_events": int(df["event_date"].nunique()),
        "leaderboard_top10": lb.head(10).reset_index().to_dict(orient="records"),
        "leaderboard_bottom10": lb.tail(10).reset_index().to_dict(orient="records"),
        "population_edge_mean": float(lb["edge"].mean()), "population_edge_median": float(lb["edge"].median()),
        "population_edge_std": float(lb["edge"].std()),
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

    out_dir = ts.RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "trader_skill_results.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nsaved {out_path}")


if __name__ == "__main__":
    main()
