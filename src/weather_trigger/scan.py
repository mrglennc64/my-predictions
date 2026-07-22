"""Requirements 3-5 wired: detect locks, snapshot depth, measure the lag.

Strictly read-only. Every poll appends rows to trigger_events (never mutates):
  LOCK      first time a bucket is mechanically decided (t_locked)
  SNAPSHOT  each later poll while the price hasn't conceded (depth decay curve)
  CONCEDE   first poll where the price crosses 0.95/0.05 (t_conceded)
Idempotent: state is derived from existing rows, so a crash/restart never
double-logs a LOCK or a CONCEDE.
"""
import json
import math
from datetime import datetime, timezone

from sqlalchemy import insert, select, text

from app import db
from src.lanes.weather import _bucket
from src.weather_trigger import clob, fetch, localtime, lock, metar, rules


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}


def _event_date(event_slug: str):
    """'highest-temperature-in-beijing-on-july-22-2026' -> date(2026, 7, 22).

    A lock is mechanical only against THAT day's observations. Without this
    check, tomorrow's buckets get judged by today's max — which manufactured
    a 'dead at 35c' flag on a Chicago market whose day hadn't started.
    """
    import re
    from datetime import date
    m = re.search(r"on-(january|february|march|april|may|june|july|august|"
                  r"september|october|november|december)-(\d{1,2})-(\d{4})",
                  event_slug or "")
    if not m:
        return None
    return date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))


def scan_once(engine, verbose=True) -> list[dict]:
    """One read-only poll. Structured so the SHARED ledger is never write-locked
    during network I/O: read prior state, do all METAR/CLOB calls with no txn
    open, then one short write. crypto-watch and the pipeline write the same
    SQLite file — holding a txn across slow HTTP would lock them out."""
    markets = fetch.fetch_markets()

    # 1. snapshot prior state up front (short read; nothing held afterward)
    prior: dict[str, set] = {}
    with engine.connect() as conn:
        for row in conn.execute(select(db.trigger_events.c.mslug,
                                       db.trigger_events.c.kind)):
            prior.setdefault(row.mslug, set()).add(row.kind)

    # 2. classify + fetch depth OUTSIDE any transaction
    obs_cache: dict[str, float | None] = {}
    to_insert, flags = [], []
    for mk in markets:
        r = rules.parse_rules(mk.description)
        if not r.watchable:
            continue
        bucket = _bucket(mk.side)
        if not bucket:
            continue
        lo, hi = bucket
        if r.icao not in obs_cache:
            obs_cache[r.icao] = _station_obs(r.icao)
        obs = obs_cache[r.icao]
        if obs is None:
            continue
        # the market's day must BE the station's current local day
        off = localtime._offset_cache.get(r.icao)
        ev_date = _event_date(mk.event_slug)
        if off is None or ev_date is None or \
                ev_date != localtime.local_date(datetime.now(timezone.utc), off):
            continue
        obs_max = metar.to_unit(obs, r.unit)
        state = lock.classify(obs_max, lo, hi, r.unit)
        if state == "LIVE":
            continue

        kinds = prior.get(mk.mslug, set())
        conceded = (mk.market_p >= 0.95 if state == "PROVEN"
                    else mk.market_p <= 0.05)
        if "LOCK" not in kinds:
            kind = "CONCEDE" if conceded else "LOCK"
        elif conceded and "CONCEDE" not in kinds:
            kind = "CONCEDE"
        elif "CONCEDE" in kinds:
            continue                          # done — nothing more to log
        else:
            kind = "SNAPSHOT"

        boundary = hi if state == "DEAD" else lo
        token = mk.yes_token if state == "PROVEN" else mk.no_token
        best_bid = best_ask = mis = edollars = None
        depth_json = None
        if token:
            try:
                book = clob.fetch_book(token)
                best_bid, best_ask = clob.best_bid_ask(book)
                edollars, walked = clob.edge_dollars(book)
                depth_json = json.dumps(walked)
                if best_ask is not None:
                    mis = round((0.99 - best_ask) * 100, 1)
            except Exception:
                pass

        to_insert.append(dict(
            mslug=mk.mslug, city=_city_of(mk), icao=r.icao, side=mk.side,
            state=state, kind=kind, boundary=boundary, unit=r.unit,
            obs_max=round(obs_max, 1), fair=lock.fair_price(state),
            market_p=mk.market_p, best_bid=best_bid, best_ask=best_ask,
            mispricing_cents=mis, edge_dollars=edollars, depth_json=depth_json,
            snapshot_at=_now_iso()))
        if kind == "LOCK":
            flags.append({"city": _city_of(mk), "side": mk.side, "state": state,
                          "market_p": mk.market_p, "obs_max": round(obs_max, 1),
                          "edge_dollars": edollars})

    # 3. one short write; wait up to 5s for a concurrent writer instead of erroring
    if to_insert:
        with engine.begin() as conn:
            conn.execute(text("PRAGMA busy_timeout=5000"))
            for v in to_insert:
                conn.execute(insert(db.trigger_events).values(**v))

    if verbose:
        for f in sorted(flags, key=lambda x: -(x["edge_dollars"] or 0)):
            print(f"  LOCK {f['state']:6} {f['city']:16} {f['side']:16} "
                  f"priced {f['market_p']:.2f}  obs {f['obs_max']}  "
                  f"${f['edge_dollars']} fillable")
    return flags


def _adaptive_poll() -> int:
    """600s normally; 300s while any watched station is in its local
    afternoon peak (12:00-20:00), when maxes are being set. Uses the DST-
    correct offsets already cached by localtime during the scan."""
    now = datetime.now(timezone.utc)
    for off in localtime._offset_cache.values():
        if 12 <= localtime.local_hour(now, off) < 20:
            return 300
    return 600


def run(minutes: int = 720, poll_s: int | None = None):
    """Read-only watch loop. Idempotent: safe to restart — trigger_events
    state prevents duplicate LOCK/CONCEDE rows."""
    import time
    engine = db.init_db()
    deadline = time.time() + minutes * 60
    print(f"[weather-trigger] read-only, {minutes} min, adaptive poll "
          f"600s/300s — logging fact-locked vs repriced lag + edge-dollars")
    while time.time() < deadline:
        try:
            scan_once(engine, verbose=True)
        except Exception as e:
            print(f"  scan error: {type(e).__name__}: {e}")
        p = poll_s or _adaptive_poll()
        print(f"[{_now_iso()[11:16]}] tick (next in {p}s)")
        time.sleep(p)


def _station_obs(icao: str) -> float | None:
    """Observed daily max in °C at the station, over its local calendar day."""
    try:
        obs = metar.fetch_metars(icao)
    except Exception:
        return None
    if not obs:
        return None
    lat, lon = obs[0].get("lat"), obs[0].get("lon")
    off = localtime.station_offset_seconds(lat, lon, icao) if lat is not None \
        else None
    if off is None:
        return None
    since = localtime.local_midnight_epoch(datetime.now(timezone.utc), off)
    return metar.observed_max_c(obs, since)


def _city_of(mk: fetch.WxMarket) -> str:
    # "Highest temperature in Beijing on July 22?" -> "Beijing"
    import re
    m = re.search(r"temperature in (.+?) on ", mk.event_title, re.I)
    return m.group(1).strip() if m else mk.event_slug[:20]
