"""Job: freeze predictions for upcoming scheduled games. APPEND-ONLY.

First freeze wins: if a prediction already exists for (game, model), this job
inserts nothing. Rows are never updated or deleted anywhere in the codebase.
Cron: 09:00 daily + T-60min per game.  Usage: python -m app.jobs.predict_and_freeze
"""
import json
from datetime import datetime, timezone

from sqlalchemy import insert, select

from app import db, elo
from src import devig

MODEL_ID = "elo_mlb_v1.0"


def _market_p_home(conn, game_id: str) -> float | None:
    """Latest market probability: Polymarket first (free, always on), then
    sportsbook consensus if an odds key is configured."""
    for book in ("polymarket", "consensus"):
        row = conn.execute(
            select(db.odds_snapshots.c.home_odds, db.odds_snapshots.c.away_odds)
            .where(db.odds_snapshots.c.game_id == game_id,
                   db.odds_snapshots.c.book == book,
                   db.odds_snapshots.c.market == "h2h")
            .order_by(db.odds_snapshots.c.fetched_at.desc()).limit(1)).fetchone()
        if row is not None:
            p_home, _ = devig.devig_power([row.home_odds, row.away_odds])
            return round(p_home, 4)
    return None


def main():
    engine = db.init_db()
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    with engine.begin() as conn:
        model = conn.execute(select(db.model_versions.c.params)
                             .where(db.model_versions.c.model_id == MODEL_ID)
                             ).fetchone()
        if model is None:
            raise SystemExit("model_versions row missing — run app.backfill first")
        params = json.loads(model.params)
        ha = params["home_adv"]

        elos = {r.team_id: r.elo for r in
                conn.execute(select(db.teams.c.team_id, db.teams.c.elo))}

        frozen = skipped = 0
        upcoming = conn.execute(
            select(db.games.c.game_id, db.games.c.home_team, db.games.c.away_team,
                   db.games.c.start_time)
            .where(db.games.c.status == "scheduled")).fetchall()
        for g in upcoming:
            if g.start_time <= now_iso:
                continue  # never freeze after first pitch
            exists = conn.execute(
                select(db.predictions.c.prediction_id)
                .where(db.predictions.c.game_id == g.game_id,
                       db.predictions.c.model_id == MODEL_ID)).fetchone()
            if exists:
                skipped += 1
                continue
            rh, ra = elos.get(g.home_team), elos.get(g.away_team)
            if rh is None or ra is None:
                continue
            market_p = _market_p_home(conn, g.game_id)
            start = datetime.fromisoformat(g.start_time.replace("Z", "+00:00"))
            mins_out = (start - now).total_seconds() / 60
            # freeze only once a market benchmark exists, or as a last chance
            # at T-90min — freezing days early leaves market_p NULL forever
            # in an append-only table
            if market_p is None and mins_out > 90:
                continue
            conn.execute(insert(db.predictions).values(
                game_id=g.game_id, model_id=MODEL_ID,
                p_home=round(elo.expected_home(rh, ra, ha), 4),
                market_p_home=market_p,
                frozen_at=now_iso))
            frozen += 1
    print(f"[predict_and_freeze] froze {frozen}, already-frozen {skipped} "
          f"at {now_iso}")


if __name__ == "__main__":
    main()
