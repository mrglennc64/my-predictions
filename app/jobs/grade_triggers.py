"""Job: grade each locked weather bucket once its market settles.

The one metric that turns "$X fillable" from a rumor into a verdict: did the
side we LOCKed actually win? PROVEN = we bet Yes (obs proved it), correct iff
the market settles Yes; DEAD = we bet No, correct iff it settles No.

Append-only, idempotent: one trigger_grades row per bucket (mslug), inserted
only once the market is closed/resolved. Unsettled buckets are retried on the
next pipeline run. Reads the same resolved_outcome() every other lane grades on.
Cron: part of app.run_pipeline (every 30 min).
"""
from datetime import datetime, timezone

from sqlalchemy import func, insert, select

from app import db
from src.lanes import core


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main():
    engine = db.init_db()
    te, tg = db.trigger_events, db.trigger_grades

    with engine.connect() as conn:
        already = {r[0] for r in conn.execute(select(tg.c.mslug))}
        # first LOCK row per bucket = the lock of record (state, obs, time)
        locks = conn.execute(
            select(te.c.mslug, te.c.city, te.c.side, te.c.state,
                   te.c.boundary, te.c.unit, te.c.obs_max, te.c.snapshot_at,
                   func.min(te.c.id))
            .where(te.c.kind == "LOCK")
            .group_by(te.c.mslug)).fetchall()

    todo = [r for r in locks if r.mslug not in already]
    print(f"[grade_triggers] {len(todo)} ungraded locks, "
          f"{len(already)} already graded")

    graded, pending, sanity = 0, 0, 0
    rows = []
    for r in todo:
        outcome = core.resolved_outcome(r.mslug)   # 1 | 0 | None(unsettled)
        if outcome is None:
            pending += 1
            continue
        correct = int((r.state == "PROVEN" and outcome == 1)
                      or (r.state == "DEAD" and outcome == 0))
        rows.append(dict(
            mslug=r.mslug, city=r.city, side=r.side, state=r.state,
            boundary=r.boundary, unit=r.unit, locked_obs_max=r.obs_max,
            locked_at=r.snapshot_at, outcome=outcome, lock_correct=correct,
            graded_at=_now_iso()))
        graded += 1
        # First few grades: print the raw orientation so a flipped index-0
        # ("Yes" vs "No" ordering) can't hide behind a plausible number.
        if sanity < 5:
            print(f"[grade_triggers] {r.city} {r.side} state={r.state} "
                  f"outcome={outcome} -> {'CORRECT' if correct else 'WRONG'}")
            sanity += 1

    if rows:
        with engine.begin() as conn:
            for v in rows:
                conn.execute(insert(tg).values(**v))

    if graded:
        with engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(tg)).scalar()
            ok = conn.execute(select(func.count()).select_from(tg)
                              .where(tg.c.lock_correct == 1)).scalar()
        print(f"[grade_triggers] +{graded} graded ({pending} still unsettled); "
              f"lock accuracy {ok}/{total}")
    else:
        print(f"[grade_triggers] nothing settled yet ({pending} unsettled)")


if __name__ == "__main__":
    main()
