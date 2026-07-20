"""CLOB API client — order books, prices, history. No auth required for reads."""
import requests

BASE = "https://clob.polymarket.com"
_session = requests.Session()
_session.headers["User-Agent"] = "contest-edge/0.1"


def _get(path: str, **params):
    r = _session.get(f"{BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def get_book(token_id: str) -> dict:
    return _get("/book", token_id=token_id)


def get_price(token_id: str, side: str) -> dict:
    """side: 'buy' returns best ask, 'sell' returns best bid."""
    return _get("/price", token_id=token_id, side=side)


def get_midpoint(token_id: str) -> dict:
    return _get("/midpoint", token_id=token_id)


def get_prices_history(token_id: str, interval: str = "1d", fidelity: int = 5,
                       start_ts: int | None = None, end_ts: int | None = None) -> list[dict]:
    params = {"market": token_id, "interval": interval, "fidelity": fidelity}
    if start_ts:
        params["startTs"] = start_ts
    if end_ts:
        params["endTs"] = end_ts
    data = _get("/prices-history", **params)
    return data.get("history", [])
