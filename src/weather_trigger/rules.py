"""Requirement 1: parse each market's OWN settlement rules — gates everything.

We never watch a market whose settlement station we can't identify. The ICAO
is embedded in the Wunderground URL in the description, so stations are
self-configuring: the hardcoded map in the old watcher missed Beijing, Hong
Kong, Kuala Lumpur, Taipei, Shanghai, ... (most of the live volume).
"""
import re
from dataclasses import dataclass

# ICAO is the final path segment, and the path depth varies:
#   intl: /history/daily/cn/beijing/ZBAA     US: /history/daily/us/ca/los-angeles/KLAX
_ICAO = re.compile(
    r"wunderground\.com/history/daily/(?:[A-Za-z0-9-]+/)+([A-Z]{4})(?![A-Za-z0-9])")
_STATION = re.compile(r"recorded at (?:the )?(.+? Station)", re.I)
# authoritative unit: "...in degrees Celsius on 23 Jul" — NOT the toggle
# boilerplate ("toggle between Fahrenheit and Celsius"), which names both.
_UNIT = re.compile(r"in degrees\s+(Celsius|Fahrenheit)\s+on", re.I)


@dataclass
class ParsedRules:
    icao: str | None
    station: str | None
    unit: str | None            # "celsius" | "fahrenheit"
    source: str | None          # "Wunderground" | ...
    window: str                 # human note on the measurement window
    excluded: str | None        # reason string, or None if watchable

    @property
    def watchable(self) -> bool:
        return self.excluded is None


def parse_rules(description: str | None) -> ParsedRules:
    text = description or ""
    icao_m = _ICAO.search(text)
    icao = icao_m.group(1) if icao_m else None

    unit_m = _UNIT.search(text)
    unit = unit_m.group(1).lower() if unit_m else None

    station_m = _STATION.search(text)
    station = station_m.group(1) if station_m else None
    source = "Wunderground" if "Wunderground" in text else (
        "unknown" if text else None)
    window = ("local calendar day at the station "
              if "all times on this day" in text else "unstated window ")

    reasons = []
    if not icao:
        reasons.append("no ICAO settlement station in rules")
    if not unit:
        reasons.append("temperature unit not stated")
    excluded = "; ".join(reasons) or None
    return ParsedRules(icao=icao, station=station, unit=unit, source=source,
                       window=window, excluded=excluded)
