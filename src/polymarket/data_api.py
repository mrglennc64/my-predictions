"""Data API client — wallet positions, trades, leaderboard. All public, no auth."""
import requests

BASE = "https://data-api.polymarket.com"
_session = requests.Session()
_session.headers["User-Agent"] = "contest-edge/0.1"


def _get(path: str, **params):
    r = _session.get(f"{BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def get_leaderboard(window: str = "month", rank_by: str = "pnl", limit: int = 50) -> list[dict]:
    """Top traders. window: day|week|month|all. rank_by: pnl|vol."""
    data = _get("/v1/leaderboard", timePeriod=window, orderBy=rank_by, limit=limit)
    # API has returned both a bare list and {"leaderboard": [...]} shapes
    if isinstance(data, dict):
        data = data.get("leaderboard") or data.get("data") or []
    return data


def get_positions(wallet: str, limit: int = 100) -> list[dict]:
    """Open positions for any proxy wallet (public)."""
    return _get("/positions", user=wallet, limit=limit, sortBy="CURRENT", sortDirection="DESC")


def get_trades(wallet: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    params = {"limit": limit, "offset": offset}
    if wallet:
        params["user"] = wallet
    return _get("/trades", **params)
