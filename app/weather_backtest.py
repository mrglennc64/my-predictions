"""Weather lane backtest on RESOLVED Polymarket temperature markets.

For each settled "Highest temperature in <city> on <date>" bucket:
  model_p  — reconstructed from Open-Meteo's historical-forecast archive
             (the forecast as it stood day-of; sigma per the live model)
  market_p — Polymarket's actual price at ~15:00 UTC on the event date,
             from CLOB price history
  outcome  — the market's own settlement
Then model vs market Brier, head to head, on history neither can retouch.

Usage: python -m app.weather_backtest [days_back]
"""
import math
import sys
from datetime import date, datetime, timedelta, timezone

import requests

from src.lanes.weather import CITIES, _MONTHS, _bucket, _phi
from src.polymarket import gamma, clob

HFC = "https://historical-forecast-api.open-meteo.com/v1/forecast"
SIGMA_F = 1.8            # day-of/day-ahead forecast error, deg F


def _hist_forecast(city: str, d: date) -> float | None:
    lat, lon, unit = CITIES[city]
    try:
        r = requests.get(HFC, params={
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max", "temperature_unit": unit,
            "start_date": d.isoformat(), "end_date": d.isoformat(),
            "timezone": "auto"}, timeout=30)
        vals = r.json().get("daily", {}).get("temperature_2m_max", [])
        return float(vals[0]) if vals and vals[0] is not None else None
    except Exception:
        return None


def _market_price_at(token_id: str, ts: int) -> float | None:
    try:
        hist = clob.get_prices_history(token_id, interval="max", fidelity=60)
    except Exception:
        return None
    best, best_dt = None, 1e12
    for pt in hist:
        dt = abs(pt.get("t", 0) - ts)
        if dt < best_dt:
            best, best_dt = pt.get("p"), dt
    if best is None or best_dt > 6 * 3600:
        return None
    return float(best)


def _city_date_from_slug(slug: str):
    text = slug.replace("-", " ")
    city = next((c for c in sorted(CITIES, key=len, reverse=True)
                 if c in text), None)
    import re
    m = re.search(r"(january|february|march|april|may|june|july|august|"
                  r"september|october|november|december)\s+(\d{1,2})", text)
    if not city or not m:
        return None, None
    return city, date(2026, _MONTHS[m.group(1)], int(m.group(2)))


def main(days_back: int = 40, hour: int = 15):
    tag_id = gamma.get_tag_id("weather")
    events = []
    for offset in range(0, 600, 100):
        batch = gamma._get("/events", closed="true", tag_id=tag_id,
                           limit=100, offset=offset, order="endDate",
                           ascending="false")
        if not batch:
            break
        events.extend(batch)
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days_back)

    n = m_sq = k_sq = 0
    per_city: dict[str, list] = {}
    fc_cache: dict = {}
    for ev in events:
        city, d = _city_date_from_slug(ev.get("slug", ""))
        if not city or not d or d < cutoff or d >= datetime.now(timezone.utc).date():
            continue
        key = (city, d)
        if key not in fc_cache:
            fc_cache[key] = _hist_forecast(city, d)
        fc = fc_cache[key]
        if fc is None:
            continue
        unit = CITIES[city][2]
        sigma = SIGMA_F * (5 / 9 if unit == "celsius" else 1.0)
        ts = int(datetime(d.year, d.month, d.day, hour, 0,
                          tzinfo=timezone.utc).timestamp())
        for mk in ev.get("markets", []):
            if str(mk.get("umaResolutionStatus", "")).lower() != "resolved" \
                    and not mk.get("closed"):
                continue
            outcomes = gamma.parse_json_field(mk.get("outcomes"))
            prices = gamma.parse_json_field(mk.get("outcomePrices"))
            tokens = gamma.parse_json_field(mk.get("clobTokenIds"))
            if outcomes[:1] != ["Yes"] or len(prices) < 1 or not tokens:
                continue
            settled = float(prices[0])
            if not (settled >= 0.99 or settled <= 0.01):
                continue
            outcome = 1 if settled >= 0.99 else 0
            bucket = _bucket(str(mk.get("groupItemTitle") or "")) \
                or _bucket(str(mk.get("question") or ""))
            if not bucket:
                continue
            mkt_p = _market_price_at(tokens[0], ts)
            if mkt_p is None or not (0.01 < mkt_p < 0.99):
                continue
            lo, hi = bucket
            model_p = _phi((hi - fc) / sigma) - _phi((lo - fc) / sigma)
            model_p = min(max(model_p, 0.001), 0.999)
            n += 1
            m_sq += (model_p - outcome) ** 2
            k_sq += (mkt_p - outcome) ** 2
            per_city.setdefault(city, []).append(
                ((model_p - outcome) ** 2, (mkt_p - outcome) ** 2))

    if not n:
        print("no comparable buckets found")
        return
    print(f"RESOLVED-MARKET BACKTEST  (last {days_back} days, "
          f"prices at {hour:02d}:00 UTC event day)")
    print(f"  buckets compared: {n}")
    print(f"  model Brier:  {m_sq / n:.4f}")
    print(f"  market Brier: {k_sq / n:.4f}")
    print(f"  -> {'MODEL AHEAD' if m_sq < k_sq else 'MARKET AHEAD'} "
          f"by {abs(m_sq - k_sq) / n:.4f}")
    for city, rows in sorted(per_city.items(), key=lambda x: -len(x[1])):
        ms = sum(r[0] for r in rows) / len(rows)
        ks = sum(r[1] for r in rows) / len(rows)
        print(f"    {city:14} n={len(rows):4}  model {ms:.4f}  market {ks:.4f}"
              f"  {'MODEL' if ms < ks else 'market'}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 40,
         int(sys.argv[2]) if len(sys.argv) > 2 else 15)
