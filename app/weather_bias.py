"""Estimate per-city station-vs-gridpoint bias from resolved markets, with an
honest time split: fit on the older window, evaluate on the recent one.

The market resolves on a specific station; Open-Meteo forecasts a gridpoint.
The offset between them is stable weather-station plumbing, not meteorology —
so it is learnable from settled buckets: the settled bucket's midpoint is the
station truth (within bucket width), and midpoint - forecast estimates bias.

Usage: python -m app.weather_bias
"""
import math
from datetime import datetime, timedelta, timezone

from app.weather_backtest import (_city_date_from_slug, _hist_forecast,
                                  _market_price_at, SIGMA_F)
from src.lanes.weather import CITIES, _bucket, _phi
from src.polymarket import gamma

SPLIT_DAYS = 12      # most recent N days = held-out evaluation


def collect(days_back: int = 40):
    tag_id = gamma.get_tag_id("weather")
    events = []
    for offset in range(0, 600, 100):
        batch = gamma._get("/events", closed="true", tag_id=tag_id, limit=100,
                           offset=offset, order="endDate", ascending="false")
        if not batch:
            break
        events.extend(batch)
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=days_back)
    fc_cache, rows = {}, []
    for ev in events:
        city, d = _city_date_from_slug(ev.get("slug", ""))
        if not city or not d or d < cutoff or d >= today:
            continue
        if (city, d) not in fc_cache:
            fc_cache[(city, d)] = _hist_forecast(city, d)
        fc = fc_cache[(city, d)]
        if fc is None:
            continue
        ts = int(datetime(d.year, d.month, d.day, 15, 0,
                          tzinfo=timezone.utc).timestamp())
        for mk in ev.get("markets", []):
            outcomes = gamma.parse_json_field(mk.get("outcomes"))
            prices = gamma.parse_json_field(mk.get("outcomePrices"))
            tokens = gamma.parse_json_field(mk.get("clobTokenIds"))
            if outcomes[:1] != ["Yes"] or not prices or not tokens:
                continue
            settled = float(prices[0])
            if not (settled >= 0.99 or settled <= 0.01):
                continue
            bucket = _bucket(str(mk.get("groupItemTitle") or "")) \
                or _bucket(str(mk.get("question") or ""))
            if not bucket:
                continue
            mkt = _market_price_at(tokens[0], ts)
            rows.append({"city": city, "date": d, "fc": fc, "bucket": bucket,
                         "outcome": 1 if settled >= 0.99 else 0, "mkt": mkt})
    return rows


def main():
    rows = collect()
    dates = sorted({r["date"] for r in rows})
    if len(dates) < 4:
        print(f"only {len(dates)} distinct days — not enough to split")
        return
    split = dates[len(dates) // 2]        # older half trains, newer half tests
    train = [r for r in rows if r["date"] < split]
    test = [r for r in rows if r["date"] >= split]
    print(f"days {dates[0]}..{dates[-1]}; train {len(train)} rows "
          f"(< {split}), test {len(test)} rows")

    # bias per city: settled-bucket midpoint minus forecast, on TRAIN days only
    bias: dict[str, float] = {}
    for city in {r["city"] for r in train}:
        deltas = []
        for r in train:
            if r["city"] != city or r["outcome"] != 1:
                continue
            lo, hi = r["bucket"]
            if math.isinf(lo) or math.isinf(hi):
                continue
            deltas.append((lo + hi) / 2 - r["fc"])
        if len(deltas) >= 4:
            bias[city] = round(sum(deltas) / len(deltas), 2)
    print("fitted station bias (train window, deg native unit):")
    for c, b in sorted(bias.items()):
        print(f"  {c:14} {b:+.2f}")

    def brier(rs, use_bias):
        m_sq = k_sq = n = 0
        for r in rs:
            if r["mkt"] is None or not (0.01 < r["mkt"] < 0.99):
                continue
            unit = CITIES[r["city"]][2]
            sigma = SIGMA_F * (5 / 9 if unit == "celsius" else 1.0)
            fc = r["fc"] + (bias.get(r["city"], 0.0) if use_bias else 0.0)
            lo, hi = r["bucket"]
            p = _phi((hi - fc) / sigma) - _phi((lo - fc) / sigma)
            p = min(max(p, 0.001), 0.999)
            n += 1
            m_sq += (p - r["outcome"]) ** 2
            k_sq += (r["mkt"] - r["outcome"]) ** 2
        return n, m_sq / n if n else None, k_sq / n if n else None

    n, raw, mkt = brier(test, use_bias=False)
    _, corr, _ = brier(test, use_bias=True)
    print(f"\nHELD-OUT last {SPLIT_DAYS} days (n={n} buckets):")
    print(f"  model raw:            {raw:.4f}")
    print(f"  model bias-corrected: {corr:.4f}")
    print(f"  market:               {mkt:.4f}")
    print(f"  -> corrected model {'BEATS' if corr < mkt else 'trails'} market "
          f"by {abs(corr - mkt):.4f}")


if __name__ == "__main__":
    main()
