"""Job: grade yesterday's finals, then — and only then — update Elo ratings.

Ordering matters: ratings update after grading guarantees no prediction was
ever informed by the result it predicted. The status transition
scheduled -> final is the idempotency marker; a game is graded exactly once.
Cron: 06:30 daily.  Usage: python -m app.jobs.grade
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, select, update

from app import db, elo, mlb_api

MODEL_ID = "elo_mlb_v1.0"


def main():
    engine = db.init_db()
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")

    schedule = mlb_api.get_schedule(start, end)
    api_by_id = {f"mlb_{g['game_pk']}": g for g in schedule}

    with engine.begin() as conn:
        params = json.loads(conn.execute(
            select(db.model_versions.c.params)
            .where(db.model_versions.c.model_id == MODEL_ID)).fetchone().params)
        k, ha = params["k"], params["home_adv"]

        pending = conn.execute(
            select(db.games.c.game_id, db.games.c.home_team, db.games.c.away_team)
            .where(db.games.c.status == "scheduled")).fetchall()

        graded = finalized = 0
        for g in pending:
            api = api_by_id.get(g.game_id)
            if api is None:
                continue
            if api["status"] != "Final":
                if "Postponed" in api.get("detailed_status", ""):
                    conn.execute(update(db.games)
                                 .where(db.games.c.game_id == g.game_id)
                                 .values(status="postponed"))
                continue
            hs, as_ = api["home_score"], api["away_score"]
            if hs is None or as_ is None or hs == as_:
                continue
            outcome = 1 if hs > as_ else 0

            # 1) mark final
            conn.execute(update(db.games).where(db.games.c.game_id == g.game_id)
                         .values(status="final", home_score=hs, away_score=as_))
            finalized += 1

            # 2) grade every frozen prediction for this game
            preds = conn.execute(
                select(db.predictions.c.prediction_id, db.predictions.c.p_home,
                       db.predictions.c.market_p_home)
                .where(db.predictions.c.game_id == g.game_id)).fetchall()
            for p in preds:
                already = conn.execute(
                    select(db.grades.c.prediction_id)
                    .where(db.grades.c.prediction_id == p.prediction_id)).fetchone()
                if already:
                    continue
                conn.execute(insert(db.grades).values(
                    prediction_id=p.prediction_id, outcome=outcome,
                    brier=round((p.p_home - outcome) ** 2, 5),
                    market_brier=(round((p.market_p_home - outcome) ** 2, 5)
                                  if p.market_p_home is not None else None),
                    graded_at=now_iso))
                graded += 1

            # 3) only now update ratings from this result
            elos = {r.team_id: r.elo for r in conn.execute(
                select(db.teams.c.team_id, db.teams.c.elo)
                .where(db.teams.c.team_id.in_([g.home_team, g.away_team])))}
            if g.home_team in elos and g.away_team in elos:
                rh, ra = elo.update(elos[g.home_team], elos[g.away_team],
                                    home_won=outcome == 1, k=k, ha=ha)
                for tid, r in ((g.home_team, rh), (g.away_team, ra)):
                    conn.execute(update(db.teams)
                                 .where(db.teams.c.team_id == tid)
                                 .values(elo=round(r, 2), elo_updated=now_iso))
    print(f"[grade] finalized {finalized} games, graded {graded} predictions")


if __name__ == "__main__":
    main()
