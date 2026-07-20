"""Print today's frozen predictions. Usage: python -m app.show_today"""
from sqlalchemy import select

from app import db
from src import devig


def main():
    engine = db.init_db()
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.games.c.game_id, db.games.c.start_time, db.games.c.away_team,
                   db.games.c.home_team, db.predictions.c.p_home,
                   db.predictions.c.market_p_home, db.predictions.c.frozen_at)
            .select_from(db.predictions.join(
                db.games, db.predictions.c.game_id == db.games.c.game_id))
            .where(db.games.c.status == "scheduled")
            .order_by(db.games.c.start_time)).fetchall()

        def live_market(game_id):
            snap = conn.execute(
                select(db.odds_snapshots.c.home_odds, db.odds_snapshots.c.away_odds)
                .where(db.odds_snapshots.c.game_id == game_id,
                       db.odds_snapshots.c.book == "polymarket")
                .order_by(db.odds_snapshots.c.fetched_at.desc()).limit(1)).fetchone()
            if snap is None:
                return None
            p, _ = devig.devig_power([snap.home_odds, snap.away_odds])
            return p

        print(f"{len(rows)} frozen predictions for upcoming games:\n")
        for r in rows:
            frozen_mkt = (f"{r.market_p_home:.3f}" if r.market_p_home is not None
                          else "  —  ")
            live = live_market(r.game_id)
            live_s = f"{live:.3f}" if live is not None else "  —  "
            print(f"  {r.start_time}  {r.away_team[4:]:4} @ {r.home_team[4:]:4}  "
                  f"model {r.p_home:.3f}  mkt@freeze {frozen_mkt}  "
                  f"polymarket now {live_s}")


if __name__ == "__main__":
    main()
