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

# Coordinates are the RESOLUTION STATIONS from the markets' own rules (each
# resolves at a specific airport via Wunderground) — not city centers. KLAX
# vs downtown LA differs by ~10F in summer; Seoul resolves at Incheon, 40km
# away. Forecasting the wrong point was the model's largest error source.
CITIES = {
    "nyc": (40.7772, -73.8726, "fahrenheit"),          # KLGA LaGuardia
    "new york": (40.7772, -73.8726, "fahrenheit"),
    "los angeles": (33.9425, -118.4081, "fahrenheit"),  # KLAX
    "las vegas": (36.0840, -115.1537, "fahrenheit"),    # KLAS
    "chicago": (41.9786, -87.9048, "fahrenheit"),       # KORD O'Hare
    "miami": (25.7959, -80.2870, "fahrenheit"),         # KMIA
    "philadelphia": (39.8719, -75.2411, "fahrenheit"),  # KPHL
    "atlanta": (33.6407, -84.4277, "fahrenheit"),       # KATL
    "dallas": (32.8471, -96.8518, "fahrenheit"),        # KDAL Love Field
    "houston": (29.6454, -95.2789, "fahrenheit"),       # KHOU Hobby
    "seattle": (47.4489, -122.3094, "fahrenheit"),      # KSEA
    "san francisco": (37.6213, -122.3790, "fahrenheit"),  # KSFO
    "austin": (30.1975, -97.6664, "fahrenheit"),        # KAUS
    "denver": (39.7017, -104.7522, "fahrenheit"),       # KBKF Buckley
    "seoul": (37.4602, 126.4407, "celsius"),            # Incheon Intl
    "london": (51.5053, 0.0553, "celsius"),             # City Airport
    "paris": (48.9694, 2.4414, "celsius"),              # Le Bourget
    "tokyo": (35.5494, 139.7798, "celsius"),            # Haneda
    "toronto": (43.6777, -79.6248, "celsius"),          # Pearson
    "munich": (48.3538, 11.7861, "celsius"),
    "madrid": (40.4722, -3.5609, "celsius"),            # Barajas
    "amsterdam": (52.3105, 4.7683, "celsius"),          # Schiphol
    "milan": (45.6301, 8.7255, "celsius"),              # Malpensa
    "warsaw": (52.1657, 20.9671, "celsius"),            # Chopin
    "helsinki": (60.3183, 24.9630, "celsius"),          # Vantaa
    "singapore": (1.3644, 103.9915, "celsius"),         # Changi
    "taipei": (25.0694, 121.5525, "celsius"),           # Songshan
    "mexico city": (19.4363, -99.0721, "celsius"),      # Benito Juarez
}
_MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}

# "73-74°F" | "75°F or above" | "72°F or below" | "less than 25°C"
_RANGE = re.compile(r"(\d+)\s*[-–]\s*(\d+)")
_ABOVE = re.compile(r"(\d+)\s*°?\s*[FC]?\s*(?:or above|or higher|\+)", re.I)
_BELOW = re.compile(r"(\d+)\s*°?\s*[FC]?\s*(?:or below|or lower|or less)", re.I)

_forecast_cache: dict[tuple, dict] = {}

# Residual bias after pointing at the true resolution stations. The old
# fitted offsets (miami -9.8, nyc -8.5) were the WRONG-COORDINATES error in
# disguise; with station coords they no longer apply. Refit from
# app/weather_bias.py after a week of station-based grades if needed.
STATION_BIAS: dict[str, float] = {}


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
