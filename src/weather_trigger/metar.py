"""Requirement 2: observations from the settlement station's own METARs.

Global source: aviationweather.gov (free, no key) — verified to serve ZBAA,
which the US-only NWS API (api.weather.gov) does NOT. The daily max is the
max of body temperatures since local midnight, RAISED by the 6-hourly max
group (1sTTT in RMK) when present — US stations report it; most international
stations (e.g. ZBAA: "METAR ZBAA 221530Z ... 22/21 Q1004 NOSIG") do not, so
body-temp max is the only signal there. That's why the running max, not a
single reading, is what tracks Wunderground's daily high.
"""
import re

import requests

METAR_URL = "https://aviationweather.gov/api/data/metar"

# 6-hourly maximum temperature group in RMK: 1 s TTT  (s: 0=+,1=-; TTT tenths °C)
_MAX6 = re.compile(r"(?<!\d)1([01])(\d{3})(?!\d)")


def parse_6hr_max_c(raw_ob: str | None) -> float | None:
    """Extract the 6-hourly max temp (°C) from a METAR's RMK section, if any."""
    if not raw_ob or "RMK" not in raw_ob:
        return None
    rmk = raw_ob.split("RMK", 1)[1]
    m = _MAX6.search(rmk)
    if not m:
        return None
    sign = -1 if m.group(1) == "1" else 1
    return sign * int(m.group(2)) / 10.0


def observed_max_c(obs: list[dict], since_epoch: int) -> float | None:
    """Running max °C at/after since_epoch, blending body temp + 6hr-max group."""
    temps = []
    for o in obs:
        ts = o.get("obsTime") or o.get("reportTime")
        try:
            when = int(ts)
        except (TypeError, ValueError):
            continue
        if when < since_epoch:
            continue
        if o.get("temp") is not None:
            temps.append(float(o["temp"]))
        g = parse_6hr_max_c(o.get("rawOb"))
        if g is not None:
            temps.append(g)
    return max(temps) if temps else None


def fetch_metars(icao: str, hours: int = 30) -> list[dict]:
    r = requests.get(METAR_URL, params={"ids": icao, "format": "json",
                                        "hours": hours}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def to_unit(c: float, unit: str) -> float:
    return c * 9 / 5 + 32 if unit == "fahrenheit" else c
