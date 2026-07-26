"""Job: is the displayed liquidity REAL? — the phantom-liquidity (spoof-and-
withdraw) check.

We can't observe sub-second order cancels at a 5-10 min poll cadence, so we
measure the OUTCOME instead: for each lock where we recorded below-fair asks,
compare that DISPLAYED depth against the volume that actually TRADED below fair
before the bucket conceded. Displayed depth that never traded — while the price
moved up to concede — is depth that was pulled, not consumed.

  phantom_ratio = 1 - traded_shares / displayed_shares   (clamped to [0,1])

It's a LOWER bound on phantom-ness: depth can also appear AFTER our single lock
snapshot and trade, inflating `traded` — so if anything this understates spoofing.
Read-only, append-only, idempotent (one row per bucket). No trading, no gate.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import func, select

from app import db
from src.weather_trigger import clob
from src.weather_trigger.revision import event_slug_of

FAIR = 0.99
WINDOW_FALLBACK_S = 6 * 3600     # if a bucket never logged a concede
ACTIVE_DAYS = 5
MAX_LOCKS = 30                   # bound gamma + trades calls per run


def phantom_ratio(displayed_shares, traded_shares):
    """Pure: fraction of displayed below-fair depth that did NOT trade."""
    if not displayed_shares or displayed_shares <= 0:
        return None
    return round(1 - min(1.0, traded_shares / displayed_shares), 3)


def _epoch(iso):
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError, AttributeError):
        return None


def _displayed_shares(depth_json):
    try:
        return sum(float(l["size"]) for l in json.loads(depth_json or "[]"))
    except (ValueError, TypeError, KeyError):
        return 0.0


def _resolve_condition(event_slug, mslug):
    """(conditionId) for the bucket market, via gamma — best effort."""
    from src.polymarket import gamma
    for closed in (None, "true"):
        try:
            kw = {"slug": event_slug}
            if closed:
                kw["closed"] = closed
            evs = gamma._get("/events", **kw)
        except Exception:
            continue
        for ev in (evs if isinstance(evs, list) else [evs] if evs else []):
            for mk in ev.get("markets", []):
                if mk.get("slug") == mslug and mk.get("conditionId"):
                    return mk["conditionId"]
    return None


def _traded_below_fair(cond, certain_outcome, lock_ts, concede_ts):
    trades = clob.fetch_trades(cond, since_ts=lock_ts)
    total = 0.0
    for t in trades:
        try:
            ts = int(t.get("timestamp", 0))
            price = float(t.get("price"))
            size = float(t.get("size"))
        except (TypeError, ValueError):
            continue
        if (t.get("outcome") == certain_outcome and t.get("side") == "BUY"
                and price < FAIR and lock_ts <= ts <= concede_ts):
            total += size
    return total


def main():
    engine = db.init_db()
    te = db.trigger_events
    cutoff = datetime.now(timezone.utc).timestamp() - ACTIVE_DAYS * 86400
    cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    with engine.connect() as conn:
        done = {r[0] for r in conn.execute(select(db.depth_reality.c.mslug))}
        # first LOCK per bucket, recent, that actually showed below-fair depth
        locks, concedes = {}, {}
        for r in conn.execute(
                select(te.c.mslug, te.c.city, te.c.state, te.c.depth_json,
                       te.c.snapshot_at, te.c.kind)
                .where(te.c.snapshot_at >= cutoff_iso).order_by(te.c.id)):
            if r.kind == "LOCK" and r.mslug not in locks:
                locks[r.mslug] = r
            elif r.kind == "CONCEDE" and r.mslug not in concedes:
                concedes[r.mslug] = r.snapshot_at

    written = 0
    for mslug, r in locks.items():
        if mslug in done or written >= MAX_LOCKS:
            continue
        displayed = _displayed_shares(r.depth_json)
        es = event_slug_of(mslug)
        lock_ts = _epoch(r.snapshot_at)
        if displayed <= 0 or not es or lock_ts is None:
            continue
        concede_ts = _epoch(concedes.get(mslug)) or (lock_ts + WINDOW_FALLBACK_S)
        cond = _resolve_condition(es, mslug)
        if not cond:
            continue
        certain = "Yes" if r.state == "PROVEN" else "No"
        try:
            traded = _traded_below_fair(cond, certain, lock_ts, concede_ts)
        except Exception as e:
            print(f"  [depth_reality] {mslug[:40]}: {type(e).__name__}: {e}")
            continue
        row = dict(mslug=mslug, city=r.city, event_slug=es, state=r.state,
                   displayed_shares=round(displayed, 1),
                   traded_shares=round(traded, 1),
                   phantom_ratio=phantom_ratio(displayed, traded),
                   lock_at=r.snapshot_at, concede_at=concedes.get(mslug),
                   computed_at=datetime.now(timezone.utc).strftime(
                       "%Y-%m-%dT%H:%M:%SZ"))
        with engine.begin() as conn:
            conn.execute(db.depth_reality.insert().values(**row))
        written += 1

    s = latest_summary()
    print(f"[depth_reality] wrote {written}; " + (s["verdict"] if s else "no data"))


def latest_summary():
    """Read-only phantom-liquidity verdict over all reconstructed locks."""
    engine = db.init_db()
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(
            select(db.depth_reality).where(
                db.depth_reality.c.phantom_ratio.isnot(None)))]
    if not rows:
        return None
    ratios = [r["phantom_ratio"] for r in rows]
    mean = round(sum(ratios) / len(ratios), 3)
    mostly_phantom = sum(1 for x in ratios if x >= 0.8)
    verdict = (f"phantom check: {len(rows)} locks with displayed depth; mean "
               f"phantom_ratio {mean:.0%} (share of displayed below-fair depth "
               f"that never traded); {mostly_phantom} were ≥80% phantom. "
               + ("displayed depth is largely REAL." if mean < 0.34 else
                  "displayed depth is largely ILLUSORY — don't trust the fill column."))
    return {"n": len(rows), "mean_phantom_ratio": mean,
            "mostly_phantom": mostly_phantom, "verdict": verdict}


if __name__ == "__main__":
    main()
