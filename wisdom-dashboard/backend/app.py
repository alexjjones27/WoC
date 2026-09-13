"""FastAPI app: the JSON API plus (in production use) the built frontend,
served from the same process/port so there's no CORS to deal with for the
single-command `./run.sh` path. CORS is still enabled for localhost so the
frontend's own Vite dev server (a different port, used only when actively
developing the UI -- see frontend/README section in the top-level README)
can call this API directly.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from common.assets import ASSET_REGISTRY
from orchestrator import get_dashboard_payload

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


# Serve the built frontend (frontend/npm run build -> frontend/dist), if
# present, at the site root. Mounted last so it never shadows /api routes.
_frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
