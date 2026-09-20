"""Two checks the headline NAV/CAGR numbers in both 13F backtests skipped:

1. STATISTICAL SIGNIFICANCE. 22 quarterly observations is a small sample --
   a "N=10 beats SPY by 0.5pp of CAGR" claim needs to survive a test before
   it's treated as a real finding rather than noise that happened to land a
   certain way. Paired t-test AND a bootstrap on the per-period excess
   returns (portfolio return minus SPY return each quarter), since a
   bootstrap doesn't assume the spread is normally distributed, which is a
   real risk with only 22 points.

2. FACTOR DECOMPOSITION. A single-factor (CAPM-style) regression of each
   portfolio's period returns against SPY's: return = alpha + beta *
   SPY_return + epsilon. A beta noticeably above 1 with a small/insignificant
   alpha is exactly what "this is just a concentrated growth/tech bet, not
   genuine stock-picking skill" would look like -- alpha is the part of the
   return that ISN'T explained by simply being more market-exposed.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy import stats

REPO_ROOT = Path(__file__).resolve().parents[1]


def nav_to_returns(nav: list[float]) -> np.ndarray:
    arr = np.array(nav)
    return arr[1:] / arr[:-1] - 1


def paired_test(port_returns: np.ndarray, bench_returns: np.ndarray, n_boot: int = 20000, seed: int = 0) -> dict:
    spread = port_returns - bench_returns
    n = len(spread)
    mean_spread = float(np.mean(spread))
    se = float(np.std(spread, ddof=1) / np.sqrt(n))
    t_stat = mean_spread / se if se > 0 else 0.0
    p_value = float(2 * (1 - stats.t.cdf(abs(t_stat), df=n - 1)))

    rng = np.random.default_rng(seed)
    boot_means = np.array([np.mean(rng.choice(spread, size=n, replace=True)) for _ in range(n_boot)])
    ci_lo, ci_hi = np.percentile(boot_means, [2.5, 97.5])
    frac_positive = float(np.mean(boot_means > 0))

    return {
        "n_periods": n,
        "mean_quarterly_spread_pct": mean_spread * 100,
        "annualized_spread_pct": mean_spread * 4 * 100,
        "t_stat": float(t_stat),
        "p_value": p_value,
        "bootstrap_ci_95_annualized_pct": [float(ci_lo * 4 * 100), float(ci_hi * 4 * 100)],
        "bootstrap_frac_periods_spread_positive": frac_positive,
        "significant_at_5pct": p_value < 0.05,
    }


def factor_regression(port_returns: np.ndarray, mkt_returns: np.ndarray) -> dict:
    beta, alpha = np.polyfit(mkt_returns, port_returns, 1)
    pred = alpha + beta * mkt_returns
    resid = port_returns - pred
    ss_res = np.sum(resid ** 2)
    ss_tot = np.sum((port_returns - np.mean(port_returns)) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    n = len(port_returns)
    dof = n - 2
    mse = ss_res / dof if dof > 0 else np.nan
    mkt_var = np.sum((mkt_returns - np.mean(mkt_returns)) ** 2)
    se_beta = np.sqrt(mse / mkt_var) if mkt_var > 0 else np.nan
    se_alpha = np.sqrt(mse * (1 / n + np.mean(mkt_returns) ** 2 / mkt_var)) if mkt_var > 0 else np.nan
    alpha_t = alpha / se_alpha if se_alpha and se_alpha > 0 else 0.0
    alpha_p = float(2 * (1 - stats.t.cdf(abs(alpha_t), df=dof))) if dof > 0 else 1.0

    return {
        "alpha_quarterly_pct": float(alpha * 100),
        "alpha_annualized_pct": float(alpha * 4 * 100),
        "alpha_p_value": alpha_p,
        "alpha_significant_at_5pct": alpha_p < 0.05,
        "beta": float(beta),
        "r_squared": float(r_squared),
    }


def analyze_backtest(nav_path: Path, keys: list[str], benchmark_key: str = "SPY") -> dict:
    data = json.loads(nav_path.read_text())
    nav = data["nav"]
    bench_returns = nav_to_returns(nav[benchmark_key])
    out = {}
    for key in keys:
        if key == benchmark_key or key not in nav:
            continue
        port_returns = nav_to_returns(nav[key])
        out[key] = {
            "significance_vs_spy": paired_test(port_returns, bench_returns),
            "factor_regression_vs_spy": factor_regression(port_returns, bench_returns),
        }
    return out


if __name__ == "__main__":
    print("=== Fixed 10-fund curated panel ===")
    fixed = analyze_backtest(
        REPO_ROOT / "results" / "sec_13f_backtest" / "nav_series.json",
        keys=["consensus", "BERKSHIRE", "ELLIOTT", "ICAHN"],
    )
    print(json.dumps(fixed, indent=2))

    print("\n=== Objective rolling N=10/20/50 panels ===")
    scaling = analyze_backtest(
        REPO_ROOT / "results" / "sec_13f_scaling_backtest" / "nav_series.json",
        keys=["N10", "N20", "N50"],
    )
    print(json.dumps(scaling, indent=2))

    out_dir = REPO_ROOT / "results" / "sec_13f_significance"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "fixed_panel.json").write_text(json.dumps(fixed, indent=2))
    (out_dir / "scaling_panels.json").write_text(json.dumps(scaling, indent=2))
