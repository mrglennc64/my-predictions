"""Gamma API client — market/event metadata. No auth required."""
import json
import requests

BASE = "https://gamma-api.polymarket.com"
_session = requests.Session()
_session.headers["User-Agent"] = "contest-edge/0.1"


def _get(path: str, **params) -> list | dict:
    r = _session.get(f"{BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def get_events(closed: bool = False, limit: int = 100, offset: int = 0,
               order: str = "volume24hr", tag_id: int | None = None,
               ascending: bool = False, **extra) -> list[dict]:
    params = {"closed": str(closed).lower(), "limit": limit, "offset": offset,
              "order": order, "ascending": str(ascending).lower(), **extra}
    if tag_id is not None:
        params["tag_id"] = tag_id
    return _get("/events", **params)


def get_markets(closed: bool = False, limit: int = 100, offset: int = 0,
                order: str = "volume24hr", **extra) -> list[dict]:
    params = {"closed": str(closed).lower(), "limit": limit, "offset": offset,
              "order": order, "ascending": "false", **extra}
    return _get("/markets", **params)


def iter_open_events(max_events: int = 1000, tag_id: int | None = None):
    """Yield open events, paginating until exhausted or max_events reached."""
    offset = 0
    while offset < max_events:
        batch = get_events(limit=100, offset=offset, tag_id=tag_id)
        if not batch:
            return
        yield from batch
        if len(batch) < 100:
            return
        offset += 100


def get_tag_id(slug: str) -> int | None:
    """Resolve a tag slug (e.g. 'mls', 'mlb', 'nba') to its numeric id."""
    try:
        data = _get(f"/tags/slug/{slug}")
    except requests.HTTPError:
        return None
    if isinstance(data, list):
        data = data[0] if data else {}
    tag_id = data.get("id")
    return int(tag_id) if tag_id is not None else None


def parse_json_field(value) -> list:
    """Gamma encodes list fields (outcomePrices, clobTokenIds) as JSON strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return []
