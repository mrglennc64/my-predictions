"""Job: snapshot MLB h2h odds from the-odds-api into odds_snapshots.

One call covers all books for all games. Writes one row per book per game
plus a 'consensus' row (median decimal odds across books). Degrades
gracefully when ODDS_API_KEY is unset or dead — the rest of the pipeline
runs and predictions freeze with market_p_home NULL.
Cron: every 30 min, 07:00-23:00.  Usage: python -m app.jobs.ingest_odds
"""
import os
import statistics
from datetime import datetime, timezone

import requests
from sqlalchemy import insert, select

from app import db

SPORT_KEY = "baseball_mlb"


def _match_game(conn, home_name: str, away_name: str, commence: str):
    """Match an odds event to a scheduled game via team names + closest start."""
    name_rows = conn.execute(select(db.teams.c.team_id, db.teams.c.name)).fetchall()
    by_name = {r.name: r.team_id for r in name_rows}
    home_id, away_id = by_name.get(home_name), by_name.get(away_name)
    if not home_id or not away_id:
        return None
    rows = conn.execute(select(db.games.c.game_id, db.games.c.start_time)
                        .where(db.games.c.home_team == home_id,
                               db.games.c.away_team == away_id,
                               db.games.c.status == "scheduled")).fetchall()
    if not rows:
        return None
    # doubleheaders: pick the game whose start time is closest to commence
    def _dist(r):
        try:
            a = datetime.fromisoformat(r.start_time.replace("Z", "+00:00"))
            b = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            return abs((a - b).total_seconds())
        except ValueError:
            return 1e12
    return min(rows, key=_dist).game_id


def main():
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("[ingest_odds] ODDS_API_KEY not set — skipping (predictions will "
              "freeze without a market benchmark)")
        return
    r = requests.get(
        f"https://api.the-odds-api.com/v4/sports/{SPORT_KEY}/odds",
        params={"apiKey": api_key, "regions": "us", "markets": "h2h",
                "oddsFormat": "decimal"}, timeout=30)
    if r.status_code != 200:
        print(f"[ingest_odds] API error {r.status_code}: {r.text[:120]}")
        return

    engine = db.init_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows_written = 0
    with engine.begin() as conn:
        for event in r.json():
            game_id = _match_game(conn, event["home_team"], event["away_team"],
                                  event.get("commence_time", ""))
            if not game_id:
                continue
            home_prices, away_prices = [], []
            for book in event.get("bookmakers", []):
                for market in book.get("markets", []):
                    if market["key"] != "h2h":
                        continue
                    prices = {o["name"]: float(o["price"])
                              for o in market["outcomes"]}
                    h = prices.get(event["home_team"])
                    a = prices.get(event["away_team"])
                    if not h or not a:
                        continue
                    conn.execute(insert(db.odds_snapshots).values(
                        game_id=game_id, book=book["key"], market="h2h",
                        home_odds=h, away_odds=a, fetched_at=now))
                    home_prices.append(h)
                    away_prices.append(a)
                    rows_written += 1
            if home_prices:
                conn.execute(insert(db.odds_snapshots).values(
                    game_id=game_id, book="consensus", market="h2h",
                    home_odds=statistics.median(home_prices),
                    away_odds=statistics.median(away_prices), fetched_at=now))
                rows_written += 1
    print(f"[ingest_odds] wrote {rows_written} snapshot rows at {now}")


if __name__ == "__main__":
    main()
