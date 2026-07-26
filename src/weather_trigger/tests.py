"""Unit tests for the pure cores — no network. Run:
    python -m src.weather_trigger.tests
Covers the three things the spec calls out: local-day boundary math, 6-hour
max-group parsing, and boundary/margin lock logic (+ edge-dollars, rules).
"""
import math
from datetime import datetime, timezone

from src.weather_trigger import clob, localtime, lock, metar, quality, rules


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
    H = 3600
    M = 1_000_000                                    # local midnight epoch
    obs = [
        {"obsTime": M - H, "temp": 30.0, "rawOb": "X"},          # yesterday body
        {"obsTime": M + 2 * H, "temp": 24.0, "rawOb": "X"},      # today body 24
        # group reported 1h after midnight -> window [M-5h, M+1h] crosses midnight
        {"obsTime": M + H, "temp": 20.0, "rawOb": "Y RMK 10300"},   # 30.0, DROP
        # group reported 6h after midnight -> window [M, M+6h] fully today
        {"obsTime": M + 6 * H, "temp": 22.0, "rawOb": "Z RMK 10261"},  # 26.1, KEEP
    ]
    # yesterday's body (30) and the cross-midnight group (30) are both excluded;
    # the fully-today afternoon group (26.1) wins over today's body (24).
    assert metar.observed_max_c(obs, since_epoch=M) == 26.1
    assert metar.observed_max_c([], 0) is None


def test_6hr_group_clamp():
    H = 3600
    M = 2_000_000
    g = "RMK 10350"                                   # 6hr-max group = 35.0C
    def one(offset):                                  # a single report at M+offset
        return metar.observed_max_c(
            [{"obsTime": M + offset, "temp": 10.0, "rawOb": g}], since_epoch=M)
    assert one(6 * H) == 35.0        # window [M, M+6h] exactly today -> counted
    assert one(6 * H - 1) == 10.0    # window starts 1s before midnight -> dropped
    assert one(H) == 10.0            # early-morning report, window mostly yesterday


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


def test_lock_quality():
    # PROVEN at KDAL priced 0.88 -> in band, US station -> PREFERRED
    q = quality.classify("PROVEN", 0.88, obs_max=96, boundary=94.5,
                         unit="fahrenheit", icao="KDAL")
    assert q["tier"] == "PREFERRED" and q["sure_p"] == 0.88
    assert not q["adverse"] and not q["low_data"]
    # PROVEN priced 0.55 -> market disagrees hard -> adverse -> AVOID
    a = quality.classify("PROVEN", 0.55, icao="KSEA")
    assert a["adverse"] and a["tier"] == "AVOID"
    # DEAD at KDAL: sure side is NO, so sure_p = 1 - 0.10 = 0.90 -> PREFERRED
    dead = quality.classify("DEAD", 0.10, icao="KDAL")
    assert dead["sure_p"] == 0.90 and dead["tier"] == "PREFERRED"
    # DEAD priced 0.60 -> NO side implied 0.40 -> adverse -> AVOID
    dbad = quality.classify("DEAD", 0.60, icao="KHOU")
    assert dbad["adverse"] and dbad["tier"] == "AVOID"
    # International station (Shenzhen ZGSZ), well-priced -> thin data flags but is
    # NOT a hard avoid (intl °C stations reconcile clean); lowered to CAUTION.
    intl = quality.classify("PROVEN", 0.90, icao="ZGSZ")
    assert intl["low_data"] and intl["tier"] == "CAUTION"
    # International AND adverse-priced (the actual Shenzhen miss) -> still AVOID.
    intl_bad = quality.classify("PROVEN", 0.36, icao="ZGSZ")
    assert intl_bad["adverse"] and intl_bad["tier"] == "AVOID"

    # --- station-bias gate (measured reliability beats nationality) ---
    # A clean, well-reconciled INTERNATIONAL station can reach PREFERRED.
    clean_intl = quality.classify("PROVEN", 0.88, unit="celsius", icao="ZGSZ",
                                  station_bias_mean=-0.4, station_bias_n=5)
    assert clean_intl["reliability_basis"] == "station"
    assert not clean_intl["low_data"] and clean_intl["tier"] == "PREFERRED"
    # A well-reconciled but BIASED US station is demoted out of PREFERRED
    # (+5°F ≈ +2.8°C-equiv, over the 1.0 threshold).
    biased_us = quality.classify("PROVEN", 0.88, unit="fahrenheit", icao="KSEA",
                                 station_bias_mean=5.0, station_bias_n=5)
    assert biased_us["reliability_basis"] == "station" and biased_us["low_data"]
    assert biased_us["tier"] == "CAUTION"
    # Too few post-fix reconciliations -> fall back to the nationality proxy.
    fallback = quality.classify("PROVEN", 0.88, unit="celsius", icao="ZGSZ",
                                station_bias_mean=-0.4, station_bias_n=1)
    assert fallback["reliability_basis"] == "proxy" and fallback["low_data"]


