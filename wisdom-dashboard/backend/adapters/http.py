"""Small shared HTTP helper. Deliberately stdlib-only (urllib) so the
adapters package has no hard dependency beyond the standard library --
consistent with this repo's existing Polymarket scripts (src/polymarket_final_pct.py).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "Mozilla/5.0 (wisdom-of-the-markets dashboard; local research tool)"


def get_json(url: str, params: dict | None = None, retries: int = 3, timeout: float = 12.0) -> object:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(min(2 ** attempt, 8))
                last_err = exc
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_err = exc
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"GET {url} failed after {retries} retries: {last_err}")
