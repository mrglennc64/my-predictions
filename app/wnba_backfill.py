"""WNBA Elo backfill from ESPN's public scoreboard (2025 + current season).

Usage: python -m app.wnba_backfill
"""
from datetime import date, datetime, timedelta, timezone

import requests
from sqlalchemy import insert

from app import db, elo
from src.lanes.wnba import HA, K

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard"
WINDOWS = [(date(2025, 5, 14), date(2025, 10, 10)),
           (date(2026, 5, 15), None)]


def season_games(start: date, end: date | None):
    end = end or datetime.now(timezone.utc).date()
    s = requests.Session()
    d = start
    while d <= end:
        try:
            r = s.get(ESPN, params={"dates": d.strftime("%Y%m%d")}, timeout=30)
            for ev in r.json().get("events", []):
                if ev.get("status", {}).get("type", {}).get("name") != "STATUS_FINAL":
                    continue
                comp = ev["competitions"][0]
                t = {c["homeAway"]: (c["team"]["displayName"],
                                     int(c.get("score") or 0))
                     for c in comp.get("competitors", [])}
                if len(t) == 2 and t["home"][1] != t["away"][1]:
                    yield d, t["home"][0], t["away"][0], t["home"][1], t["away"][1]
        except requests.RequestException:
            pass
        d += timedelta(days=1)


def main():
    ratings: dict[str, float] = {}
    sq = n_eval = correct = 0
    for i, (start, end) in enumerate(WINDOWS):
        if i:
            ratings = {k: elo.regress_season(v) for k, v in ratings.items()}
        n_season = 0
        for d, home, away, hs, as_ in season_games(start, end):
            rh = ratings.get(home, elo.MEAN)
            ra = ratings.get(away, elo.MEAN)
            home_won = hs > as_
            if i == len(WINDOWS) - 1:      # current season = evaluation
                p = elo.expected_home(rh, ra, ha=HA)
                sq += (p - (1.0 if home_won else 0.0)) ** 2
                correct += (p > 0.5) == home_won
                n_eval += 1
            ratings[home], ratings[away] = elo.update(rh, ra, home_won,
                                                      k=K, ha=HA)
            n_season += 1
        print(f"  window {start}: {n_season} finals")

    if n_eval:
        print(f"current-season eval: n={n_eval} Brier={sq / n_eval:.5f} "
              f"acc={correct / n_eval:.1%}")

    engine = db.init_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with engine.begin() as conn:
        for name, rating in ratings.items():
            tid = "wnba_" + "".join(w[0] for w in name.split()).upper()
            conn.execute(db.teams.delete().where(db.teams.c.team_id == tid))
            conn.execute(insert(db.teams).values(
                team_id=tid, sport="wnba", name=name,
                elo=round(rating, 1), elo_updated=now))
    print(f"wrote {len(ratings)} WNBA team ratings")
    for name, r in sorted(ratings.items(), key=lambda x: -x[1])[:5]:
        print(f"  {name:26} {r:7.1f}")


if __name__ == "__main__":
    main()
