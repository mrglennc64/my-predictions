"""Historical backfill + tuning: run the Elo chain over 2024-present, tune
K and home advantage on 2025 held-out Brier, then write warm ratings and the
model_versions row into the ledger DB.

Usage: python -m app.backfill
"""
import json
from datetime import datetime, timezone

from sqlalchemy import insert

from app import db, elo, mlb_api

SEASONS = [("2024", "2024-03-20", "2024-10-01"),
           ("2025", "2025-03-18", "2025-09-29"),
           ("2026", "2026-03-25", None)]  # None -> through yesterday
MODEL_ID = "elo_mlb_v1.0"


def _season_finals(start: str, end: str | None) -> list[dict]:
    if end is None:
        end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    games = mlb_api.get_schedule(start, end)
    return [g for g in games if g["status"] == "Final"
            and g["home_score"] is not None and g["away_score"] is not None
            and g["home_score"] != g["away_score"]]


def run_chain(seasons_games: list[list[dict]], k: float, ha: float,
              eval_season_idx: int | None = None):
    """Run the Elo chain; return (ratings, brier_on_eval_season)."""
    ratings: dict[int, float] = {}
    total_sq, n_eval = 0.0, 0
    for idx, season in enumerate(seasons_games):
        if idx > 0:
            ratings = {t: elo.regress_season(r) for t, r in ratings.items()}
        for g in season:
            rh = ratings.get(g["home_id"], elo.MEAN)
            ra = ratings.get(g["away_id"], elo.MEAN)
            home_won = g["home_score"] > g["away_score"]
            if idx == eval_season_idx:
                e = elo.expected_home(rh, ra, ha)
                total_sq += (e - (1.0 if home_won else 0.0)) ** 2
                n_eval += 1
            ratings[g["home_id"]], ratings[g["away_id"]] = elo.update(
                rh, ra, home_won, k=k, ha=ha)
    brier = total_sq / n_eval if n_eval else None
    return ratings, brier


def main():
    print("Fetching season schedules from MLB Stats API...")
    seasons_games = []
    for name, start, end in SEASONS:
        finals = _season_finals(start, end)
        print(f"  {name}: {len(finals)} decided games")
        seasons_games.append(finals)

    print("\nTuning K and home advantage on 2025 Brier (2024 warmup)...")
    best = (None, None, 1.0)
    for k in (2, 3, 4, 5, 6, 8):
        for ha in (10, 18, 24, 30, 40):
            _, brier = run_chain(seasons_games[:2], k=k, ha=ha, eval_season_idx=1)
            if brier < best[2]:
                best = (k, ha, brier)
    k, ha, brier = best
    print(f"  best: K={k} HA={ha}  2025 Brier={brier:.5f}  "
          f"(coin flip 0.25000)")

    print("\nRunning full chain 2024->present with tuned params...")
    ratings, _ = run_chain(seasons_games, k=k, ha=ha)

    engine = db.init_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    team_meta = mlb_api.get_teams()
    with engine.begin() as conn:
        for mlb_id, rating in sorted(ratings.items(), key=lambda x: -x[1]):
            meta = team_meta.get(mlb_id, {"abbr": str(mlb_id), "name": str(mlb_id)})
            team_id = f"mlb_{meta['abbr']}"
            conn.execute(db.teams.delete().where(db.teams.c.team_id == team_id))
            conn.execute(insert(db.teams).values(
                team_id=team_id, sport="mlb", name=meta["name"],
                elo=round(rating, 2), elo_updated=now))
        conn.execute(db.model_versions.delete().where(
            db.model_versions.c.model_id == MODEL_ID))
        conn.execute(insert(db.model_versions).values(
            model_id=MODEL_ID, sport="mlb",
            description="Plain Elo, tuned on 2025 held-out Brier (2024 warmup)",
            params=json.dumps({"k": k, "home_adv": ha,
                               "season_regression": elo.SEASON_REGRESSION,
                               "tuned_2025_brier": round(brier, 5)}),
            created_at=now))

    top = sorted(ratings.items(), key=lambda x: -x[1])[:5]
    print("\nWarm ratings written. Top 5:")
    for mlb_id, r in top:
        print(f"  {team_meta.get(mlb_id, {}).get('name', mlb_id):28} {r:7.1f}")


if __name__ == "__main__":
    main()
