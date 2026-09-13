"""How concentrated is trading behind a Polymarket market's implied
probability, and does trade direction show a herding/momentum signature?

Motivated by a direct question: does the wisdom-dashboard's aggregation
treat a market's volume as if it represents many independent opinions,
when it might really be a handful of large traders? Polymarket's public
trade feed (data-api.polymarket.com/trades, wallet-attributed, no auth)
makes this directly checkable -- Kalshi (a regulated DCM) and Manifold
expose no equivalent, so this is Polymarket-only by necessity, not choice.

Two measures, both computed from the same trade list:

  1. TRADER CONCENTRATION (Herfindahl-Hirschman Index, HHI): the standard
     economics measure of market concentration, applied here to volume
     share by wallet instead of firm market share. HHI = sum(share_i^2)
     over each wallet's fraction of total notional traded; ranges from
     ~1/N (perfectly even across N wallets) to 1 (one wallet does
     everything). 1/HHI is the "effective number of equally-sized
     participants" this concentration is equivalent to -- e.g. HHI=0.15
     behaves, for concentration purposes, like ~6.7 equal-sized traders,
     however many actual wallets traded.

  2. HERDING / MOMENTUM SIGNATURE: lag-k autocorrelation of trade
     direction (+1 = a trade that pushes the "Yes" probability up i.e.
     BUY-Yes or SELL-No; -1 = the reverse), trades ordered by time.
     Independent, fresh assessments would show autocorrelation near 0;
     positive autocorrelation means a directional trade tends to be
     followed by more same-direction trades more often than chance --
     consistent with traders reacting to the recent price move itself
     (momentum/anchoring) rather than each contributing an independent
     read, which is exactly the failure mode "wisdom of crowds" arguments
     assume away.

Neither measure by itself proves non-independence (concentrated volume
could still reflect genuinely different, independently-formed views held
by a few well-capitalized traders; autocorrelation could partly reflect
legitimate sequential information arrival, not pure herding) -- they're
diagnostic, not a hard correction, which is why this module stops at
measuring rather than silently discounting anything.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import numpy as np

DATA_API_BASE = "https://data-api.polymarket.com"
USER_AGENT = "Mozilla/5.0 (research; contact via repo)"


def _request_json(url: str, retries: int = 4, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
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


def fetch_trades(condition_id: str, max_trades: int = 5000) -> list[dict]:
    """All trades for one market (a single outcome token's condition),
    newest-first per the API's own default order, capped at `max_trades`
    (a very liquid market can have far more; this is a diagnostic sample,
    not an exhaustive audit)."""
    trades: list[dict] = []
    offset = 0
    page_size = 500
    while offset < max_trades:
        batch = _request_json(f"{DATA_API_BASE}/trades?market={condition_id}&limit={page_size}&offset={offset}")
        if not batch:
            break
        trades.extend(batch)
        offset += len(batch)
        if len(batch) < page_size:
            break
    return trades


@dataclass
class ConcentrationResult:
    condition_id: str
    n_trades: int
    n_wallets: int
    total_notional: float
    hhi: float
    effective_traders: float
    top5_share: float
    top10_share: float
    time_span_days: float
    lag1_autocorr: float
    lag5_autocorr: float


def analyze(condition_id: str, trades: list[dict] | None = None) -> ConcentrationResult | None:
    if trades is None:
        trades = fetch_trades(condition_id)
    if len(trades) < 10:
        return None  # too few trades for either measure to mean anything
    trades = sorted(trades, key=lambda t: t["timestamp"])

    notional_by_wallet: dict[str, float] = {}
    for t in trades:
        w = t["proxyWallet"]
        notional_by_wallet[w] = notional_by_wallet.get(w, 0.0) + float(t["size"]) * float(t["price"])
    total_notional = sum(notional_by_wallet.values())
    shares = np.array(sorted(notional_by_wallet.values(), reverse=True)) / total_notional
    hhi = float(np.sum(shares ** 2))

    direction = np.array([
        1.0 if (t["outcome"] == "Yes" and t["side"] == "BUY") or (t["outcome"] == "No" and t["side"] == "SELL") else -1.0
        for t in trades
    ])
    lag1 = float(np.corrcoef(direction[:-1], direction[1:])[0, 1]) if len(direction) > 2 else float("nan")
    lag5 = float(np.corrcoef(direction[:-5], direction[5:])[0, 1]) if len(direction) > 6 else float("nan")

    return ConcentrationResult(
        condition_id=condition_id,
        n_trades=len(trades),
        n_wallets=len(notional_by_wallet),
        total_notional=total_notional,
        hhi=hhi,
        effective_traders=1.0 / hhi,
        top5_share=float(shares[:5].sum()),
        top10_share=float(shares[:10].sum()),
        time_span_days=(trades[-1]["timestamp"] - trades[0]["timestamp"]) / 86400.0,
        lag1_autocorr=lag1,
        lag5_autocorr=lag5,
    )
