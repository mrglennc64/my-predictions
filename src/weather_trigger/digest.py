"""Requirement 7: per-city verdict lines + an honest global $/week estimate.

Reads only trigger_events. The verdict is deliberately plain-English and
deflationary: "real lag, toy depth" is the expected finding, and edge-dollars
(not edge-percent) is what earns the adjective.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from app import db


def _iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _pctile(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def compute():
    with db.get_engine().connect() as conn:
        rows = conn.execute(select(db.trigger_events)
                            .order_by(db.trigger_events.c.id)).fetchall()
    by_slug: dict[str, dict] = {}
    for r in rows:
        d = by_slug.setdefault(r.mslug, {"city": r.city})
        if r.kind == "LOCK":
            d["locked_at"] = r.snapshot_at
            d["edge_dollars"] = r.edge_dollars
        if r.kind == "CONCEDE" and "conceded_at" not in d:
            d["conceded_at"] = r.snapshot_at

    cities: dict[str, dict] = {}
    span_start = span_end = None
    for slug, d in by_slug.items():
        c = cities.setdefault(d["city"], {"locks": 0, "lags": [], "edges": []})
        if "locked_at" not in d:
            continue
        c["locks"] += 1
        if d.get("edge_dollars") is not None:
            c["edges"].append(d["edge_dollars"])
        t0 = _iso(d["locked_at"])
        span_start = min(span_start or t0, t0)
        span_end = max(span_end or t0, t0)
        if "conceded_at" in d:
            c["lags"].append(int((_iso(d["conceded_at"]) - t0).total_seconds()))

    out_cities, total_edge = [], 0.0
    for city, c in sorted(cities.items(), key=lambda kv: -kv[1]["locks"]):
        med_lag = _pctile(c["lags"], 0.5)
        p90_lag = _pctile(c["lags"], 0.9)
        med_edge = _pctile(c["edges"], 0.5)
        total_edge += sum(e for e in c["edges"] if e)
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
            "city": city, "locks": c["locks"],
            "median_lag_s": med_lag, "p90_lag_s": p90_lag,
            "median_edge_dollars": med_edge, "verdict": verdict})

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
    return {
        "cities": out_cities,
        "observed_days": round(days, 3),
        "total_edge_dollars_at_lock": round(total_edge, 2),
        "est_dollars_per_week_upper_bound": (round(per_week, 2)
                                             if per_week is not None else None),
        "global_verdict": verdict,
    }


def print_digest():
    d = compute()
    print("=== Weather near-resolution trigger — daily digest ===")
    for c in d["cities"]:
        print("  " + c["verdict"])
    if not d["cities"]:
        print("  (no locks logged yet)")
    print("  " + d["global_verdict"])
