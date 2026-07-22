"""Job: assert ledger invariants mechanically. Exit 1 on any failure.

The invariants are what let the ledger claim to be leak-free — checked daily,
not asserted rhetorically.
Cron: 07:30 daily.  Usage: python -m app.jobs.healthcheck
"""
import sys
from datetime import datetime, timezone

from sqlalchemy import func, select

from app import db


def main():
    engine = db.init_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    failures = []

    with engine.connect() as conn:
        # 1. every final game's predictions are graded
        ungraded = conn.execute(
            select(func.count()).select_from(
                db.predictions.join(db.games,
                                    db.predictions.c.game_id == db.games.c.game_id)
                .outerjoin(db.grades,
                           db.grades.c.prediction_id == db.predictions.c.prediction_id))
            .where(db.games.c.status == "final",
                   db.grades.c.prediction_id.is_(None))).scalar()
        if ungraded:
            failures.append(f"{ungraded} predictions on final games lack grades")

        # 2. no prediction frozen at/after first pitch (the leak-proof invariant)
        leaked = conn.execute(
            select(func.count()).select_from(
                db.predictions.join(db.games,
                                    db.predictions.c.game_id == db.games.c.game_id))
            .where(db.predictions.c.frozen_at >= db.games.c.start_time)).scalar()
        if leaked:
            failures.append(f"LEAK: {leaked} predictions frozen at/after start_time")

        # 3. odds snapshots exist for today's slate (warn only — key may be absent)
        todays_games = conn.execute(
            select(func.count()).select_from(db.games)
            .where(db.games.c.start_time.like(f"{today}%"),
                   db.games.c.status == "scheduled")).scalar()
        todays_snaps = conn.execute(
            select(func.count(func.distinct(db.odds_snapshots.c.game_id)))
            .select_from(db.odds_snapshots)
            .where(db.odds_snapshots.c.fetched_at.like(f"{today}%"))).scalar()
        odds_note = (f"odds snapshots cover {todays_snaps}/{todays_games} of "
                     f"today's games" + ("" if todays_snaps else
                                         " (WARN: none — key unset/dead?)"))

        # 3b. weather trigger_events: no CONCEDE ever precedes its own LOCK
        #     (t_conceded < t_locked is impossible under correct operation)
        te = db.trigger_events
        ev = conn.execute(select(te.c.mslug, te.c.kind, te.c.snapshot_at)
                          .order_by(te.c.id)).fetchall()
        locked_at, bad_lag = {}, 0
        for r in ev:
            if r.kind == "LOCK":
                locked_at.setdefault(r.mslug, r.snapshot_at)
            elif r.kind == "CONCEDE" and r.mslug in locked_at \
                    and r.snapshot_at < locked_at[r.mslug]:
                bad_lag += 1
        if bad_lag:
            failures.append(f"{bad_lag} trigger_events concede before lock")

        # 3c. locks should carry book data (LOCK or a later SNAPSHOT with an
        #     ask). Transient CLOB failures happen, so this fails only when
        #     it's systematic (>20% of locks bookless) — otherwise a note.
        locks = {r.mslug for r in ev if r.kind == "LOCK"}
        with_book = {r.mslug for r in conn.execute(
            select(te.c.mslug).where(te.c.best_ask.isnot(None)))}
        bookless = len(locks - with_book)
        if locks and bookless / len(locks) > 0.20:
            failures.append(f"{bookless}/{len(locks)} locks have no book data")

        # 4. Elo ratings stable (zero-sum: mean stays near 1500)
        mean_elo = conn.execute(
            select(func.avg(db.teams.c.elo))
            .where(db.teams.c.sport == "mlb")).scalar() or 1500
        if abs(mean_elo - 1500) > 15:
            failures.append(f"Elo mean drifted to {mean_elo:.1f}")

    print(f"[healthcheck] {odds_note}; Elo mean {mean_elo:.1f}")
    if failures:
        for f in failures:
            print(f"[healthcheck] FAIL: {f}")
        sys.exit(1)
    print("[healthcheck] all invariants hold")


if __name__ == "__main__":
    main()
