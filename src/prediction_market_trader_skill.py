"""Have individual Polymarket traders actually been good at predicting BTC
and oil prices -- and is any apparent skill real (persists out-of-sample)
or just noise from looking at enough wallets?

Two different market structures are used, matched to what each asset
actually has (see wisdom-dashboard's adapters for the same reasoning):

  BTC: the 96+ resolved daily "Bitcoin price on <date>" range-bucket
  events already used by btc_price_market_calibration.py (reused directly
  -- discovery, caching, resolution ground truth). A trade in the bucket
  that actually won is a "correct" bet; ground truth is Polymarket's own
  resolution (outcomePrices collapsing to ["1","0"]).

  OIL: Polymarket has no point-in-time range-bucket family for oil (see
  wisdom-dashboard/backend/adapters/polymarket.py's docstring) -- only the
  recurring "What will WTI Crude Oil (WTI) hit [week of <date> | in
  <month>]?" touch events. Ground truth here is independent of Polymarket:
  each event's real WTI daily high/low (yfinance, ticker CL=F) over the
  event's own [startDate, endDate] window determines which "touch above
  $X" / "touch below $X" thresholds actually happened.

--- Scoring method ---

Position-level netting (who held how many shares when the market closed)
would need a COMPLETE trade history per wallet, which a capped fetch can't
guarantee for a high-volume market. Trade-level scoring sidesteps that:
every individual trade is itself a resolvable bet, independent of the
trader's other trades --

    pnl_per_share = (resolution_value - price)   if side == BUY
                  = (price - resolution_value)   if side == SELL

`resolution_value` is 1.0 if that trade's outcome token paid off, 0.0 if
not (fractional for nothing here -- every market used resolves cleanly to
one side). A trader's "edge" is their total pnl (pnl_per_share * size,
summed across every trade of theirs in the sample) divided by their total
notional traded -- a size-normalized, cross-trader-comparable return on
capital deployed, not a raw dollar P&L (which just tracks who bet biggest).

--- Skill vs. luck: the split-sample persistence test ---

With hundreds of wallets, some will show a large positive edge in ANY
single sample purely by chance -- a leaderboard of "best edge this
period" is not evidence of skill on its own (this is the same multiple-
comparisons trap as "best fund manager of the year"). The standard fix:
split events chronologically into two halves, compute each wallet's edge
in period 1, and check whether period-1 edge predicts period-2 edge
(correlation, or top-decile-in-P1's average P2 edge vs bottom-decile's).
Real skill persists across independent periods; pure noise does not.

Two things that test does NOT handle on its own, both measured here rather
than caveated:

  1. HEAVY TAILS. Edge is a ratio whose denominator can be tiny -- a
     $0.001 share that resolves Yes returns ~999x the dollar risked -- so
     per-wallet edge has a std of ~10 (BTC) to ~34 (OIL) against a median
     near zero. A Pearson correlation on that can rest almost entirely on
     two or three wallets, and the permutation test does not protect
     against it: shuffling tests whether the PAIRING is non-random, which
     one extreme point present in both periods satisfies perfectly well.
     Every correlation is therefore reported three ways -- raw Pearson,
     Spearman (rank), and winsorized Pearson -- each with its own
     permutation p-value, plus a leave-one-out sensitivity showing how far
     any single wallet moves the raw number.

  2. THE FAVORITE-LONGSHOT CONFOUND, which decides what a positive result
     MEANS. btc_price_market_calibration.py found cheap buckets on these
     same markets are systematically overpriced (the ~5%-priced bucket
     resolves Yes 0.8-3.7% of the time). That is a static, price-level
     mispricing: a wallet that simply sells cheap contracts harvests it in
     every period, forever, without forecasting anything -- and shows
     exactly the signature this module tests for. So persistence alone
     cannot separate "can predict BTC" from "sells overpriced longshots."
     The fix is a decomposition using only the sample itself: estimate
     q(p), the empirical probability that a token trading at price p
     actually pays off, across all trades (a market-level property, not a
     per-wallet fit), attribute side_sign * (q(price) - price) of each
     trade's P&L to price level alone, and re-run the persistence test on
     what is left. Survives -> forecasting skill. Collapses -> the
     calibration finding wearing a different hat. See the section comment
     above split_sample_persistence, and tests/test_trader_skill_stats.py,
     which checks the method against synthetic populations of each kind.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import btc_price_market_calibration as btccal

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / "data" / "raw" / "trader_skill"
TRADES_CACHE_DIR = CACHE_DIR / "trades"
OIL_EVENTS_CACHE_DIR = CACHE_DIR / "oil_events"
RESULTS_DIR = REPO_ROOT / "results" / "prediction_market_trader_skill"

GAMMA_BASE = "https://gamma-api.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"
USER_AGENT = "Mozilla/5.0 (research; contact via repo)"

# Bounds fetch cost. A trade-level score doesn't need a market's entire
# history to be representative -- capped, not exhaustive, same tradeoff
# wisdom-dashboard's concentration.py makes for the same reason.
MAX_TRADES_PER_MARKET = 1000
# How many of the most recent oil touch events to pull (there are many
# more historical weekly ones than this; capped to keep total runtime
# reasonable -- see report.md for exactly how many this run covered).
MAX_OIL_EVENTS = 16

TOUCH_LABEL_RE = re.compile(r"^([↑↓])\s*\$?([\d,]+(?:\.\d+)?)$")


def _request_json(url: str, retries: int = 4, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(min(2 ** attempt, 10))
                last_err = exc
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_err = exc
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"GET {url} failed after {retries} retries: {last_err}")


def fetch_trades_cached(condition_id: str) -> list[dict]:
    cache_path = TRADES_CACHE_DIR / f"{condition_id}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    trades: list[dict] = []
    offset = 0
    page_size = 500
    while offset < MAX_TRADES_PER_MARKET:
        batch = _request_json(f"{DATA_API_BASE}/trades?market={condition_id}&limit={page_size}&offset={offset}") or []
        if not batch:
            break
        trades.extend(batch)
        offset += len(batch)
        if len(batch) < page_size:
            break
    TRADES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(trades))
    return trades


@dataclass
class ScoredTrade:
    wallet: str
    asset: str
    event_date: date  # for chronological period-splitting
    pnl_per_share: float
    size: float
    notional: float
    # Kept per trade so the favorite-longshot confound below can be measured
    # rather than assumed away: `price` is what the trade paid per share,
    # `outcome` is 1.0/0.0 for whether that token actually paid off, and
    # `side_sign` is +1 for BUY, -1 for SELL, so that
    # pnl_per_share == side_sign * (outcome - price).
    price: float = 0.0
    outcome: float = 0.0
    side_sign: int = 1


def _score_trades(trades_raw: list[dict], winning_outcome: str, event_date: date, asset: str) -> list[ScoredTrade]:
    """Score every trade in one binary market against `winning_outcome`
    (the name of the side that actually paid off, e.g. "Yes" or "No").

    Polymarket's trade feed reports trades on BOTH of a market's outcome
    tokens, tagged with `outcome`/`outcomeIndex`, and `price` is the price
    of THAT token -- a market whose Yes settled at 0 typically shows a
    crowd of "BUY No at 0.99" trades that were correct. So the payoff has
    to be resolved per trade: the token pays 1 if it is the winning side
    and 0 otherwise. Scoring every trade against the Yes resolution (which
    this did before) inverts the sign of every No-token trade, and those
    are the majority in most of these markets -- it made the market's own
    price/outcome relationship come out backwards (cheap tokens appearing
    to pay off far MORE often than their price, expensive ones far less),
    which is the signature that surfaced the bug.
    """
    out = []
    for t in trades_raw:
        try:
            price, size = float(t["price"]), float(t["size"])
            side = t["side"]
            traded_outcome = t["outcome"]
        except (KeyError, TypeError, ValueError):
            continue
        token_pays = 1.0 if str(traded_outcome) == str(winning_outcome) else 0.0
        side_sign = 1 if side == "BUY" else -1
        pnl_per_share = side_sign * (token_pays - price)
        out.append(ScoredTrade(
            wallet=t["proxyWallet"], asset=asset, event_date=event_date,
            pnl_per_share=pnl_per_share, size=size, notional=size * price,
            price=price, outcome=token_pays, side_sign=side_sign,
        ))
    return out


# ---------------------------------------------------------------------------
# BTC: point-in-time daily events (reuses btc_price_market_calibration.py)
# ---------------------------------------------------------------------------

def collect_btc_trades(end_date: date, max_workers: int = 12) -> list[ScoredTrade]:
    dates = btccal.discover_resolved_dates(end_date)

    def process_date(d: date) -> list[ScoredTrade]:
        ev = btccal.fetch_event(d)
        if ev is None or not ev.get("closed"):
            return []
        out = []
        for m in ev.get("markets", []):
            label = m.get("groupItemTitle") or m.get("question", "")
            low, high = btccal._parse_bucket_label(label)
            if low is None and high is None:
                continue
            cond_id = m.get("conditionId")
            if not cond_id:
                continue
            try:
                outcomes = m["outcomes"] if isinstance(m["outcomes"], list) else json.loads(m["outcomes"])
                prices = m["outcomePrices"] if isinstance(m["outcomePrices"], list) else json.loads(m["outcomePrices"])
                winner_idx = max(range(len(prices)), key=lambda i: float(prices[i]))
                if float(prices[winner_idx]) <= 0.5:
                    continue  # not actually settled
                winning_outcome = outcomes[winner_idx]
            except (KeyError, ValueError, TypeError, IndexError):
                continue
            try:
                trades_raw = fetch_trades_cached(cond_id)
            except Exception:
                continue
            out.extend(_score_trades(trades_raw, winning_outcome, d, "BTC"))
        return out

    all_trades: list[ScoredTrade] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(process_date, d): d for d in dates}
        for fut in as_completed(futures):
            all_trades.extend(fut.result())
    return all_trades


# ---------------------------------------------------------------------------
# OIL: touch events, ground truth from realized WTI high/low (yfinance)
# ---------------------------------------------------------------------------

def _parse_touch_label(label: str) -> tuple[str, float] | None:
    m = TOUCH_LABEL_RE.match(label.strip())
    if not m:
        return None
    arrow, num = m.groups()
    return ("above" if arrow == "↑" else "below"), float(num.replace(",", ""))


OIL_TOUCH_TITLE_RE = re.compile(r"^what will wti crude oil \(wti\) hit\b", re.IGNORECASE)


def discover_resolved_oil_touch_events(max_events: int = MAX_OIL_EVENTS) -> list[dict]:
    """Closed "What will WTI Crude Oil (WTI) hit ...?" events (weekly and
    monthly), most recent first, capped at max_events."""
    seen: dict[str, dict] = {}
    for term in ("WTI crude oil hit", "WTI hit"):
        params = urllib.parse.urlencode({"q": term, "limit_per_type": 50})
        data = _request_json(f"{GAMMA_BASE}/public-search?{params}") or {}
        for ev in data.get("events", []):
            if ev.get("closed") is True and OIL_TOUCH_TITLE_RE.match(ev.get("title", "")) and ev.get("slug"):
                seen[ev["slug"]] = ev
    events = sorted(seen.values(), key=lambda e: e.get("endDate") or "", reverse=True)
    return events[:max_events]


def _fetch_oil_event_detail(slug: str) -> dict | None:
    cache_path = OIL_EVENTS_CACHE_DIR / f"{slug}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    ev = _request_json(f"{GAMMA_BASE}/events/slug/{slug}")
    OIL_EVENTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(ev) if ev is not None else "null")
    return ev


def _realized_wti_high_low(start_iso: str, end_iso: str) -> tuple[float, float] | None:
    """(max, min) of WTI (CL=F) daily High/Low over [start, end] --
    determines which touch thresholds actually happened. Padded a day on
    each side since yfinance's `end` is exclusive and futures don't trade
    every calendar day. Returns None if the price series is unavailable
    (no network, Yahoo unreachable, an empty response), which sends
    collect_oil_trades to the fallback described there."""
    try:
        import yfinance as yf

        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).date() - timedelta(days=1)
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00")).date() + timedelta(days=2)
        df = yf.download("CL=F", start=start, end=end, interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            return None
        high = df["High"]
        low = df["Low"]
        if hasattr(high, "columns"):
            high = high.iloc[:, 0]
            low = low.iloc[:, 0]
        return float(high.max()), float(low.min())
    except Exception:
        return None


def _polymarket_resolution(market: dict) -> float | None:
    """A resolved market's own outcome, from Gamma's outcomePrices
    collapsing to ["1","0"] (Yes) or ["0","1"] (No) -- the same ground
    truth the BTC half of this module uses.

    Used for OIL only as a FALLBACK, when realized WTI prices can't be
    fetched. The two are not interchangeable in provenance: realized
    high/low is measured independently of Polymarket, this is Polymarket
    grading itself. For touch markets the report argues they should agree
    (the question is exactly "did the actual max/min cross this"), but
    which one a given run used is recorded in the results rather than left
    for the reader to guess -- see OIL_GROUND_TRUTH_NOTE.
    """
    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except (ValueError, TypeError):
            return None
    if not isinstance(prices, list) or len(prices) < 2:
        return None
    try:
        yes = float(prices[0])
    except (TypeError, ValueError):
        return None
    if yes >= 0.99:
        return 1.0
    if yes <= 0.01:
        return 0.0
    return None  # not actually settled


# Which ground truth the last collect_oil_trades() run actually used, so
# the report can state it instead of asserting the preferred one.
_OIL_GROUND_TRUTH_USED: set[str] = set()


def oil_ground_truth_used() -> str:
    if not _OIL_GROUND_TRUTH_USED:
        return "none"
    return "+".join(sorted(_OIL_GROUND_TRUTH_USED))


def collect_oil_trades(max_events: int = MAX_OIL_EVENTS, max_workers: int = 8) -> list[ScoredTrade]:
    _OIL_GROUND_TRUTH_USED.clear()
    events = discover_resolved_oil_touch_events(max_events)

    def process_event(ev_summary: dict) -> list[ScoredTrade]:
        slug = ev_summary["slug"]
        ev = _fetch_oil_event_detail(slug)
        if ev is None:
            return []
        start_date, end_date = ev.get("startDate"), ev.get("endDate")
        if not start_date or not end_date:
            return []
        # Preferred ground truth: realized WTI high/low, measured
        # independently of Polymarket. Where that series is unavailable,
        # fall back to the market's own settled outcome rather than
        # dropping the event silently -- an empty OIL sample and an OIL
        # sample graded a different way are very different things to report.
        high_low = _realized_wti_high_low(start_date, end_date)
        realized_max, realized_min = high_low if high_low else (None, None)
        event_day = datetime.fromisoformat(end_date.replace("Z", "+00:00")).date()

        out = []
        for m in ev.get("markets", []):
            if m.get("closed") is False:
                continue  # keep only markets that actually reached resolution
            parsed = _parse_touch_label(m.get("groupItemTitle") or "")
            if parsed is None:
                continue
            direction, threshold = parsed
            if realized_max is not None:
                touched = (realized_max >= threshold) if direction == "above" else (realized_min <= threshold)
                _OIL_GROUND_TRUTH_USED.add("realized_wti")
            else:
                resolved = _polymarket_resolution(m)
                if resolved is None:
                    continue
                touched = resolved >= 0.5
                _OIL_GROUND_TRUTH_USED.add("polymarket_resolution")
            winning_outcome = "Yes" if touched else "No"
            cond_id = m.get("conditionId")
            if not cond_id:
                continue
            try:
                trades_raw = fetch_trades_cached(cond_id)
            except Exception:
                continue
            out.extend(_score_trades(trades_raw, winning_outcome, event_day, "OIL"))
        return out

    all_trades: list[ScoredTrade] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(process_event, ev): ev["slug"] for ev in events}
        for fut in as_completed(futures):
            all_trades.extend(fut.result())
    return all_trades


# ---------------------------------------------------------------------------
# Aggregation, leaderboard, and the skill-vs-luck persistence test
# ---------------------------------------------------------------------------

def trades_to_frame(trades: list[ScoredTrade]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "wallet": t.wallet, "asset": t.asset, "event_date": t.event_date,
            "pnl": t.pnl_per_share * t.size, "notional": t.notional,
            "size": t.size, "price": t.price, "outcome": t.outcome, "side_sign": t.side_sign,
        }
        for t in trades
    ])


def leaderboard(df: pd.DataFrame, min_trades: int = 5) -> pd.DataFrame:
    g = df.groupby("wallet").agg(
        total_pnl=("pnl", "sum"),
        total_notional=("notional", "sum"),
        n_trades=("pnl", "size"),
        n_events=("event_date", "nunique"),
    )
    g = g[g["n_trades"] >= min_trades].copy()
    g["edge"] = g["total_pnl"] / g["total_notional"]
    return g.sort_values("edge", ascending=False)


# ---------------------------------------------------------------------------
# Correlation machinery
#
# Edge is a ratio whose denominator can be tiny (a $0.001 share that resolves
# Yes returns ~999x the dollar risked), so the population is extremely
# heavy-tailed -- BTC's per-wallet edge has std ~10 against a median of
# -0.43, OIL's std ~34. A Pearson correlation on a sample like that can be
# carried almost entirely by two or three wallets, and the permutation test
# does NOT protect against that: it tests whether the PAIRING is
# non-random, which a single extreme point present in both periods
# satisfies perfectly well. So every correlation below is reported three
# ways -- raw Pearson, Spearman (rank, immune to the magnitude of the
# tails), and Pearson on winsorized edges -- each with its own permutation
# p-value. A finding that only survives in the raw column is a finding
# about a handful of wallets, and should be read that way.
# ---------------------------------------------------------------------------

WINSORIZE_PCT = 0.01


def _zscore(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    sd = a.std()
    return (a - a.mean()) / sd if sd > 0 else np.zeros_like(a)


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return float("nan")
    xs, ys = _zscore(x), _zscore(y)
    return float(np.dot(xs, ys) / len(xs))


def _winsorize(a: np.ndarray, pct: float = WINSORIZE_PCT) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    lo, hi = np.quantile(a, pct), np.quantile(a, 1.0 - pct)
    return np.clip(a, lo, hi)


def _ranks(a: np.ndarray) -> np.ndarray:
    """Average ranks (ties shared), so Pearson-on-ranks is Spearman."""
    return pd.Series(np.asarray(a, dtype=float)).rank(method="average").to_numpy()


def _corr_with_permutation_p(x: np.ndarray, y: np.ndarray, n_permutations: int, seed: int) -> dict:
    """Correlation plus a permutation p-value under the null that the two
    periods' performance is unrelated (shuffle which wallet's P2 edge is
    paired with which wallet's P1 edge)."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    n = len(x)
    if n < 8:
        return {"r": float("nan"), "p": float("nan"), "n": int(n)}
    observed = _pearson(x, y)
    # Permuting y leaves its mean/std alone, so z-score once and shuffle that.
    xs, ys = _zscore(x), _zscore(y)
    rng = np.random.default_rng(seed)
    idx = np.argsort(rng.random((n_permutations, n)), axis=1)
    perm_r = (ys[idx] @ xs) / n
    return {
        "r": float(observed),
        "p": float(np.mean(np.abs(perm_r) >= abs(observed))),
        "n": int(n),
    }


