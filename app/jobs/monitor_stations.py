"""Job: monitor the health of our SETTLEMENT INSTRUMENT — the US airport
ASOS/METAR feed our locks and Wunderground settlement both depend on.

This is the concrete hedge against the NOAA/NWS budget-cut risk: the cuts hit
forecasting (models, balloons, headcount), which the lock edge doesn't touch —
but a flakier ASOS network (station outages, missing obs, dropped 6-hour-max
groups, stale reads) degrades the instrument we DO depend on. Those show up in
the data long before they show up as a loss, so we log per-station health over
time and can watch the trend.

Read-only measurement: it fetches METARs and appends health rows. It does NOT
touch lock detection, grading, the money gate, or any prediction. Self-throttled
so the every-30-min pipeline probes at most hourly (kind to the public API).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app import db
from src.weather_trigger import metar

PROBE_EVERY_MIN = 60     # cap probes to hourly even though the pipeline runs /30m
STALE_MIN = 90           # an ob older than this = the station has gone quiet
ACTIVE_DAYS = 3          # stations seen in trigger_events within this window
MAX_STATIONS = 60        # backstop so one bad day can't fan out unboundedly


def _now():
    return datetime.now(timezone.utc)


def station_metrics(obs: list[dict], now_epoch: int) -> dict:
    """Pure: health metrics from a station's recent METAR obs. No network."""
    times, has_6h = [], False
    for o in obs:
        ts = o.get("obsTime") or o.get("reportTime")
        try:
            times.append(int(ts))
        except (TypeError, ValueError):
            pass
        if metar.parse_6hr_max_c(o.get("rawOb")) is not None:
            has_6h = True
    n = len(obs)
    staleness = round((now_epoch - max(times)) / 60.0, 1) if times else None
    return {"n_obs_6h": n, "staleness_min": staleness,
            "has_6hr_max": int(has_6h), "ok": int(n > 0)}


def _active_stations(conn) -> list[tuple]:
    te = db.trigger_events
    cutoff = (_now() - timedelta(days=ACTIVE_DAYS)).isoformat()
    rows = conn.execute(
        select(te.c.icao, te.c.city)
        .where(te.c.snapshot_at >= cutoff, te.c.icao.isnot(None), te.c.icao != "")
        .distinct()).fetchall()
    seen, out = set(), []
    for r in rows:
        if r.icao not in seen:
            seen.add(r.icao)
            out.append((r.icao, r.city))
    return out[:MAX_STATIONS]


def _throttled(conn) -> bool:
    last = conn.execute(select(func.max(db.station_health.c.run_at))).scalar()
    if not last:
        return False
    try:
        age = (_now() - datetime.fromisoformat(last.replace("Z", "+00:00"))
               ).total_seconds() / 60.0
    except (ValueError, AttributeError):
        return False
    return age < PROBE_EVERY_MIN


def main():
    engine = db.init_db()
    with engine.connect() as conn:
        if _throttled(conn):
            print("[monitor_stations] skipped — probed within the last hour")
            return
        stations = _active_stations(conn)

    run_at = _now().isoformat().replace("+00:00", "Z")
    rows = []
    for icao, city in stations:
        checked_at = _now().isoformat().replace("+00:00", "Z")
        try:
            obs = metar.fetch_metars(icao, hours=6)
            m = station_metrics(obs, int(_now().timestamp()))
        except Exception as e:
            print(f"  [monitor] {icao}: {type(e).__name__}: {e}")
            m = {"n_obs_6h": 0, "staleness_min": None, "has_6hr_max": 0, "ok": 0}
        rows.append({
            "run_at": run_at, "icao": icao, "city": city,
            "is_us": int(bool(icao) and icao.strip().upper().startswith("K")),
            "checked_at": checked_at, **m})

    with engine.begin() as conn:
        if rows:
            conn.execute(db.station_health.insert(), rows)

    s = summarize(rows)
    print(f"[monitor_stations] {s['verdict']}")


def summarize(rows: list[dict]) -> dict:
    """Aggregate one probe run's rows into a health verdict."""
    n = len(rows)
    if not n:
        return {"n": 0, "verdict": "no active stations to probe."}
    down = [r for r in rows if not r["ok"]]
    stale = [r for r in rows if r["ok"] and r["staleness_min"] is not None
             and r["staleness_min"] > STALE_MIN]
    us = [r for r in rows if r["is_us"]]
    us_no_6h = [r for r in us if r["ok"] and not r["has_6hr_max"]]
    verdict = (f"{n - len(down)}/{n} stations reporting; {len(stale)} stale "
               f"(>{STALE_MIN}m); {len(us_no_6h)}/{len(us)} US stations missing "
               f"the 6hr-max group. " + ("all healthy." if not (down or stale or us_no_6h)
               else "watch for a climbing trend — that's the cuts showing up."))
    return {"n": n, "down": len(down), "stale": len(stale),
            "us": len(us), "us_missing_6hr": len(us_no_6h), "verdict": verdict}


def latest_summary() -> dict | None:
    """Read-only: summarize the most recent probe run (for the digest/API/watch).
    No network — just the last batch of rows. Returns None if never run."""
    engine = db.init_db()
    with engine.connect() as conn:
        last = conn.execute(select(func.max(db.station_health.c.run_at))).scalar()
        if not last:
            return None
        sh = db.station_health
        rows = [dict(r._mapping) for r in conn.execute(
            select(sh).where(sh.c.run_at == last))]
    out = summarize(rows)
    out["run_at"] = last
    return out


if __name__ == "__main__":
    main()
