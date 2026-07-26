"""Requirement 2: observations from the settlement station's own METARs.

Global source: aviationweather.gov (free, no key) — verified to serve ZBAA,
which the US-only NWS API (api.weather.gov) does NOT. The daily max is the
max of body temperatures since local midnight, RAISED by the 6-hourly max
group (1sTTT in RMK) when present — US stations report it; most international
stations (e.g. ZBAA: "METAR ZBAA 221530Z ... 22/21 Q1004 NOSIG") do not, so
body-temp max is the only signal there. That's why the running max, not a
single reading, is what tracks Wunderground's daily high.
"""
import csv
import io
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

METAR_URL = "https://aviationweather.gov/api/data/metar"
# Independent fallback: Iowa Environmental Mesonet (Iowa State) mirrors the same
# ASOS raw METARs — including the RMK 6hr-max group (verified) — on separate
# infrastructure, so a NOAA/aviationweather API outage doesn't blind the watcher.
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"

# 6-hourly maximum temperature group in RMK: 1 s TTT  (s: 0=+,1=-; TTT tenths °C)
_MAX6 = re.compile(r"(?<!\d)1([01])(\d{3})(?!\d)")

# The 1sTTT group summarises the 6 hours BEFORE the report. Counting it whenever
# the report itself lands after local midnight imports yesterday-evening heat on
# US stations (UTC−5/6): the 06Z group covers 18:00–24:00 local the day before.
# Only trust a group whose whole window is on/after local midnight. Measured on
# 2026-07-24 this was the entire high-read bias — Houston +9.4°F, Dallas +6.5°F —
# while international stations (no 1sTTT group) already reconciled clean.
_MAX6_WINDOW_S = 6 * 3600


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
    """Running max °C over the local day: every body temp at/after since_epoch,
    plus each 6hr-max group whose full 6-hour window is also at/after since_epoch
    (so it can't carry heat from before local midnight)."""
    temps = []
    for o in obs:
        ts = o.get("obsTime") or o.get("reportTime")
        try:
            when = int(ts)
        except (TypeError, ValueError):
            continue
        if when >= since_epoch and o.get("temp") is not None:
            temps.append(float(o["temp"]))
        g = parse_6hr_max_c(o.get("rawOb"))
        if g is not None and when - _MAX6_WINDOW_S >= since_epoch:
            temps.append(g)
    return max(temps) if temps else None


def _fetch_awc(icao: str, hours: int = 30) -> list[dict]:
    """Primary source: aviationweather.gov JSON (global, incl. non-US stations)."""
    r = requests.get(METAR_URL, params={"ids": icao, "format": "json",
                                        "hours": hours}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def _iem_station(icao: str) -> str:
    """AWC keys on ICAO (KDAL); IEM's ASOS network keys on the local id (DAL for
    CONUS K-stations). Non-K ICAOs pass through (IEM's US coverage is the point —
    international fallback is best-effort)."""
    icao = (icao or "").strip().upper()
    return icao[1:] if len(icao) == 4 and icao.startswith("K") else icao


def _temp_from_metar(raw: str | None) -> float | None:
    """°C from a raw METAR: prefer the precise RMK T-group, else the body TT/DD."""
    if raw and "RMK" in raw:
        m = re.search(r"\bT([01]\d{3})[01]\d{3}\b", raw.split("RMK", 1)[1])
        if m:
            v = int(m.group(1)[1:]) / 10.0
            return -v if m.group(1)[0] == "1" else v
    m = re.search(r"\b(M?\d{2})/(M?\d{2})\b", raw or "")
    if m:
        t = m.group(1)
        return float(-int(t[1:]) if t.startswith("M") else int(t))
    return None


def _parse_iem_csv(text: str) -> list[dict]:
    """Pure: IEM 'station,valid,metar' CSV -> AWC-shaped obs dicts
    ({obsTime epoch, temp °C, rawOb}). Temp is decoded from the raw METAR so we
    don't depend on IEM's optional numeric columns."""
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        raw = (r.get("metar") or "").strip()
        valid = r.get("valid")
        if not raw or not valid:
            continue
        try:
            ep = int(datetime.strptime(valid, "%Y-%m-%d %H:%M")
                     .replace(tzinfo=timezone.utc).timestamp())
        except (ValueError, TypeError):
            continue
        out.append({"obsTime": ep, "temp": _temp_from_metar(raw), "rawOb": raw})
    return out


def fetch_metars_iem(icao: str, hours: int = 30) -> list[dict]:
    """Fallback fetch from IEM, normalized to the AWC obs shape."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    r = requests.get(IEM_URL, params={
        "station": _iem_station(icao), "data": "metar",
        "sts": start.strftime("%Y-%m-%dT%H:%MZ"),
        "ets": end.strftime("%Y-%m-%dT%H:%MZ"),
        "tz": "UTC", "format": "onlycomma", "latlon": "no",
        "missing": "M", "trace": "T"}, timeout=40)
    r.raise_for_status()
    return _parse_iem_csv(r.text)


def fetch_metars(icao: str, hours: int = 30) -> list[dict]:
    """Observed METARs, resilient to a primary-source outage. Tries
    aviationweather.gov first (unchanged behavior when it works); only on error
    or an empty result does it fall back to IEM. Same obs shape either way, so
    lock detection is agnostic to which source answered."""
    try:
        data = _fetch_awc(icao, hours)
        if data:
            return data
        print(f"[metar] aviationweather empty for {icao}; trying IEM", file=sys.stderr)
    except Exception as e:
        print(f"[metar] aviationweather failed for {icao}: {type(e).__name__}: {e}"
              f"; trying IEM", file=sys.stderr)
    try:
        return fetch_metars_iem(icao, hours)
    except Exception as e:
        print(f"[metar] IEM fallback failed for {icao}: {type(e).__name__}: {e}",
              file=sys.stderr)
        return []


def to_unit(c: float, unit: str) -> float:
    return c * 9 / 5 + 32 if unit == "fahrenheit" else c
