"""Unit tests for the pure cores — no network. Run:
    python -m src.weather_trigger.tests
Covers the three things the spec calls out: local-day boundary math, 6-hour
max-group parsing, and boundary/margin lock logic (+ edge-dollars, rules).
"""
import math
from datetime import datetime, timezone

from src.weather_trigger import clob, localtime, lock, metar, rules


def test_lock_margin():
    inf = math.inf
    # "75°F or above" -> _bucket gives (74.5, inf); margin 1.0
    assert lock.classify(76, 74.5, inf, "fahrenheit") == "PROVEN"   # 76>=75.5
    assert lock.classify(75, 74.5, inf, "fahrenheit") == "LIVE"     # margin saves
    # "73-74°F" -> (72.5, 74.5); already 76 -> DEAD; 75 within margin -> LIVE
    assert lock.classify(76, 72.5, 74.5, "fahrenheit") == "DEAD"
    assert lock.classify(75, 72.5, 74.5, "fahrenheit") == "LIVE"
    # celsius margin 0.5
    assert lock.classify(30.5, 29.5, inf, "celsius") == "PROVEN"    # 30.5>=30.0
    assert lock.classify(29.9, 29.5, inf, "celsius") == "LIVE"
    assert lock.fair_price("PROVEN") == 1.0 and lock.fair_price("DEAD") == 0.0


def test_6hr_max_parse():
    # US METAR with 6-hourly max group 10261 -> +26.1C ; hourly T group ignored
    us = "METAR KDAL 221953Z ... RMK AO2 SLP123 10261 20206 T02610206"
    assert metar.parse_6hr_max_c(us) == 26.1
    # negative sign group 11005 -> -0.5C
    assert metar.parse_6hr_max_c("KXXX ... RMK 11005") == -0.5
    # international METAR (ZBAA) has no RMK/6hr group -> None
    assert metar.parse_6hr_max_c(
        "METAR ZBAA 221530Z VRB01MPS CAVOK 22/21 Q1004 NOSIG") is None
    assert metar.parse_6hr_max_c(None) is None


def test_observed_max_blend():
    obs = [
        {"obsTime": 1000, "temp": 20.0, "rawOb": "X"},         # before window
        {"obsTime": 2000, "temp": 24.0, "rawOb": "X"},
        {"obsTime": 3000, "temp": 23.0, "rawOb": "Y RMK 10261"},  # 6hr max 26.1
    ]
    assert metar.observed_max_c(obs, since_epoch=1500) == 26.1   # group wins
    assert metar.observed_max_c(obs, since_epoch=2500) == 26.1
    assert metar.observed_max_c([], 0) is None


def test_local_midnight():
    now = datetime(2026, 7, 22, 15, 30, tzinfo=timezone.utc)
    # Beijing +8h: local is 23:30 Jul 22 -> local midnight = Jul 22 00:00 local
    # = Jul 21 16:00 UTC
    exp = int(datetime(2026, 7, 21, 16, 0, tzinfo=timezone.utc).timestamp())
    assert localtime.local_midnight_epoch(now, 8 * 3600) == exp
    assert localtime.local_date(now, 8 * 3600).isoformat() == "2026-07-22"
    # negative offset wrap: US Pacific -7h at 03:00 UTC is still previous local day
    n2 = datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)
    assert localtime.local_date(n2, -7 * 3600).isoformat() == "2026-07-21"


def test_edge_dollars():
    book = {"bids": [{"price": "0.10", "size": "5"}],
            "asks": [{"price": "0.60", "size": "100"},
                     {"price": "0.80", "size": "50"},
                     {"price": "0.999", "size": "10"}]}
    ed, walked = clob.edge_dollars(book, fair=0.99)
    assert abs(ed - ((0.99 - 0.60) * 100 + (0.99 - 0.80) * 50)) < 1e-6
    assert len(walked) == 2                    # 0.999 level is >= fair, skipped
    bb, ba = clob.best_bid_ask(book)
    assert bb == 0.10 and ba == 0.60


def test_rules_parse():
    beijing = ("This market will resolve to the temperature range that contains "
               "the highest temperature recorded at the Beijing Capital "
               "International Airport Station in degrees Celsius on 22 Jul '26. "
               "The resolution source ... Wunderground ... "
               "https://www.wunderground.com/history/daily/cn/beijing/ZBAA. "
               "highest temperature recorded for all times on this day")
    r = rules.parse_rules(beijing)
    assert r.icao == "ZBAA" and r.unit == "celsius" and r.watchable
    assert r.station == "Beijing Capital International Airport Station"
    assert "local calendar day" in r.window
    bad = rules.parse_rules("some market with no station and no unit")
    assert not bad.watchable and "no ICAO" in bad.excluded


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"{len(tests)} tests passed")


if __name__ == "__main__":
    main()
