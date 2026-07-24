"""Requirement 7: per-city verdict lines + an honest global $/week estimate.

Reads only trigger_events. The verdict is deliberately plain-English and
deflationary: "real lag, toy depth" is the expected finding, and edge-dollars
(not edge-percent) is what earns the adjective.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import func, select

from app import db

STAKE = 200.0   # the paper order we actually simulate against the book


def _iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _pctile(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def _fill_profit(depth_json, stake=STAKE, fair=1.0):
    """Realistic profit-if-correct for a fixed-$ order, walking real asks.

    depth_json is the stored below-fair asks [{price,size}]. We buy cheapest
    first until `stake` dollars of cost are spent (partial-filling the last
    level), then profit = shares * fair - cost. This is what actually survives
    a $200 order — not the notional (fair-p)*size swept across the whole book.
    """
    if not depth_json:
        return 0.0
    try:
        levels = sorted(json.loads(depth_json), key=lambda l: l["price"])
    except (TypeError, ValueError):
        return 0.0
    cost = shares = 0.0
    for lvl in levels:
        p, sz = float(lvl["price"]), float(lvl["size"])
        if p >= fair:
            break
        spend = min(p * sz, stake - cost)
        if spend <= 0:
            break
        shares += spend / p
        cost += spend
        if cost >= stake:
            break
    return round(shares * fair - cost, 2)


def compute():
    with db.get_engine().connect() as conn:
        rows = conn.execute(select(db.trigger_events)
                            .order_by(db.trigger_events.c.id)).fetchall()
    # mslug is per-bucket (…-on-2026-07-23-90-91f), so grouping by mslug groups
    # by bucket. Keep the FIRST lock and ALL concede timestamps — the lag is
    # only meaningful against a concede that lands at/after the lock.
    by_slug: dict[str, dict] = {}
    for r in rows:
        d = by_slug.setdefault(r.mslug, {"city": r.city, "concedes": []})
        if r.kind == "LOCK":
            if "locked_at" not in d:
                d["locked_at"] = r.snapshot_at
                d["edge_dollars"] = r.edge_dollars
                d["depth_json"] = r.depth_json
        if r.kind == "CONCEDE":
            d["concedes"].append(r.snapshot_at)

    cities: dict[str, dict] = {}
    span_start = span_end = None
    for slug, d in by_slug.items():
        c = cities.setdefault(d["city"], {"locks": 0, "lags": [], "edges": [],
                                          "market_led": 0, "fills": []})
        if "locked_at" not in d:
            continue
        c["locks"] += 1
        if d.get("edge_dollars") is not None:
            c["edges"].append(d["edge_dollars"])
        c["fills"].append(_fill_profit(d.get("depth_json")))
        t0 = _iso(d["locked_at"])
        span_start = min(span_start or t0, t0)
        span_end = max(span_end or t0, t0)
        # Only a concede AT/AFTER the lock measures our edge window. A bucket
        # the market priced dead BEFORE the mechanical lock is the market
        # leading the instrument — zero edge, not a (negative) lag.
        post = [_iso(t) for t in d["concedes"] if _iso(t) >= t0]
        if post:
            c["lags"].append(int((min(post) - t0).total_seconds()))
        elif d["concedes"]:
            c["market_led"] += 1

    # resolution accuracy — the verdict. Read the append-only grade table.
    tg = db.trigger_grades
    with db.get_engine().connect() as conn:
        resolved = conn.execute(select(func.count()).select_from(tg)).scalar() or 0
        correct = conn.execute(select(func.count()).select_from(tg)
                               .where(tg.c.lock_correct == 1)).scalar() or 0
        by_city_res = {row[0]: (row[1], row[2]) for row in conn.execute(
            select(tg.c.city, func.count(),
                   func.sum(tg.c.lock_correct)).group_by(tg.c.city))}

    out_cities, total_edge, total_fill = [], 0.0, 0.0
    for city, c in sorted(cities.items(), key=lambda kv: -kv[1]["locks"]):
        med_lag = _pctile(c["lags"], 0.5)
        p90_lag = _pctile(c["lags"], 0.9)
        med_edge = _pctile(c["edges"], 0.5)
        med_fill = _pctile(c["fills"], 0.5)
        total_edge += sum(e for e in c["edges"] if e)
        total_fill += sum(f for f in c["fills"] if f)
        res_n, res_ok = by_city_res.get(city, (0, 0))
        depth_word = ("no fills logged" if not c["edges"]
                      else "toy depth" if (med_edge or 0) < 25
                      else "tradeable depth" if (med_edge or 0) < 200
                      else "real depth")
        lag_word = ("no concede yet" if med_lag is None
                    else f"median lag {med_lag // 60}m")
        verdict = (f"{city}: {c['locks']} locks, {lag_word}, "
                   f"median ${med_edge or 0:.0f} fillable — "
                   + ("real lag, " if med_lag and med_lag > 300 else "")
                   + depth_word)
        out_cities.append({
            "city": city, "locks": c["locks"], "market_led": c["market_led"],
            "n_conceded": len(c["lags"]),
            "median_lag_s": med_lag, "p90_lag_s": p90_lag,
            "median_edge_dollars": med_edge,
            "realistic_fill_200": med_fill,
            "resolved": res_n, "resolved_correct": (res_ok or 0),
            "verdict": verdict})

    days = ((span_end - span_start).total_seconds() / 86400
            if span_start and span_end else 0)
    n_locks = sum(c["locks"] for c in cities.values())
    # Never annualize a sub-day burst — extrapolating one scan's locks to a
    # week produces absurd numbers. Estimate weekly only with >= 1 real day.
    per_week = total_edge / days * 7 if days >= 1.0 else None
    if per_week is not None:
        verdict = (f"Across {n_locks} locks over {round(days, 1)}d, at most "
                   f"~${per_week:,.0f}/week sat fillable at lock — and only if "
                   f"you filled every share the instant it locked, which you "
                   f"can't. Treat as a ceiling, not a forecast.")
    else:
        verdict = (f"{n_locks} locks, ${total_edge:,.0f} fillable at lock over "
                   f"{round(days * 24, 1)}h so far — need >= 1 day of "
                   f"observation before any weekly estimate is meaningful.")
    # The gate: no real money until the locks prove themselves. A rate is
    # meaningless below a floor of resolved locks, so accuracy is None until then.
    GATE_MIN = 50
    accuracy = round(correct / resolved, 4) if resolved else None
    gate_open = resolved >= GATE_MIN and correct == resolved
    res_line = (f"locks resolved correctly: {correct}/{resolved}"
                + (f" ({accuracy:.0%})" if resolved else "")
                + f" — gate {'OPEN' if gate_open else 'CLOSED'}: real money only "
                f"after ≥{GATE_MIN} resolved at 100%.")
    return {
        "cities": out_cities,
        "observed_days": round(days, 3),
        "total_edge_dollars_at_lock": round(total_edge, 2),
        "total_realistic_fill_200": round(total_fill, 2),
        "est_dollars_per_week_upper_bound": (round(per_week, 2)
                                             if per_week is not None else None),
        "resolved": resolved, "resolved_correct": correct,
        "lock_accuracy": accuracy, "gate_min_resolved": GATE_MIN,
        "gate_open": gate_open, "resolution_verdict": res_line,
        "station_bias": _station_bias(),
        "global_verdict": verdict,
    }


def _station_bias():
    # Lazy import: revision -> gamma pulls the network stack; keep digest import
    # cheap for callers that only want the lag/fill view.
    from src.weather_trigger import revision
    try:
        return revision.station_bias()
    except Exception:
        return {"stations": [], "n_events": 0}


def print_digest():
    d = compute()
    print("=== Weather near-resolution trigger — daily digest ===")
    for c in d["cities"]:
        print("  " + c["verdict"])
    if not d["cities"]:
        print("  (no locks logged yet)")
    print("  " + d["resolution_verdict"])
    for s in d.get("station_bias", {}).get("stations", []):
        if s["max_delta"] > 0:
            print(f"  bias {s['city']}: max {s['max_delta']:+}{s['unit'][:1].upper()}"
                  f" over {s['n']} -> margin {s['suggested_margin']}")
    print("  " + d["global_verdict"])
