"""DEPRECATED v0 — superseded by src/weather_trigger/ (rules-parsed stations,
book-walking edge-dollars, tested cores). Not scheduled anywhere; kept only so
the historical weather_watch table rows stay interpretable.

Weather near-resolution watcher — mechanical facts vs lagging prices.

A daily max can only rise. Two irreversible states per bucket, one-sided by
construction:
  DEAD    — station already exceeded the bucket's top: true p = 0, forever
  PROVEN  — station already reached the floor of an "X or above" bucket:
            true p = 1, forever
Everything else ("the peak has probably passed") is still a forecast and is
deliberately NOT flagged.

Observations come from METAR (aviationweather.gov, free, no key) for the
exact airport stations the markets resolve on — never from a model. A safety
margin absorbs METAR-vs-Wunderground rounding.

READ-ONLY: this logs, per bucket, the moment the fact locked vs the moment
the market repriced past 0.95/0.05. That lag IS the edge; measure it on
paper before any dollar. Run: python scan.py weather-watch [minutes]
"""
import math
import time
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import insert, select, update

from app import db
from src.lanes import core
from src.lanes.weather import CITIES, _bucket, _city_and_date

STATION_ICAO = {
    "nyc": "KLGA", "new york": "KLGA", "los angeles": "KLAX",
    "las vegas": "KLAS", "chicago": "KORD", "miami": "KMIA",
    "philadelphia": "KPHL", "atlanta": "KATL", "dallas": "KDAL",
    "houston": "KHOU", "seattle": "KSEA", "san francisco": "KSFO",
    "austin": "KAUS", "denver": "KBKF", "seoul": "RKSI", "london": "EGLC",
    "paris": "LFPB", "tokyo": "RJTT", "toronto": "CYYZ", "munich": "EDDM",
    "madrid": "LEMD", "amsterdam": "EHAM", "milan": "LIMC", "warsaw": "EPWA",
    "helsinki": "EFHK", "singapore": "WSSS", "taipei": "RCSS",
    "mexico city": "MMMX",
}
METAR = "https://aviationweather.gov/api/data/metar"
POLL_S = 600
MARGIN_F, MARGIN_C = 1.0, 0.6


def _local_offset_h(city: str) -> int:
    return round(CITIES[city][1] / 15)


def obs_max_today(city: str) -> float | None:
    """Max station temperature since local midnight, in the market's unit."""
    icao = STATION_ICAO.get(city)
    if not icao:
        return None
    try:
        r = requests.get(METAR, params={"ids": icao, "format": "json",
                                        "hours": 30}, timeout=30)
        obs = r.json()
    except Exception:
        return None
    off = _local_offset_h(city)
    local_now = datetime.now(timezone.utc) + timedelta(hours=off)
    midnight_utc = (local_now.replace(hour=0, minute=0, second=0,
                                      microsecond=0)
                    - timedelta(hours=off))
    temps = []
    for o in obs if isinstance(obs, list) else []:
        t = o.get("temp")
        ts = o.get("obsTime") or o.get("reportTime")
        if t is None or ts is None:
            continue
        try:
            when = (datetime.fromtimestamp(int(ts), tz=timezone.utc)
                    if isinstance(ts, (int, float)) or str(ts).isdigit()
                    else datetime.fromisoformat(
                        str(ts).replace("Z", "+00:00")))
        except (ValueError, OSError):
            continue
        if when >= midnight_utc:
            temps.append(float(t))
    if not temps:
        return None
    mx = max(temps)                       # METAR reports Celsius
    return mx * 9 / 5 + 32 if CITIES[city][2] == "fahrenheit" else mx


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scan_once(engine) -> None:
    rows = core.fetch_lane_rows("weather", "weather")
    obs_cache: dict[str, float | None] = {}
    flags = []
    with engine.begin() as conn:
        for r in rows:
            city, d = _city_and_date(r.event_slug, r.event_title)
            if not city or not d or city not in STATION_ICAO:
                continue
            off = _local_offset_h(city)
            local_today = (datetime.now(timezone.utc)
                           + timedelta(hours=off)).date()
            if d != local_today:
                continue
            if city not in obs_cache:
                obs_cache[city] = obs_max_today(city)
            mx = obs_cache[city]
            if mx is None:
                continue
            bucket = _bucket(r.side) or _bucket(r.title)
            if not bucket:
                continue
            lo, hi = bucket
            margin = MARGIN_F if CITIES[city][2] == "fahrenheit" else MARGIN_C
            state = boundary = None
            if not math.isinf(hi) and mx >= hi + margin:
                state, boundary = "DEAD", hi
            elif math.isinf(hi) and mx >= lo + margin:
                state, boundary = "PROVEN", lo
            if state is None:
                continue

            existing = conn.execute(
                select(db.weather_watch.c.id, db.weather_watch.c.priced_at,
                       db.weather_watch.c.detected_at)
                .where(db.weather_watch.c.mslug == r.mslug)).fetchone()
            repriced = (r.market_p <= 0.05 if state == "DEAD"
                        else r.market_p >= 0.95)
            if existing is None:
                conn.execute(insert(db.weather_watch).values(
                    mslug=r.mslug, city=city, state=state, obs_max=round(mx, 1),
                    boundary=boundary, market_p_detect=r.market_p,
                    detected_at=_now_iso(),
                    priced_at=_now_iso() if repriced else None,
                    lag_s=0 if repriced else None))
                if not repriced:
                    upside = (r.market_p - 0.0 if state == "DEAD"
                              else 1.0 - r.market_p)
                    flags.append((upside, state, city, r.side, r.market_p, mx))
            elif existing.priced_at is None and repriced:
                det = datetime.fromisoformat(
                    existing.detected_at.replace("Z", "+00:00"))
                lag = int((datetime.now(timezone.utc) - det).total_seconds())
                conn.execute(update(db.weather_watch)
                             .where(db.weather_watch.c.id == existing.id)
                             .values(priced_at=_now_iso(), lag_s=lag))
                print(f"  repriced {city} {r.side!r} after {lag // 60}m")

    flags.sort(reverse=True)
    for upside, state, city, side, p, mx in flags:
        print(f"  {state:6} {city:14} {side:16} priced {p:.2f}, obs max "
              f"{mx:.1f} -> mispriced by {upside:.2f}")


def run(minutes: int = 720):
    engine = db.init_db()
    deadline = time.time() + minutes * 60
    print(f"[weather-watch] read-only, {minutes} min, poll {POLL_S}s — "
          f"logging fact-locked vs repriced lag")
    while time.time() < deadline:
        try:
            scan_once(engine)
        except Exception as e:
            print(f"  scan error: {type(e).__name__}: {e}")
        with engine.connect() as conn:
            done = conn.execute(select(db.weather_watch)
                                .where(db.weather_watch.c.lag_s.isnot(None))
                                ).fetchall()
        if done:
            lags = sorted(x.lag_s for x in done if x.lag_s is not None)
            med = lags[len(lags) // 2]
            print(f"[{_now_iso()[11:16]}] lag samples {len(lags)}, "
                  f"median {med // 60}m")
        time.sleep(POLL_S)
