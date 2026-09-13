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


def _score_trades(trades_raw: list[dict], resolution_value: float, event_date: date, asset: str) -> list[ScoredTrade]:
    out = []
    for t in trades_raw:
        try:
            price, size = float(t["price"]), float(t["size"])
            side = t["side"]
        except (KeyError, TypeError, ValueError):
            continue
        pnl_per_share = (resolution_value - price) if side == "BUY" else (price - resolution_value)
        out.append(ScoredTrade(wallet=t["proxyWallet"], asset=asset, event_date=event_date, pnl_per_share=pnl_per_share, size=size, notional=size * price))
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
                resolved_yes = float(prices[outcomes.index("Yes")]) > 0.5
            except (KeyError, ValueError, TypeError, IndexError):
                continue
            resolution_value = 1.0 if resolved_yes else 0.0
            try:
                trades_raw = fetch_trades_cached(cond_id)
            except Exception:
                continue
            out.extend(_score_trades(trades_raw, resolution_value, d, "BTC"))
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
    every calendar day."""
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


def collect_oil_trades(max_events: int = MAX_OIL_EVENTS, max_workers: int = 8) -> list[ScoredTrade]:
    events = discover_resolved_oil_touch_events(max_events)

    def process_event(ev_summary: dict) -> list[ScoredTrade]:
        slug = ev_summary["slug"]
        ev = _fetch_oil_event_detail(slug)
        if ev is None:
            return []
        start_date, end_date = ev.get("startDate"), ev.get("endDate")
        if not start_date or not end_date:
            return []
        high_low = _realized_wti_high_low(start_date, end_date)
        if high_low is None:
            return []
        realized_max, realized_min = high_low
        event_day = datetime.fromisoformat(end_date.replace("Z", "+00:00")).date()

        out = []
        for m in ev.get("markets", []):
            if m.get("closed") is False:
                continue  # keep only markets that actually reached resolution
            parsed = _parse_touch_label(m.get("groupItemTitle") or "")
            if parsed is None:
                continue
            direction, threshold = parsed
            resolved_yes = (realized_max >= threshold) if direction == "above" else (realized_min <= threshold)
            cond_id = m.get("conditionId")
            if not cond_id:
                continue
            try:
                trades_raw = fetch_trades_cached(cond_id)
            except Exception:
                continue
            out.extend(_score_trades(trades_raw, 1.0 if resolved_yes else 0.0, event_day, "OIL"))
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
        {"wallet": t.wallet, "asset": t.asset, "event_date": t.event_date, "pnl": t.pnl_per_share * t.size, "notional": t.notional}
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


def split_sample_persistence(df: pd.DataFrame, split_date: date, min_trades_per_period: int = 3, n_permutations: int = 2000, seed: int = 0) -> dict:
    """The real test: does period-1 edge predict period-2 (out-of-sample)
    edge? Returns the correlation, a permutation-test p-value for it (null:
    period-1 and period-2 performance are unrelated -- shuffle the pairing
    between wallets' P1 and P2 edges and see how often |correlation| this
    large happens by chance), and top-vs-bottom decile comparison."""
    p1 = df[df["event_date"] < split_date].groupby("wallet").agg(pnl1=("pnl", "sum"), notional1=("notional", "sum"), n1=("pnl", "size"))
    p2 = df[df["event_date"] >= split_date].groupby("wallet").agg(pnl2=("pnl", "sum"), notional2=("notional", "sum"), n2=("pnl", "size"))
    both = p1.join(p2, how="inner")
    both = both[(both["n1"] >= min_trades_per_period) & (both["n2"] >= min_trades_per_period)]
    if len(both) < 8:
        return {"n_wallets": len(both), "note": "too few wallets active in both periods to test"}

    both["edge1"] = both["pnl1"] / both["notional1"]
    both["edge2"] = both["pnl2"] / both["notional2"]

    observed_corr = float(np.corrcoef(both["edge1"], both["edge2"])[0, 1])

    rng = np.random.default_rng(seed)
    edge2_vals = both["edge2"].to_numpy()
    perm_corrs = np.empty(n_permutations)
    for i in range(n_permutations):
        perm_corrs[i] = np.corrcoef(both["edge1"].to_numpy(), rng.permutation(edge2_vals))[0, 1]
    p_value = float(np.mean(np.abs(perm_corrs) >= abs(observed_corr)))

    n_decile = max(1, len(both) // 10)
    top = both.nlargest(n_decile, "edge1")
    bottom = both.nsmallest(n_decile, "edge1")

    return {
        "n_wallets": int(len(both)),
        "correlation_edge1_vs_edge2": observed_corr,
        "permutation_p_value": p_value,
        "n_permutations": n_permutations,
        "top_decile_p1_edge_mean": float(top["edge1"].mean()),
        "top_decile_p2_edge_mean": float(top["edge2"].mean()),
        "bottom_decile_p1_edge_mean": float(bottom["edge1"].mean()),
        "bottom_decile_p2_edge_mean": float(bottom["edge2"].mean()),
        "population_p2_edge_mean": float(both["edge2"].mean()),
    }