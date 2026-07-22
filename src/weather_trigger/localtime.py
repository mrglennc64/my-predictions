"""Requirement 2 (cont.): the local-day boundary, done with a DST-correct
offset — not lon/15.

The old watcher used round(lon/15) for the timezone, which is wrong near DST
transitions and for offset-from-solar cities. Open-Meteo (already a repo
dependency) returns `utc_offset_seconds` that is DST-adjusted for *today*, so
we get the correct local midnight without shipping a tz database. The offset
is injectable, which is what makes the boundary math unit-testable.
"""
from datetime import datetime, timedelta, timezone

import requests

_OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
_offset_cache: dict[str, int] = {}


def local_midnight_epoch(now_utc: datetime, offset_seconds: int) -> int:
    """UTC epoch of the most recent local midnight for a station at this offset.

    Pure and deterministic given (now_utc, offset) — the tested core.
    """
    local = now_utc + timedelta(seconds=offset_seconds)
    local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_midnight = local_midnight - timedelta(seconds=offset_seconds)
    return int(utc_midnight.replace(tzinfo=timezone.utc).timestamp())


def local_date(now_utc: datetime, offset_seconds: int):
    return (now_utc + timedelta(seconds=offset_seconds)).date()


def local_hour(now_utc: datetime, offset_seconds: int) -> int:
    return (now_utc + timedelta(seconds=offset_seconds)).hour


def station_offset_seconds(lat: float, lon: float, cache_key: str) -> int | None:
    """DST-correct current UTC offset for a station, via Open-Meteo (cached)."""
    if cache_key in _offset_cache:
        return _offset_cache[cache_key]
    try:
        r = requests.get(_OPEN_METEO, params={
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "forecast_days": 1}, timeout=30)
        off = int(r.json().get("utc_offset_seconds"))
    except (requests.RequestException, TypeError, ValueError, KeyError):
        return None
    _offset_cache[cache_key] = off
    return off