def _all_three_correlations(x: np.ndarray, y: np.ndarray, n_permutations: int, seed: int) -> dict:
    return {
        "pearson": _corr_with_permutation_p(x, y, n_permutations, seed),
        "spearman": _corr_with_permutation_p(_ranks(x), _ranks(y), n_permutations, seed + 1),
        "pearson_winsorized": _corr_with_permutation_p(
            _winsorize(x), _winsorize(y), n_permutations, seed + 2
        ),
    }


def _outlier_sensitivity(x: np.ndarray, y: np.ndarray) -> dict:
    """How much of the raw Pearson correlation rests on single wallets --
    leave-one-out, reported as the largest swing any one wallet causes."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    full = _pearson(x, y)
    n = len(x)
    if n < 10 or not np.isfinite(full):
        return {}
    loo = np.array([_pearson(np.delete(x, i), np.delete(y, i)) for i in range(n)])
    worst = int(np.argmax(np.abs(loo - full)))
    order = np.argsort(-np.abs(loo - full))[:max(1, n // 100)]
    keep = np.setdiff1d(np.arange(n), order)
    return {
        "pearson_full": float(full),
        "largest_single_wallet_swing": float(loo[worst] - full),
        "pearson_without_most_influential_wallet": float(loo[worst]),
        "pearson_without_top_1pct_most_influential": float(_pearson(x[keep], y[keep])),
    }


# ---------------------------------------------------------------------------
# The favorite-longshot confound
#
# btc_price_market_calibration.py found that cheap buckets on these same
# markets are systematically overpriced: the ~5%-priced bucket resolves Yes
# only 0.8-3.7% of the time. That is a STATIC, price-level mispricing, and a
# wallet that simply sells cheap contracts harvests it in every period,
# forever, without forecasting anything. Such a wallet shows exactly the
# signature this module tests for -- an edge in P1 that predicts its edge in
# P2 -- so "persistence" on its own cannot distinguish price-forecasting
# skill from bias harvesting.
#
# The decomposition below separates them using nothing but the sample
# itself. q(p), the empirical probability that a token trading at price p
# actually pays off, is estimated across ALL trades (a market-level
# property, ~200k trades, not a per-wallet fit). Each trade's
# bias-explained P&L is then side_sign * (q(price) - price): what that
# trade was worth given only where it sat on the price scale, with zero
# information about direction. Subtracting it from a wallet's realized edge
# leaves the part that is NOT explained by price-level mispricing, and the
# persistence test is re-run on that residual. If persistence survives, it
# is forecasting skill. If it collapses, the headline result was the
# calibration finding wearing a different hat.
# ---------------------------------------------------------------------------

PRICE_BINS = [0.0, 0.01, 0.02, 0.035, 0.05, 0.075, 0.10, 0.15, 0.20, 0.30,
              0.40, 0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.925, 0.95, 0.965, 0.98, 0.99, 1.0]
MIN_SIZE_PER_PRICE_BIN = 500.0  # below this a bin's q(p) is too thin to use


def price_calibration_curve(df: pd.DataFrame, bins: list[float] | None = None) -> pd.DataFrame:
    """Empirical q(p): of all the contract size traded at prices inside each
    bin, what fraction actually paid off. This is the market's own
    calibration curve, measured trade-weighted rather than event-weighted."""
    bins = bins or PRICE_BINS
    d = df[(df["price"] > 0) & (df["price"] < 1)].copy()
    d["price_bin"] = pd.cut(d["price"], bins=bins, include_lowest=True)
    g = d.groupby("price_bin", observed=True).apply(
        lambda x: pd.Series({
            "size": x["size"].sum(),
            "mean_price": np.average(x["price"], weights=x["size"]),
            "q": np.average(x["outcome"], weights=x["size"]),
            "n_trades": len(x),
        }),
        include_groups=False,
    )
    return g.reset_index()


def _q_lookup(curve: pd.DataFrame):
    """Interpolator over the usable bins of the calibration curve. Falls
    back to q(p) = p (i.e. "no mispricing at this price level") wherever
    there isn't enough traded size to say otherwise, so a thin bin can never
    manufacture a bias-explained edge."""
    usable = curve[curve["size"] >= MIN_SIZE_PER_PRICE_BIN]
    if len(usable) < 3:
        return lambda p: np.asarray(p, dtype=float)
    xs = usable["mean_price"].to_numpy(dtype=float)
    ys = usable["q"].to_numpy(dtype=float)
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    return lambda p: np.interp(np.asarray(p, dtype=float), xs, ys)


def add_bias_explained_pnl(df: pd.DataFrame, curve: pd.DataFrame | None = None) -> pd.DataFrame:
    """Adds `bias_pnl`: the P&L each trade earns from price-level
    mispricing alone, and `skill_pnl`: what is left of its realized P&L
    after that is removed."""
    curve = price_calibration_curve(df) if curve is None else curve
    q = _q_lookup(curve)
    out = df.copy()
    out["bias_pnl"] = out["side_sign"] * (q(out["price"].to_numpy()) - out["price"].to_numpy()) * out["size"]
    out["skill_pnl"] = out["pnl"] - out["bias_pnl"]
    return out


def _period_edges(df: pd.DataFrame, pnl_col: str) -> pd.DataFrame:
    g = df.groupby("wallet").agg(
        pnl=(pnl_col, "sum"),
        notional=("notional", "sum"),
        n=(pnl_col, "size"),
        mean_price=("price", "mean"),
    )
    g["edge"] = g["pnl"] / g["notional"]
    return g


def split_sample_persistence(
    df: pd.DataFrame,
    split_date: date,
    min_trades_per_period: int = 3,
    n_permutations: int = 2000,
    seed: int = 0,
) -> dict:
    """The real test: does period-1 edge predict period-2 (out-of-sample)
    edge?

    Reports the correlation three ways (raw Pearson, Spearman, winsorized
    Pearson), each with a permutation p-value; a leave-one-out sensitivity
    showing how much any single wallet moves the raw number; the
    top-vs-bottom decile comparison; and -- the question that decides what
    the result MEANS -- the same test re-run on edges with the
    favorite-longshot component removed (see the section comment above).
    """
    df = add_bias_explained_pnl(df)
    p1_raw = df[df["event_date"] < split_date]
    p2_raw = df[df["event_date"] >= split_date]

    p1 = _period_edges(p1_raw, "pnl").add_suffix("1")
    p2 = _period_edges(p2_raw, "pnl").add_suffix("2")
    both = p1.join(p2, how="inner")
    both = both[(both["n1"] >= min_trades_per_period) & (both["n2"] >= min_trades_per_period)]
    if len(both) < 8:
        return {"n_wallets": int(len(both)), "note": "too few wallets active in both periods to test"}

    e1 = both["edge1"].to_numpy(dtype=float)
    e2 = both["edge2"].to_numpy(dtype=float)
    correlations = _all_three_correlations(e1, e2, n_permutations, seed)

    # Same wallets, same periods, edges recomputed with the price-level
    # mispricing stripped out of every trade.
    s1 = _period_edges(p1_raw, "skill_pnl").add_suffix("1")
    s2 = _period_edges(p2_raw, "skill_pnl").add_suffix("2")
    resid = s1.join(s2, how="inner").reindex(both.index).dropna()
    residual_correlations = _all_three_correlations(
        resid["edge1"].to_numpy(dtype=float),
        resid["edge2"].to_numpy(dtype=float),
        n_permutations,
        seed + 10,
    )

    bias_share = float(df["bias_pnl"].sum() / df["pnl"].sum()) if df["pnl"].sum() else float("nan")

    n_decile = max(1, len(both) // 10)
    top = both.nlargest(n_decile, "edge1")
    bottom = both.nsmallest(n_decile, "edge1")

    return {
        "n_wallets": int(len(both)),
        "split_date": str(split_date),
        # Headline, kept under the original key names so this stays
        # comparable with earlier runs -- but read `correlations` below,
        # not this alone.
        "correlation_edge1_vs_edge2": correlations["pearson"]["r"],
        "permutation_p_value": correlations["pearson"]["p"],
        "n_permutations": n_permutations,
        "correlations": correlations,
        "outlier_sensitivity": _outlier_sensitivity(e1, e2),
        "longshot_control": {
            "population_bias_explained_share_of_pnl": bias_share,
            "corr_edge1_vs_mean_trade_price1": _pearson(e1, both["mean_price1"].to_numpy(dtype=float)),
            "residual_correlations": residual_correlations,
            "n_wallets_residual": int(len(resid)),
        },
        "top_decile_p1_edge_mean": float(top["edge1"].mean()),
        "top_decile_p2_edge_mean": float(top["edge2"].mean()),
        "bottom_decile_p1_edge_mean": float(bottom["edge1"].mean()),
        "bottom_decile_p2_edge_mean": float(bottom["edge2"].mean()),
        "population_p2_edge_mean": float(both["edge2"].mean()),
    }
