"""Requirement 4 (EOD reconciliation): measure the real METAR-vs-settlement gap.

Every wrong lock so far has been the same shape: a DEAD lock (obs read as having
exceeded a bucket) that settled YES — the true daily high landed IN a bucket we
ruled out. That is a systematic HIGH read: our peak METAR sits above the value
the market settles on. This module quantifies it.

For each settled event we locked, compare our peak observed max against the
bucket that actually won (= Wunderground's published high, to whole degrees).
delta = obs_max - won_hi. Positive means we read above the winning bucket's top —
the bias. The per-station max delta is the empirical safety margin (feeds #6):
a margin larger than the worst overshoot would have stopped every miss.

Read-only against settled markets; writes one append-only reconciliation per
event. No new data source — the winning bucket comes from the same settlement
Gamma already exposes.
"""
import math
import re
from datetime import datetime, timezone

from sqlalchemy import func, select

from app import db
from src.lanes.weather import _bucket
from src.polymarket import gamma

# event slug = highest-temperature-in-<city>-on-<month>-<day>-<year>; the bucket
# suffix (…-96-97f, …-88f-or-higher) follows. City is letters+hyphens, so the
# prefix ends cleanly at the 4-digit year.
_EVENT = re.compile(r"^(highest-temperature-in-[a-z-]+-on-[a-z]+-\d{1,2}-\d{4})")


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def event_slug_of(mslug: str) -> str | None:
    m = _EVENT.match(mslug or "")
    return m.group(1) if m else None


def published_high(event_slug: str) -> tuple[float, float, str] | None:
    """The settled winning bucket (lo, hi, label) for an event, or None.

    Winner = the one market in the event whose Yes settled (outcomePrices[0]
    ~1). Its bucket range is the published daily high to whole degrees.
    """
    try:
        events = gamma._get("/events", slug=event_slug, closed="true")
    except Exception:
        return None
    for ev in events if isinstance(events, list) else [events]:
        for mk in ev.get("markets", []):
            prices = gamma.parse_json_field(mk.get("outcomePrices"))
            if not prices:
                continue
            try:
                if float(prices[0]) < 0.99:
                    continue
            except (TypeError, ValueError):
                continue
            label = mk.get("groupItemTitle") or mk.get("question") or ""
            b = _bucket(label)
            if b:
                return b[0], b[1], label
    return None


def reconcile_pending(engine) -> list[dict]:
    """Reconcile every settled, locked, not-yet-reconciled event. Returns the
    rows written. Idempotent via the unique event_slug."""
    tg, tr = db.trigger_grades, db.trigger_reconciliations
    with engine.connect() as conn:
        done = {r[0] for r in conn.execute(select(tr.c.event_slug))}
        # peak read per event, from the locks we graded (locked_obs_max)
        graded = conn.execute(select(
            tg.c.mslug, tg.c.city, tg.c.unit, tg.c.locked_obs_max)).fetchall()

    events: dict[str, dict] = {}
    for r in graded:
        es = event_slug_of(r.mslug)
        if not es:
            continue
        e = events.setdefault(es, {"city": r.city, "unit": r.unit, "obs": []})
        if r.locked_obs_max is not None:
            e["obs"].append(r.locked_obs_max)

    written = []
    for es, e in events.items():
        if es in done or not e["obs"]:
            continue
        won = published_high(es)
        if won is None:                      # not settled yet — retry next run
            continue
        lo, hi, _label = won
        our_max = max(e["obs"])
        # hi may be inf for an "or above" winner (open top) — no overshoot to
        # measure there; record delta against lo instead so it isn't dropped.
        ref = hi if not math.isinf(hi) else lo
        written.append(dict(
            event_slug=es, city=e["city"], icao="", unit=e["unit"],
            our_obs_max=round(our_max, 1),
            won_lo=(None if math.isinf(lo) else lo),
            won_hi=(None if math.isinf(hi) else hi),
            delta_deg=round(our_max - ref, 1), reconciled_at=_now_iso()))
    return written


def station_bias() -> dict:
    """Per-city METAR-vs-settlement summary for the digest/page. suggested_margin
    is the worst overshoot rounded up — a margin above it stops the misses."""
    tr = db.trigger_reconciliations
    with db.get_engine().connect() as conn:
        rows = conn.execute(select(tr.c.city, tr.c.unit, tr.c.delta_deg)
                            .where(tr.c.delta_deg.isnot(None))).fetchall()
    by_city: dict[str, dict] = {}
    for r in rows:
        c = by_city.setdefault(r.city, {"unit": r.unit, "deltas": []})
        c["deltas"].append(r.delta_deg)
    out = []
    for city, c in sorted(by_city.items(), key=lambda kv: -max(kv[1]["deltas"])):
        ds = c["deltas"]
        mx = max(ds)
        out.append({
            "city": city, "unit": c["unit"], "n": len(ds),
            "mean_delta": round(sum(ds) / len(ds), 1), "max_delta": round(mx, 1),
            # margin must EXCEED the worst overshoot; round up + half-degree cushion
            "suggested_margin": round(math.ceil(mx + 0.5)) if mx > 0 else None})
    return {"stations": out, "n_events": len(rows)}
