"""FastAPI app: the JSON API plus (in production use) the built frontend,
served from the same process/port so there's no CORS to deal with for the
single-command `./run.sh` path. CORS is still enabled for localhost so the
frontend's own Vite dev server (a different port, used only when actively
developing the UI -- see frontend/README section in the top-level README)
can call this API directly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from common.assets import ASSET_REGISTRY
from crowds import CROWD_ASSETS, build_crowds_payload
from orchestrator import get_dashboard_payload
from signals.sentiment import fetch_stocktwits

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from combined_signals_service import (  # noqa: E402
    load_smart_money_backtest,
    load_smart_money_portfolio,
    lookup_ticker,
)

app = FastAPI(title="Wisdom of the Markets")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/assets")
def list_assets():
    return {
        "assets": [
            {
                "symbol": a.symbol,
                "display_name": a.display_name,
                "adapters": a.adapters,
                "enabled": a.enabled,
            }
            for a in ASSET_REGISTRY.values()
        ]
    }


@app.get("/api/forecast/{symbol}")
def forecast(symbol: str, refresh: bool = False):
    return get_dashboard_payload(symbol.upper(), force_refresh=refresh)


@app.get("/api/smart-money/portfolio")
def smart_money_portfolio():
    """Pre-computed S&P 500 four-signal portfolio (smart money, analyst,
    options, retail attention) -- this takes ~50 minutes to build (options
    liquidity checks and Wikipedia rate limits are the bottleneck), so it's
    served from the last run's saved JSON, not recomputed per request."""
    try:
        return load_smart_money_portfolio()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/smart-money/backtest")
def smart_money_backtest():
    """Pre-computed backtest results: the fixed 10-fund panel (2021-2026)
    and the objective N=10/20/50 rolling panels (2013-2026), plus
    significance testing and factor decomposition against SPY."""
    try:
        return load_smart_money_backtest()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/lookup/{ticker}")
def stock_lookup(ticker: str):
    """Live lookup for any ticker -- smart money weight from the cached
    N=50 13F panel, plus a live fetch of analyst consensus,
    options-implied distribution, Wikipedia retail attention and StockTwits
    bullish/bearish tags. Unlike the S&P 500 portfolio above, this computes
    fresh per request (a single ticker is fast enough: a few seconds, not
    tens of minutes)."""
    try:
        result = lookup_ticker(ticker.upper())
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    result["stocktwits"] = fetch_stocktwits(ticker.upper())
    return result


@app.get("/api/crowds/{asset}")
def crowds_view(asset: str):
    """Every independent crowd for BTC/ETH/GOLD/OIL side by side:
    prediction markets, options, futures & perps, the EIA's forecast (oil),
    CFTC positioning and retail sentiment -- see crowds.py. Live, cached
    for a minute."""
    asset = asset.upper()
    if asset not in CROWD_ASSETS:
        raise HTTPException(status_code=404, detail=f"asset must be one of {', '.join(CROWD_ASSETS)}")
    return build_crowds_payload(asset)


# Serve the built frontend (frontend/npm run build -> frontend/dist), if
# present, at the site root. Mounted last so it never shadows /api routes.
_frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
