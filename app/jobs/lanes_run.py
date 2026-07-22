"""Job: freeze + grade every experimental lane through one discipline.

Lanes with a model attach model_p; mirror lanes (no model yet) freeze with
model_p NULL — they build graded volume and market-calibration data until a
model earns its way in. Runs inside run_pipeline every 30 min.
"""
from datetime import datetime, timezone

from sqlalchemy import insert, select, update

from app import db
from src.lanes import core, weather, wnba

LANES = [
    ("wnba", "wnba", wnba.attach),
    ("weather", "weather", weather.attach),
    ("ufc", "ufc", None),          # mirror until Glicko-MMA lands
    ("soccer", "soccer", None),    # mirror until Dixon-Coles lands (August)
]


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    engine = db.init_db()
    for lane, tag, attach in LANES:
        try:
            rows = core.fetch_lane_rows(lane, tag)
        except Exception as e:
            print(f"[lanes] {lane} fetch error: {e}")
            continue
        modeled = attach(rows) if attach else 0
        frozen = 0
        with engine.begin() as conn:
            for r in rows:
                if conn.execute(select(db.lane_predictions.c.row_id)
                                .where(db.lane_predictions.c.mslug == r.mslug)
                                ).fetchone():
                    continue
                conn.execute(insert(db.lane_predictions).values(
                    lane=lane, mslug=r.mslug, title=r.title, side=r.side,
                    model_p=r.model_p, market_p=r.market_p, frozen_at=_now()))
                frozen += 1
        print(f"[lanes] {lane}: {len(rows)} live, modeled {modeled}, "
              f"frozen {frozen}")

    graded = 0
    with engine.begin() as conn:
        pending = conn.execute(
            select(db.lane_predictions)
            .where(db.lane_predictions.c.outcome.is_(None))
            .order_by(db.lane_predictions.c.row_id.asc())
            .limit(600)).fetchall()      # oldest first — those have resolved
        for row in pending:
            outcome = core.resolved_outcome(row.mslug)
            if outcome is None:
                continue
            conn.execute(update(db.lane_predictions)
                         .where(db.lane_predictions.c.row_id == row.row_id)
                         .values(
                outcome=outcome,
                model_brier=(round((row.model_p - outcome) ** 2, 5)
                             if row.model_p is not None else None),
                market_brier=round((row.market_p - outcome) ** 2, 5),
                graded_at=_now()))
            graded += 1
    print(f"[lanes] graded {graded}")


if __name__ == "__main__":
    main()
