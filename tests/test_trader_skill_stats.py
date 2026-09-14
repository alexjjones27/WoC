"""Tests for the skill-vs-luck statistics in prediction_market_trader_skill.py.

These are synthetic-data tests with a KNOWN right answer, because the thing
being checked is the method, not the data: given a population that has real
forecasting skill, does the test find it? Given one that has none, does it
stay quiet? And -- the question the longshot control exists to answer --
given a population whose only "edge" is harvesting a static price-level
mispricing, does the test correctly refuse to call that skill?
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

import prediction_market_trader_skill as ts

N_PERM = 400  # keep the suite fast; enough resolution for p < 0.01 vs p ~ 0.5
START = date(2026, 1, 1)
SPLIT = date(2026, 3, 1)


def _frame(rows):
    df = pd.DataFrame(rows)
    df["pnl"] = df["side_sign"] * (df["outcome"] - df["price"]) * df["size"]
    df["notional"] = df["size"] * df["price"]
    return df


def _trade(wallet, day, price, outcome, side_sign=1, size=100.0):
    return {
        "wallet": wallet, "asset": "BTC", "event_date": day, "size": size,
        "price": price, "outcome": float(outcome), "side_sign": side_sign,
    }


# --- correlation helpers --------------------------------------------------

def test_pearson_matches_numpy():
    rng = np.random.default_rng(0)
    x, y = rng.normal(size=200), rng.normal(size=200)
    assert ts._pearson(x, y) == pytest.approx(np.corrcoef(x, y)[0, 1])


def test_spearman_is_pearson_on_ranks_and_ignores_monotone_rescaling():
    rng = np.random.default_rng(1)
    x = rng.normal(size=150)
    y = x * 2.0 + rng.normal(size=150) * 0.5
    r_lin = ts._pearson(ts._ranks(x), ts._ranks(y))
    r_exp = ts._pearson(ts._ranks(x), ts._ranks(np.exp(y)))
    assert r_lin == pytest.approx(r_exp)  # rank correlation is scale-free


def test_winsorize_clips_both_tails_only():
    a = np.array([-1000.0] + [0.0] * 98 + [1000.0])
    w = ts._winsorize(a, 0.01)
    assert w.max() < 1000.0 and w.min() > -1000.0
    assert np.median(w) == 0.0


def test_permutation_p_is_near_one_for_independent_vectors():
    rng = np.random.default_rng(2)
    out = ts._corr_with_permutation_p(rng.normal(size=300), rng.normal(size=300), N_PERM, 0)
    assert abs(out["r"]) < 0.15
    assert out["p"] > 0.2


def test_permutation_p_is_small_for_a_genuinely_related_pair():
    rng = np.random.default_rng(3)
    x = rng.normal(size=300)
    y = x + rng.normal(size=300)
    out = ts._corr_with_permutation_p(x, y, N_PERM, 0)
    assert out["r"] > 0.5
    assert out["p"] < 0.01


def test_one_outlier_can_manufacture_a_significant_pearson_that_rank_rejects():
    """The exact failure mode the extra columns exist to expose: two vectors
    that are pure noise apart from a single shared extreme point."""
    rng = np.random.default_rng(4)
    x, y = rng.normal(size=200), rng.normal(size=200)
    x[0], y[0] = 400.0, 400.0  # one wallet with a longshot payoff in both periods

    out = ts._all_three_correlations(x, y, N_PERM, 0)
    assert out["pearson"]["r"] > 0.9 and out["pearson"]["p"] < 0.01
    assert abs(out["spearman"]["r"]) < 0.2 and out["spearman"]["p"] > 0.1
    assert abs(out["pearson_winsorized"]["r"]) < 0.2

    sens = ts._outlier_sensitivity(x, y)
    assert abs(sens["pearson_without_most_influential_wallet"]) < 0.2


# --- the calibration curve ------------------------------------------------

def test_price_calibration_curve_recovers_a_known_mispricing():
    rng = np.random.default_rng(5)
    rows = []
    for i in range(6000):
        rows.append(_trade(f"w{i%50}", START, 0.05, rng.random() < 0.01))   # 5c priced, 1% true
        rows.append(_trade(f"w{i%50}", START, 0.50, rng.random() < 0.50))   # 50c priced, fair
    curve = ts.price_calibration_curve(_frame(rows))
    cheap = curve[curve["mean_price"] < 0.1].iloc[0]
    fair = curve[(curve["mean_price"] > 0.4) & (curve["mean_price"] < 0.6)].iloc[0]
    assert cheap["q"] == pytest.approx(0.01, abs=0.01)
    assert fair["q"] == pytest.approx(0.50, abs=0.03)


def test_thin_price_bins_cannot_manufacture_a_bias_edge():
    """A bin with almost no traded size falls back to q(p)=p, so it
    contributes zero bias-explained P&L rather than a noisy estimate."""
    rows = [_trade("w", START, 0.5, 1)] * 3
    curve = ts.price_calibration_curve(_frame(rows))
    q = ts._q_lookup(curve)
    assert q(np.array([0.05, 0.5, 0.9])) == pytest.approx([0.05, 0.5, 0.9])


def test_bias_and_skill_pnl_decompose_the_realized_pnl():
    rng = np.random.default_rng(6)
    rows = [_trade(f"w{i%20}", START, float(rng.choice([0.05, 0.2, 0.5, 0.9])), rng.random() < 0.4)
            for i in range(4000)]
    out = ts.add_bias_explained_pnl(_frame(rows))
    assert (out["bias_pnl"] + out["skill_pnl"]).to_numpy() == pytest.approx(out["pnl"].to_numpy())


# --- the three populations the method has to tell apart --------------------
#
# These build a synthetic market with the mispricing shape
# btc_price_market_calibration.py actually measured -- contracts under 15c
# are worth about a fifth of their price, everything above is fair -- and
# then vary ONLY what the wallets do, so each test has a known answer.


def _true_q(p):
    p = np.asarray(p, dtype=float)
    return np.where(p < 0.15, p * 0.2, p)


def _run(df, seed=0):
    return ts.split_sample_persistence(df, SPLIT, n_permutations=N_PERM, seed=seed)


def _day(period, rng):
    base = START if period == 1 else SPLIT
    return base + timedelta(days=int(rng.integers(0, 50)))


def test_no_skill_population_shows_no_persistence():
    """400 wallets trading fairly-priced contracts at random. Some will look
    great in P1; none of that should predict P2."""
    rng = np.random.default_rng(7)
    rows = []
    for w in range(400):
        for period in (1, 2):
            for _ in range(30):
                price = float(rng.uniform(0.20, 0.80))
                rows.append(_trade(f"w{w}", _day(period, rng), price, rng.random() < _true_q(price)))
    res = _run(_frame(rows))
    assert abs(res["correlations"]["pearson"]["r"]) < 0.15
    assert res["correlations"]["pearson"]["p"] > 0.1
    assert res["correlations"]["spearman"]["p"] > 0.1


def test_genuine_forecasting_skill_is_detected_and_survives_the_longshot_control():
    """Wallets differ in a persistent ability to pick winners, at prices
    where there is no mispricing to harvest -- so the residual test must
    still find it."""
    rng = np.random.default_rng(8)
    rows = []
    for w in range(400):
        ability = rng.uniform(-0.20, 0.20)  # persistent across both periods
        for period in (1, 2):
            for _ in range(30):
                price = float(rng.uniform(0.20, 0.80))
                hit = rng.random() < np.clip(_true_q(price) + ability, 0.01, 0.99)
                rows.append(_trade(f"w{w}", _day(period, rng), price, hit))
    res = _run(_frame(rows))
    assert res["correlations"]["pearson"]["r"] > 0.3
    assert res["correlations"]["pearson"]["p"] < 0.01
    assert res["correlations"]["spearman"]["p"] < 0.01
    # Skill at fairly-priced levels is not explained by price-level bias, so
    # stripping the bias must leave the finding standing.
    assert res["longshot_control"]["residual_correlations"]["spearman"]["r"] > 0.2
    assert res["longshot_control"]["residual_correlations"]["spearman"]["p"] < 0.01


def test_pure_longshot_harvesting_is_not_reported_as_skill():
    """The confound this control exists for. Nobody here forecasts anything:
    wallets differ only in how often they sell cheap contracts that are
    worth a fifth of their price. Raw persistence is strong and highly
    significant; the residual test has to take it away."""
    rng = np.random.default_rng(9)
    rows = []
    for w in range(400):
        propensity = rng.random()  # persistent, but carries no forecasting content
        for period in (1, 2):
            for _ in range(30):
                if rng.random() < propensity:
                    price, side = float(rng.uniform(0.01, 0.14)), -1  # sell a longshot
                else:
                    price, side = float(rng.uniform(0.20, 0.80)), 1   # fair, no edge
                hit = rng.random() < _true_q(price)
                rows.append(_trade(f"w{w}", _day(period, rng), price, hit, side_sign=side))
    res = _run(_frame(rows))

    # On the headline numbers this is indistinguishable from skill.
    assert res["correlations"]["pearson"]["r"] > 0.25
    assert res["correlations"]["pearson"]["p"] < 0.01
    assert res["correlations"]["spearman"]["p"] < 0.01

    # And it is correctly attributed to price-level mispricing instead.
    for kind in ("pearson", "spearman"):
        resid = res["longshot_control"]["residual_correlations"][kind]
        assert abs(resid["r"]) < 0.15
        assert resid["p"] > 0.05
    assert res["longshot_control"]["population_bias_explained_share_of_pnl"] > 0.5


def test_too_few_wallets_returns_a_note_not_a_number():
    rng = np.random.default_rng(10)
    rows = [_trade(f"w{w}", _day(period, rng), 0.5, rng.random() < 0.5)
            for w in range(3) for period in (1, 2) for _ in range(5)]
    res = _run(_frame(rows))
    assert "note" in res and "too few wallets" in res["note"]


# --- per-trade outcome resolution -----------------------------------------
#
# Polymarket's feed reports trades on BOTH outcome tokens of a binary
# market. Scoring them all against the Yes resolution inverts every
# No-token trade, and those are usually the majority.

def _raw(side, price, outcome, size=100.0, wallet="w"):
    return {"proxyWallet": wallet, "side": side, "price": price, "outcome": outcome, "size": size}


def test_no_token_trades_are_scored_against_their_own_token():
    """"BUY No at 0.98" in a market whose No side won is a correct, barely
    profitable trade -- not the catastrophic loss it looks like if scored
    against the Yes outcome."""
    scored = ts._score_trades([_raw("BUY", 0.98, "No")], "No", START, "BTC")[0]
    assert scored.outcome == 1.0
    assert scored.pnl_per_share == pytest.approx(0.02)

    wrong_side = ts._score_trades([_raw("BUY", 0.98, "Yes")], "No", START, "BTC")[0]
    assert wrong_side.outcome == 0.0
    assert wrong_side.pnl_per_share == pytest.approx(-0.98)


def test_selling_the_losing_token_is_profitable():
    scored = ts._score_trades([_raw("SELL", 0.90, "Yes")], "No", START, "BTC")[0]
    assert scored.pnl_per_share == pytest.approx(0.90)


def test_a_mixed_book_prices_out_coherently():
    """Both sides of the same market, all correct trades: everyone who
    bought the winning token cheap made money, everyone who bought the
    losing token lost their stake."""
    raw = [
        _raw("BUY", 0.02, "Yes", wallet="a"),   # bought the loser cheap
        _raw("BUY", 0.98, "No", wallet="b"),    # bought the winner rich
        _raw("SELL", 0.02, "Yes", wallet="c"),  # sold the loser cheap
    ]
    scored = ts._score_trades(raw, "No", START, "BTC")
    by_wallet = {t.wallet: t for t in scored}
    assert by_wallet["a"].pnl_per_share == pytest.approx(-0.02)
    assert by_wallet["b"].pnl_per_share == pytest.approx(0.02)
    assert by_wallet["c"].pnl_per_share == pytest.approx(0.02)


def test_calibration_curve_is_not_inverted_by_mixed_outcome_tokens():
    """The regression check: a market where the expensive token is the one
    that keeps winning must produce q(p) ~ p, not q(p) ~ 1-p."""
    rng = np.random.default_rng(11)
    rows = []
    for i in range(4000):
        yes_wins = rng.random() < 0.20
        winner = "Yes" if yes_wins else "No"
        # Both tokens trade; the Yes token trades near 0.2, No near 0.8.
        rows += ts._score_trades(
            [_raw("BUY", 0.20, "Yes", wallet=f"w{i%40}"), _raw("BUY", 0.80, "No", wallet=f"w{i%40}")],
            winner, START, "BTC",
        )
    df = ts.trades_to_frame(rows)
    curve = ts.price_calibration_curve(df)
    cheap = curve[curve["mean_price"] < 0.5].iloc[0]
    rich = curve[curve["mean_price"] > 0.5].iloc[0]
    assert cheap["q"] == pytest.approx(0.20, abs=0.03)
    assert rich["q"] == pytest.approx(0.80, abs=0.03)
