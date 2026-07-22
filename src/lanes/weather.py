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
    "los angeles": (34.05, -118.24, "fahrenheit"),
    "las vegas": (36.08, -115.15, "fahrenheit"),
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

# Station-vs-gridpoint offset fitted on resolved markets (app/weather_bias.py,
# train window 07-11..07-15, settled-bucket midpoint minus forecast). The
# miami/nyc magnitudes are too large for pure station bias — likely a
# resolution-definition mismatch; the empirical offset still prices better on
# held-out days, but READ THOSE MARKETS' RULES before staking anything.
STATION_BIAS = {"atlanta": +1.3, "dallas": +2.4, "miami": -9.8, "nyc": -8.5}


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


ENSEMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"


def _ensemble_maxes(city: str, d: date) -> list[float]:
    """Daily-max distribution across the 31 GFS ensemble members — empirical
    spread instead of a deterministic forecast plus a guessed sigma."""
    lat, lon, unit = CITIES[city]
    key = (city, d.isoformat())
    if key not in _forecast_cache:
        _forecast_cache[key] = []
        try:
            r = requests.get(ENSEMBLE, params={
                "latitude": lat, "longitude": lon,
                "hourly": "temperature_2m", "models": "gfs_seamless",
                "temperature_unit": unit, "timezone": "auto",
                "forecast_days": 7}, timeout=60)
            h = r.json().get("hourly", {})
            times = h.get("time", [])
            idx = [i for i, t in enumerate(times)
                   if str(t).startswith(d.isoformat())]
            maxes = []
            for k, vals in h.items():
                if k == "time" or not k.startswith("temperature_2m"):
                    continue
                vs = [vals[i] for i in idx
                      if i < len(vals) and vals[i] is not None]
                if vs:
                    maxes.append(max(vs))
            _forecast_cache[key] = maxes
        except requests.RequestException:
            pass
    return _forecast_cache[key]


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
        members = _ensemble_maxes(city, d)
        if len(members) < 10:
            continue
        offset = STATION_BIAS.get(city, 0.0)
        members = [m + offset for m in members]
        # per-member kernel absorbs station-vs-gridpoint noise; the SPREAD
        # comes from the ensemble itself, not an assumed sigma
        unit = CITIES[city][2]
        s0 = 0.9 if unit == "fahrenheit" else 0.5
        lo, hi = bucket
        p = sum(_phi((hi - m) / s0) - _phi((lo - m) / s0)
                for m in members) / len(members)
        r.model_p = round(min(max(p, 0.001), 0.999), 3)
        n += 1
    return n
