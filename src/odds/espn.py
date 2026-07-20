"""Free odds source — ESPN's public scoreboard API (no key, no account).

Returns de-vigged win probabilities per team from ESPN BET moneylines.
Not as sharp as Pinnacle, but free and good enough to surface candidate
mispricings on Polymarket for manual review.
"""
import requests

from src import devig

BASE = "https://site.api.espn.com/apis/site/v2/sports"

SPORT_PATHS = {
    "mlb": "baseball/mlb",
    "nba": "basketball/nba",
    "wnba": "basketball/wnba",
    "nfl": "football/nfl",
    "nhl": "hockey/nhl",
    "mls": "soccer/usa.1",
}


def american_to_decimal(a: float) -> float:
    return 1 + (a / 100 if a > 0 else 100 / abs(a))


def fetch_games(sport: str, date: str | None = None) -> list[dict]:
    """[{home_team, away_team, commence, probs: {team_name: prob}}] for a slate.

    date: YYYYMMDD. Defaults to today; finished games carry no odds, so pass
    tomorrow's date when today's slate is over. Only scheduled games returned.
    """
    path = SPORT_PATHS.get(sport)
    if not path:
        raise ValueError(f"Unknown sport {sport!r}; options: {sorted(SPORT_PATHS)}")
    params = {"dates": date} if date else {}
    r = requests.get(f"{BASE}/{path}/scoreboard", params=params, timeout=30)
    r.raise_for_status()

    games = []
    for event in r.json().get("events", []):
        if event.get("status", {}).get("type", {}).get("name") != "STATUS_SCHEDULED":
            continue
        for comp in event.get("competitions", []):
            teams = {c["homeAway"]: c["team"]["displayName"]
                     for c in comp.get("competitors", [])}
            if len(teams) != 2:
                continue
            parsed = None
            for odds in comp.get("odds", []):
                parsed = _parse_moneylines(odds)
                if parsed:
                    break
            if not parsed:
                continue
            home_ml, away_ml, draw_ml = parsed
            decimals = [american_to_decimal(home_ml), american_to_decimal(away_ml)]
            if draw_ml is not None:
                decimals.append(american_to_decimal(draw_ml))
            probs = devig.devig_power(decimals)
            # 3-way team probs are outright-win, matching Polymarket
            # "Will X win?" resolution where a draw resolves No
            prob_map = {teams["home"]: probs[0], teams["away"]: probs[1]}
            if draw_ml is not None:
                prob_map["Draw"] = probs[2]
            games.append({
                "home_team": teams["home"],
                "away_team": teams["away"],
                "commence": event.get("date", ""),
                "probs": prob_map,
            })
    return games


def _parse_moneylines(odds: dict) -> tuple[float, float, float | None] | None:
    """Return (home_ml, away_ml, draw_ml_or_None) American odds from either
    ESPN odds shape: legacy homeTeamOdds.moneyLine, or moneyline.home.close.odds."""
    home = (odds.get("homeTeamOdds") or {}).get("moneyLine")
    away = (odds.get("awayTeamOdds") or {}).get("moneyLine")
    if home and away:
        draw = (odds.get("drawOdds") or {}).get("moneyLine")
        return float(home), float(away), (float(draw) if draw else None)

    ml = odds.get("moneyline") or {}

    def _side(side: str):
        node = ml.get(side) or {}
        raw = (node.get("close") or node.get("current") or node.get("open") or {}).get("odds")
        if raw in (None, "", "EVEN"):
            return 100.0 if raw == "EVEN" else None
        try:
            return float(str(raw).replace("+", ""))
        except ValueError:
            return None

    home, away = _side("home"), _side("away")
    if home is None or away is None:
        return None
    draw = (odds.get("drawOdds") or {}).get("moneyLine")
    if draw is None:
        draw_side = _side("draw")
        draw = draw_side
    return home, away, (float(draw) if draw else None)
