"""The retail 'other end of the error bar': not smart money's dollar
conviction, but genuine crowd ATTENTION -- how many people are actively
looking a company up on Wikipedia. This is a real, well-precedented proxy
in the academic literature (Moat et al. 2013, "Quantifying Wikipedia Usage
Patterns Before Stock Market Moves") for what the retail crowd is focused
on, not a novelty metric invented for this project.

It is NOT the same thing as retail opinion/sentiment (which would need
something like Reddit, requiring the user's own API credentials -- see
module docstring history in this session for why StockTwits/Estimize/
TradingView aren't viable free alternatives). Attention tells you WHAT
people are looking at, not what they think will happen to it. Both data
source and limitation should be represented honestly wherever this is used.

Everything here is free, public, no API key -- Wikipedia's own REST
pageviews API and MediaWiki opensearch API. Rate-limited in practice
(confirmed live: a burst of requests without delay gets a 429), so lookups
are both cached to disk and paced with a small delay.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / "data" / "raw" / "wikipedia_attention"

HEADERS = {"User-Agent": "wisdom-of-crowds-research contact@example.com (educational/personal research)"}
REQUEST_DELAY_SECONDS = 0.6


def _get_with_backoff(url: str, params: dict | None = None, max_retries: int = 6) -> requests.Response:
    """Confirmed live: a burst of Wikipedia API requests gets a flat 429
    with no Retry-After guidance -- exponential backoff rather than a fixed
    delay, since the right wait time isn't otherwise knowable."""
    delay = 3.0
    for attempt in range(max_retries):
        resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
        if resp.status_code != 429:
            return resp
        time.sleep(delay)
        delay *= 2
    return resp


def _title_cache_path() -> Path:
    return CACHE_DIR / "title_resolution_cache.json"


def _load_title_cache() -> dict:
    p = _title_cache_path()
    return json.loads(p.read_text()) if p.exists() else {}


def _save_title_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _title_cache_path().write_text(json.dumps(cache, indent=2))


def _resolve_redirect(title: str) -> str | None:
    """Follows a Wikipedia redirect to its canonical target. Confirmed live
    this matters: "Amazon Com Inc" IS a real Wikipedia page, but it's a bare
    redirect stub to "Amazon (company)" -- querying pageviews for the
    redirect title itself returns near-zero traffic (real readers land on
    the target after redirecting; Wikipedia's pageview stats track that
    target, not the stub), not "no data for Amazon." Same issue hit "Kraft
    Heinz Co" -> "Kraft Heinz" and "Micron Technology Inc" -> "Micron
    Technology." Returns None if the title doesn't exist at all."""
    resp = _get_with_backoff(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "titles": title, "redirects": 1, "format": "json"},
    )
    time.sleep(REQUEST_DELAY_SECONDS)
    if resp.status_code != 200:
        return None
    pages = resp.json().get("query", {}).get("pages", {})
    for page in pages.values():
        if "missing" in page:
            return None
        return page.get("title")
    return None


def resolve_wikipedia_title(query: str) -> str | None:
    """Best-effort company-name -> Wikipedia article title resolution.
    Two-step: opensearch finds a candidate title (handles messy input like
    "AMAZON COM INC" title-cased), then a redirects=1 lookup resolves that
    candidate to its canonical target if it's itself a redirect stub (see
    _resolve_redirect). Cached; not re-queried once resolved (or confirmed
    unresolvable) for a given query string."""
    cache = _load_title_cache()
    if query in cache:
        return cache[query]

    resp = _get_with_backoff(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "opensearch", "search": query, "limit": 3, "format": "json"},
    )
    time.sleep(REQUEST_DELAY_SECONDS)
    if resp.status_code != 200:
        return None  # transient failure -- don't cache, allow retry later
    data = resp.json()
    titles = data[1] if len(data) > 1 else []
    candidate = titles[0] if titles else None
    result = _resolve_redirect(candidate) if candidate else None
    cache[query] = result
    _save_title_cache(cache)
    return result


def fetch_pageviews_cached(article_title: str, days: int = 180) -> pd.Series | None:
    safe_name = article_title.replace("/", "_")
    cache_path = CACHE_DIR / f"pageviews__{safe_name}.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        if cached is None:
            return None
        return pd.Series(cached["views"], index=pd.to_datetime(cached["dates"]))

    end = pd.Timestamp.now().normalize()
    start = end - pd.Timedelta(days=days)
    wiki_title = article_title.replace(" ", "_")
    url = (
        f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"en.wikipedia/all-access/user/{wiki_title}/daily/"
        f"{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}"
    )
    resp = _get_with_backoff(url)
    time.sleep(REQUEST_DELAY_SECONDS)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if resp.status_code == 404:
        cache_path.write_text("null")  # genuinely no pageview data for this article -- a real negative result
        return None
    if resp.status_code != 200:
        return None  # transient failure (429, 5xx) -- don't cache, allow retry on a later run
    items = resp.json().get("items", [])
    if not items:
        cache_path.write_text("null")
        return None
    dates = [pd.to_datetime(it["timestamp"][:8]) for it in items]
    views = [it["views"] for it in items]
    cache_path.write_text(json.dumps({"dates": [str(d) for d in dates], "views": views}))
    return pd.Series(views, index=pd.DatetimeIndex(dates))


def compute_attention_score(pageviews: pd.Series, recent_days: int = 14, baseline_days: int = 90) -> dict | None:
    """recent/baseline ratio -- a spike above 1.0 means more attention lately
    than the trailing baseline; this is the 'abnormal views' idea from the
    Wikipedia-attention literature, just with a simple ratio instead of a
    fitted expected-views model."""
    if pageviews is None or len(pageviews) < baseline_days // 2:
        return None
    pageviews = pageviews.sort_index()
    recent = pageviews.iloc[-recent_days:]
    baseline = pageviews.iloc[-baseline_days:-recent_days] if len(pageviews) > baseline_days else pageviews.iloc[:-recent_days]
    if len(baseline) == 0 or baseline.mean() == 0:
        return None
    recent_avg = float(recent.mean())
    baseline_avg = float(baseline.mean())
    return {
        "recent_avg_daily_views": recent_avg,
        "baseline_avg_daily_views": baseline_avg,
        "attention_ratio": recent_avg / baseline_avg,
        "total_recent_views": int(recent.sum()),
    }


def get_attention_for_company(company_query: str) -> dict | None:
    title = resolve_wikipedia_title(company_query)
    if not title:
        return None
    pageviews = fetch_pageviews_cached(title)
    score = compute_attention_score(pageviews)
    if score is None:
        return None
    score["resolved_title"] = title
    return score
