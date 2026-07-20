"""Job: pull today's and tomorrow's MLB schedule + probables into games.

Idempotent upsert on game_id; never touches games already marked final.
Cron: 06:00 daily.  Usage: python -m app.jobs.ingest_schedule
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, select, update

from app import db, mlb_api


def main():
    engine = db.init_db()
    today = datetime.now(timezone.utc)
    start = today.strftime("%Y-%m-%d")
    end = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    schedule = mlb_api.get_schedule(start, end)
    id_to_abbr = {i: m["abbr"] for i, m in mlb_api.get_teams().items()}

    upserted = skipped = 0
    with engine.begin() as conn:
        for g in schedule:
            game_id = f"mlb_{g['game_pk']}"
            home = f"mlb_{id_to_abbr.get(g['home_id'], g['home_id'])}"
            away = f"mlb_{id_to_abbr.get(g['away_id'], g['away_id'])}"
            meta = json.dumps({"home_probable": g["home_probable"],
                               "away_probable": g["away_probable"],
                               "venue": g["venue"],
                               "doubleheader": g["doubleheader"]})
            row = conn.execute(select(db.games.c.status)
                               .where(db.games.c.game_id == game_id)).fetchone()
            if row is None:
                conn.execute(insert(db.games).values(
                    game_id=game_id, sport="mlb", start_time=g["start_time"],
                    home_team=home, away_team=away, status="scheduled", meta=meta))
                upserted += 1
            elif row.status == "scheduled":
                conn.execute(update(db.games)
                             .where(db.games.c.game_id == game_id)
                             .values(start_time=g["start_time"], meta=meta))
                upserted += 1
            else:
                skipped += 1
    print(f"[ingest_schedule] upserted {upserted}, skipped {skipped} finals "
          f"({start}..{end})")


if __name__ == "__main__":
    main()