def test_station_health_metrics():
    from app.jobs import monitor_stations as mon
    now = 1_800_000_000
    # A healthy US station: fresh obs, 6hr-max group present.
    obs = [{"obsTime": now - 300, "temp": 30, "rawOb": "KDAL ... RMK 10280 20150"},
           {"obsTime": now - 3900, "temp": 29, "rawOb": "KDAL ..."}]
    h = mon.station_metrics(obs, now)
    assert h["ok"] == 1 and h["n_obs_6h"] == 2 and h["has_6hr_max"] == 1
    assert h["staleness_min"] == 5.0
    # A degraded station: no obs at all (outage) — the failure mode to catch.
    d = mon.station_metrics([], now)
    assert d["ok"] == 0 and d["n_obs_6h"] == 0 and d["staleness_min"] is None
    # International-style: fresh obs but no 6hr-max group (legitimately absent).
    i = mon.station_metrics([{"obsTime": now - 600, "temp": 22, "rawOb": "ZGSZ 22/21"}], now)
    assert i["ok"] == 1 and i["has_6hr_max"] == 0 and i["staleness_min"] == 10.0


def test_iem_fallback_parse():
    csv_text = (
        "station,valid,metar\n"
        "DAL,2026-07-25 17:53,KDAL 251753Z 24008KT 10SM CLR 35/19 A3004 RMK AO2 SLP165 10339 T03500194\n"
        "DAL,2026-07-25 18:00,KDAL 251800Z AUTO 16007KT 10SM CLR 34/20 A2995 RMK T03400200\n"
    )
    obs = metar._parse_iem_csv(csv_text)
    assert len(obs) == 2
    assert obs[0]["rawOb"].startswith("KDAL 251753Z")
    assert obs[0]["temp"] == 35.0                 # RMK T-group 0350 -> +35.0
    assert obs[1]["temp"] == 34.0
    assert obs[1]["obsTime"] > obs[0]["obsTime"]  # chronological, real epochs
    # the 6hr-max group (10339 -> 33.9C) survives for the existing parser
    assert metar.parse_6hr_max_c(obs[0]["rawOb"]) == 33.9
    # ICAO -> IEM id mapping: strip K for CONUS, pass through non-US
    assert metar._iem_station("KDAL") == "DAL"
    assert metar._iem_station("ZGSZ") == "ZGSZ"


def test_phantom_ratio():
    from app.jobs.depth_reality import phantom_ratio
    # displayed 100 shares, all 100 traded -> real, 0% phantom
    assert phantom_ratio(100, 100) == 0.0
    # displayed 100, only 10 traded -> 90% phantom (depth pulled)
    assert phantom_ratio(100, 10) == 0.9
    # more traded than the single displayed snapshot -> clamp to 0 (real)
    assert phantom_ratio(100, 250) == 0.0
    # no displayed depth -> nothing to judge
    assert phantom_ratio(0, 0) is None
    # Near-conceded high price -> flagged, downside ratio large
    hi = quality.classify("PROVEN", 0.97, icao="KDAL")
    assert hi["near_conceded"] and hi["tier"] == "CAUTION"
    assert hi["downside_ratio"] > 30   # 0.97/0.03 ~ 32x loss-to-win if wrong


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
