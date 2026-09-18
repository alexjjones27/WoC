"""Minimal retrying JSON/CSV fetcher.

Standard library only, matching the convention in this repo's other data
modules (src/btc_price_market_calibration.py et al.) -- no requests dependency
so the ingest layer stays installable anywhere.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_UA = "WoC-equity-forecaster/0.1 (research; contact via repository)"


class FetchError(RuntimeError):
    """Raised when a URL could not be fetched after all retries."""


def fetch_bytes(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 30.0,
    retries: int = 4,
    backoff: float = 2.0,
) -> bytes:
    """GET ``url`` with exponential backoff. Raises FetchError on final failure.

    4xx responses other than 429 are not retried: a bad API key or an unknown
    symbol will not fix itself, and retrying hides the real error from the
    caller.
    """
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    hdrs = {"User-Agent": DEFAULT_UA, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)

    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:  # noqa: PERF203 - need per-attempt policy
            last = exc
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # pragma: no cover - diagnostic path only
                pass
            if 400 <= exc.code < 500 and exc.code != 429:
                raise FetchError(f"HTTP {exc.code} for {url}: {body}") from exc
        except Exception as exc:  # network-level failure; retry
            last = exc
        if attempt < retries - 1:
            time.sleep(backoff * (2**attempt))
    raise FetchError(f"failed to fetch {url}: {last}")


def fetch_json(url: str, **kwargs):
    """GET ``url`` and parse the body as JSON."""
    raw = fetch_bytes(url, **kwargs)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FetchError(f"non-JSON response from {url}: {raw[:200]!r}") from exc
