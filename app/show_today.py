"""Print today's frozen predictions. Usage: python -m app.show_today"""
from sqlalchemy import select

from app import db


def main():
    engine = db.init_db()
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.games.c.start_time, db.games.c.away_team, db.games.c.home_team,
                   db.predictions.c.p_home, db.predictions.c.market_p_home,
                   db.predictions.c.frozen_at)
            .select_from(db.predictions.join(
                db.games, db.predictions.c.game_id == db.games.c.game_id))
            .where(db.games.c.status == "scheduled")
            .order_by(db.games.c.start_time)).fetchall()
    print(f"{len(rows)} frozen predictions for upcoming games:\n")
    for r in rows:
        market = f"{r.market_p_home:.3f}" if r.market_p_home is not None else "  —  "
        print(f"  {r.start_time}  {r.away_team[4:]:4} @ {r.home_team[4:]:4}  "
              f"P(home) model {r.p_home:.3f}  market {market}  "
              f"frozen {r.frozen_at}")


if __name__ == "__main__":
    main()
