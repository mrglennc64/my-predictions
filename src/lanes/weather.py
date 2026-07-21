"""Weather lane model — Open-Meteo (free, no key) daily-max forecasts vs
Polymarket's "Highest temperature in <city> on <date>" bucket markets.

Model: the forecast max is the mean of a normal whose sigma grows with lead
time (verification literature: ~1.3F day-of, ~2.5F at 2+ days). Each bucket's
probability is the normal mass inside its range. The crowd here is retail;
the forecast is a real numerical model — this is the softest matchup we have.
"""
import math
import re
from datetime import date, datetime, timezone

import requests

CITIES = {
    "nyc": (40.78, -73.97, "fahrenheit"), "new york": (40.78, -73.97, "fahrenheit"),
    "los angeles": (34.05, -118.24, "fahrenheit"), "la": (34.05, -118.24, "fahrenheit"),
    "chicago": (41.98, -87.90, "fahrenheit"), "miami": (25.79, -80.32, "fahrenheit"),
    "philadelphia": (39.87, -75.23, "fahrenheit"), "atlanta": (33.63, -84.44, "fahrenheit"),
    "dallas": (32.90, -97.04, "fahrenheit"), "houston": (29.65, -95.28, "fahrenheit"),
    "seattle": (47.45, -122.31, "fahrenheit"), "denver": (39.85, -104.66, "fahrenheit"),
    "seoul": (37.57, 126.98, "celsius"), "london": (51.51, -0.13, "celsius"),
    "paris": (48.86, 2.35, "celsius"), "tokyo": (35.68, 139.69, "celsius"),
    "moscow": (55.75, 37.62, "celsius"), "toronto": (43.68, -79.63, "celsius"),
}
_MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}

# "73-74°F" | "75°F or above" | "72°F or below" | "less than 25°C"
_RANGE = re.compile(r"(\d+)\s*[-–]\s*(\d+)")
_ABOVE = re.compile(r"(\d+)\s*°?\s*[FC]?\s*(?:or above|or higher|\+)", re.I)
_BELOW = re.compile(r"(\d+)\s*°?\s*[FC]?\s*(?:or below|or lower|or less)", re.I)

_forecast_cache: dict[tuple, dict] = {}


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _city_and_date(slug: str, title: str):
    text = (slug or "").replace("-", " ") + " " + (title or "").lower()
    city = next((c for c in sorted(CITIES, key=len, reverse=True)
                 if c in text), None)
    m = re.search(r"(january|february|march|april|may|june|july|august|"
                  r"september|october|november|december)\s+(\d{1,2})", text)
    if not city or not m:
        return None, None
    today = datetime.now(timezone.utc).date()
    d = date(today.year, _MONTHS[m.group(1)], int(m.group(2)))
    return city, d


def _forecast_max(city: str, d: date) -> float | None:
    lat, lon, unit = CITIES[city]
    key = (city, d.isoformat())
    if key not in _forecast_cache:
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon,
                "daily": "temperature_2m_max", "temperature_unit": unit,
                "timezone": "auto", "forecast_days": 7}, timeout=30)
            data = r.json().get("daily", {})
            _forecast_cache[key] = dict(zip(data.get("time", []),
                                            data.get("temperature_2m_max", [])))
        except requests.RequestException:
            _forecast_cache[key] = {}
    return _forecast_cache[key].get(d.isoformat())


def _bucket(text: str):
    m = _RANGE.search(text)
    if m:
        return float(m.group(1)) - 0.5, float(m.group(2)) + 0.5
    m = _ABOVE.search(text)
    if m:
        return float(m.group(1)) - 0.5, math.inf
    m = _BELOW.search(text)
    if m:
        return -math.inf, float(m.group(1)) + 0.5
    return None


def attach(rows) -> int:
    today = datetime.now(timezone.utc).date()
    n = 0
    for r in rows:
        city, d = _city_and_date(r.event_slug, r.event_title)
        if not city or not d:
            continue
        bucket = _bucket(r.side) or _bucket(r.title)
        if not bucket:
            continue
        fc = _forecast_max(city, d)
        if fc is None:
            continue
        lead = max(0, (d - today).days)
        unit = CITIES[city][2]
        sigma = (1.3, 1.8, 2.5)[min(lead, 2)]
        if unit == "celsius":
            sigma *= 5 / 9
        lo, hi = bucket
        p = _phi((hi - fc) / sigma) - _phi((lo - fc) / sigma)
        r.model_p = round(min(max(p, 0.001), 0.999), 3)
        n += 1
    return n
