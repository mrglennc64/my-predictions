"""MLB Stats API client — statsapi.mlb.com, free, official, no auth."""
import requests

BASE = "https://statsapi.mlb.com/api/v1"
_session = requests.Session()
_session.headers["User-Agent"] = "contest-edge/0.1"


def get_teams() -> dict[int, dict]:
    """{mlb_team_id: {'abbr': 'NYY', 'name': 'New York Yankees'}}"""
    r = _session.get(f"{BASE}/teams", params={"sportId": 1}, timeout=30)
    r.raise_for_status()
    return {t["id"]: {"abbr": t["abbreviation"], "name": t["name"]}
            for t in r.json()["teams"]}


def get_schedule(start_date: str, end_date: str,
                 game_types: str = "R") -> list[dict]:
    """Games between dates (YYYY-MM-DD), chronological.

    Returns [{game_pk, start_time, status, home_id, away_id, home_score,
    away_score, home_probable, away_probable, venue, doubleheader}].
    game_types: comma list, 'R' regular season; add 'F,D,L,W' for postseason.
    """
    r = _session.get(f"{BASE}/schedule", params={
        "sportId": 1, "startDate": start_date, "endDate": end_date,
        "gameType": game_types, "hydrate": "probablePitcher",
    }, timeout=60)
    r.raise_for_status()

    games = []
    for day in r.json().get("dates", []):
        for g in day.get("games", []):
            home, away = g["teams"]["home"], g["teams"]["away"]
            games.append({
                "game_pk": g["gamePk"],
                "start_time": g["gameDate"],                 # UTC ISO
                "status": g["status"]["abstractGameState"],  # Preview|Live|Final
                "detailed_status": g["status"].get("detailedState", ""),
                "home_id": home["team"]["id"],
                "away_id": away["team"]["id"],
                "home_score": home.get("score"),
                "away_score": away.get("score"),
                "home_probable": (home.get("probablePitcher") or {}).get("fullName"),
                "away_probable": (away.get("probablePitcher") or {}).get("fullName"),
                "venue": (g.get("venue") or {}).get("name"),
                "doubleheader": g.get("doubleHeader", "N"),
            })
    games.sort(key=lambda x: x["start_time"])
    return games
