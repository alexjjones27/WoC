"""Retail-sentiment crowd, alongside the Wikipedia page-view signal the
dashboard already had (src/wikipedia_attention.py):

  - StockTwits: posts where the author tagged themselves Bullish or
    Bearish. Free public stream, no key, confirmed live 2026-09-23 for
    BTC.X, ETH.X, GLD, USO and ordinary tickers. Gold and oil use their
    ETFs (GLD, USO) because that's where StockTwits' gold/oil chatter is.
    Each page is the latest 30 posts; two pages are read. Untagged posts
    are ignored, so the sample is small (typically 20-50 tagged posts
    covering the last few hours to a day) -- a mood reading, not a poll.
  - Crypto Fear & Greed index (alternative.me): 0-100 composite of
    volatility, momentum, social volume, dominance and search trends.
    Crypto-wide, so the same number for BTC and ETH.
  - MVRV (CoinMetrics community API): market cap / "realized cap" (every
    coin valued at the price it last moved on-chain). Roughly, holders'
    average unrealized profit: 1.0 = the average holder is at break-even,
    historically >3 has marked overheated tops and <1 capitulation.
    BTC/ETH only. It's an on-chain measure of the holder crowd, independent
    of any exchange.

StockTwits and Fear & Greed lean bullish by construction (people who post
about an asset mostly own it), so both are shown next to their own recent
average where possible, not as absolute calls.
"""
from __future__ import annotations

from adapters.http import get_json
from cache import TTLCache

STOCKTWITS_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
FEAR_GREED_URL = "https://api.alternative.me/fng/"
COINMETRICS_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"

STOCKTWITS_SYMBOL = {"BTC": "BTC.X", "ETH": "ETH.X", "GOLD": "GLD", "OIL": "USO"}
COINMETRICS_ASSET = {"BTC": "btc", "ETH": "eth"}
STOCKTWITS_PAGES = 2

_cache = TTLCache(ttl_seconds=10 * 60)


def tally_stocktwits(messages: list[dict]) -> dict:
    bull = sum(1 for m in messages if ((m.get("entities") or {}).get("sentiment") or {}).get("basic") == "Bullish")
    bear = sum(1 for m in messages if ((m.get("entities") or {}).get("sentiment") or {}).get("basic") == "Bearish")
    times = sorted(m["created_at"] for m in messages if m.get("created_at"))
    tagged = bull + bear
    return {
        "bullish": bull,
        "bearish": bear,
        "untagged": len(messages) - tagged,
        "bullish_share": bull / tagged if tagged else None,
        "oldest_post": times[0] if times else None,
        "newest_post": times[-1] if times else None,
    }


def fetch_stocktwits(symbol: str) -> dict:
    key = f"st:{symbol}"
    cached = _cache.get(key)
    if cached is not None:
        return cached
    messages: list[dict] = []
    watchers = None
    max_id = None
    try:
        for _ in range(STOCKTWITS_PAGES):
            data = get_json(STOCKTWITS_URL.format(symbol=symbol), {"max": max_id} if max_id else None)
            page = data.get("messages", [])
            watchers = watchers or (data.get("symbol") or {}).get("watchlist_count")
            messages += page
            if not page:
                break
            max_id = page[-1]["id"] - 1
    except Exception as exc:  # noqa: BLE001
        if not messages:
            return {"error": f"StockTwits fetch failed: {exc}", "symbol": symbol}
    result = {"symbol": symbol, "watchers": watchers, "posts_read": len(messages), **tally_stocktwits(messages),
              "source_url": f"https://stocktwits.com/symbol/{symbol}"}
    _cache.set(key, result)
    return result


def fetch_fear_greed() -> dict:
    cached = _cache.get("fng")
    if cached is not None:
        return cached
    try:
        rows = get_json(FEAR_GREED_URL, {"limit": 31})["data"]
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Fear & Greed fetch failed: {exc}"}
    values = [int(r["value"]) for r in rows]
    result = {
        "value": values[0],
        "classification": rows[0]["value_classification"],
        "avg_30d": sum(values[1:]) / len(values[1:]) if len(values) > 1 else None,
        "source_url": "https://alternative.me/crypto/fear-and-greed-index/",
    }
    _cache.set("fng", result)
    return result


def fetch_mvrv(asset: str) -> dict | None:
    cm = COINMETRICS_ASSET.get(asset)
    if cm is None:
        return None
    key = f"mvrv:{cm}"
    cached = _cache.get(key)
    if cached is not None:
        return cached
    try:
        rows = get_json(COINMETRICS_URL, {"assets": cm, "metrics": "CapMVRVCur", "frequency": "1d",
                                          "page_size": 1000, "paging_from": "end"})["data"]
    except Exception as exc:  # noqa: BLE001
        return {"error": f"CoinMetrics fetch failed: {exc}"}
    rows = sorted((r for r in rows if r.get("CapMVRVCur")), key=lambda r: r["time"])
    vals = [float(r["CapMVRVCur"]) for r in rows]
    if not vals:
        return {"error": "CoinMetrics returned no MVRV data"}
    latest = vals[-1]
    result = {
        "value": latest,
        "as_of": rows[-1]["time"][:10],
        "percentile_history": sum(1 for v in vals if v <= latest) / len(vals),
        "history_days": len(vals),
        "source_url": "https://coinmetrics.io/community-network-data/",
    }
    _cache.set(key, result)
    return result


def fetch_sentiment(asset: str) -> dict:
    symbol = STOCKTWITS_SYMBOL.get(asset)
    return {
        "stocktwits": fetch_stocktwits(symbol) if symbol else None,
        "fear_greed": fetch_fear_greed() if asset in COINMETRICS_ASSET else None,
        "mvrv": fetch_mvrv(asset),
    }
