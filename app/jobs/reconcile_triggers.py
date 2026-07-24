"""Job: EOD reconciliation — record our peak METAR read vs the bucket that
actually settled, per event. Produces the empirical METAR-vs-settlement gap
that sets the safety margin. Append-only, idempotent (unique event_slug).
Part of app.run_pipeline (every 30 min); reconciles only settled events.
"""
from sqlalchemy import insert

from app import db
from src.weather_trigger import revision


def main():
    engine = db.init_db()
    rows = revision.reconcile_pending(engine)
    if rows:
        with engine.begin() as conn:
            for v in rows:
                conn.execute(insert(db.trigger_reconciliations).values(**v))
        for v in rows:
            arrow = "HIGH" if v["delta_deg"] > 0 else "ok"
            print(f"[reconcile_triggers] {v['city']}: read {v['our_obs_max']} "
                  f"vs won<= {v['won_hi']} {v['unit'][:1].upper()} "
                  f"-> {v['delta_deg']:+} ({arrow})")
    bias = revision.station_bias()
    hot = [s for s in bias["stations"] if s["max_delta"] > 0]
    print(f"[reconcile_triggers] +{len(rows)} reconciled; "
          f"{bias['n_events']} events, {len(hot)} stations reading high")
    for s in hot[:8]:
        print(f"[reconcile_triggers]   {s['city']}: mean {s['mean_delta']:+} "
              f"max {s['max_delta']:+} {s['unit'][:1].upper()} (n={s['n']}) "
              f"-> suggest margin {s['suggested_margin']}")


if __name__ == "__main__":
    main()
