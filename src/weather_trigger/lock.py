"""Requirement 3: the lock detector — mechanical certainty ONLY, no forecast.

A daily max only rises, so exactly two facts are irreversible once observed:
  PROVEN  an "X or above" bucket whose floor the station already cleared
  DEAD    a bucket whose ceiling the station already exceeded
V1 flags ONLY these already-exceeded facts. "Can't reach X by midnight" is a
forecast, never counted here (logged as LIKELY_DEAD elsewhere for later study).
A safety margin absorbs METAR-vs-Wunderground rounding.
"""
import math

MARGIN = {"fahrenheit": 1.0, "celsius": 0.5}   # default safety margins


def classify(obs_max: float, lo: float, hi: float, unit: str,
             margin: float | None = None) -> str:
    """Return 'PROVEN', 'DEAD', or 'LIVE' for a bucket [lo, hi] given obs_max.

    lo/hi are the bucket bounds in `unit`; hi may be math.inf ("or above"),
    lo may be -math.inf ("or below"). obs_max is the running observed station
    max in the same unit.
    """
    m = MARGIN.get(unit, 1.0) if margin is None else margin
    if not math.isinf(hi) and obs_max >= hi + m:
        return "DEAD"                     # already hotter than this bucket's top
    if math.isinf(hi) and obs_max >= lo + m:
        return "PROVEN"                   # already reached an "or above" floor
    return "LIVE"


def fair_price(state: str) -> float:
    return 1.0 if state == "PROVEN" else 0.0    # true probability, forever
