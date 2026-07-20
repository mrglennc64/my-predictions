"""Sportsbook-line vs Polymarket divergence scanner.

Strategy: we do NOT try to beat the sportsbook line — we treat the de-vigged
book line as the best available truth (Polymarket's own market makers price
pregame sports off sharp books). Any Polymarket price that disagrees with the
line beyond the threshold is a candidate mispricing on the *Polymarket* side.

Polymarket side uses Gamma's league tags (exact, no fuzzy matching): game
events carry per-team "Will X win?" Yes/No moneyline markets, plus a draw
market for soccer. Book side defaults to ESPN's free public odds (no key).
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.polymarket import gamma
from src.odds import espn

# our sport slug -> Polymarket tag slug
PM_TAGS = {"mlb": "mlb", "nba": "nba", "wnba": "wnba", "nfl": "nfl",
           "nhl": "nhl", "mls": "mls", "epl": "epl"}

_WIN_RE = re.compile(r"^Will (.+?) win on ", re.IGNORECASE)
_DRAW_RE = re.compile(r"end in a draw\?", re.IGNORECASE)
_STOPWORDS = {"the", "fc", "sc", "cf", "st", "de", "of", "at", "vs"}


@dataclass
class Divergence:
    game: str
    outcome: str
    book_prob: float        # de-vigged sportsbook probability
    pm_price: float         # Polymarket YES price for that outcome
    edge_pct: float         # book_prob - pm_price (positive => PM underpriced)
    pm_slug: str
    commence: str


def _tokens(name: str) -> set[str]:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return {w for w in words if w not in _STOPWORDS}


def _pm_league_events(sport: str, max_events: int = 300) -> list[dict]:
    tag_slug = PM_TAGS.get(sport)
    if not tag_slug:
        return []
    tag_id = gamma.get_tag_id(tag_slug)
    if not tag_id:
        return []
    events = []
    for offset in range(0, max_events, 100):
        batch = gamma.get_events(limit=100, offset=offset, tag_id=tag_id)
        if not batch:
            break
        events.extend(batch)
        if len(batch) < 100:
            break
    return events


def scan(sport: str = "mlb", threshold: float = 0.04) -> list[Divergence]:
    games = espn.fetch_games(sport)
    if not games:  # today's slate finished — look at tomorrow
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
        games = espn.fetch_games(sport, date=tomorrow)
    if not games:
        return []
    pm_events = _pm_league_events(sport)

    results = []
    for game in games:
        team_tokens = {t: _tokens(t) for t in game["probs"] if t != "Draw"}
        event = next(
            (e for e in pm_events
             if all(toks & _tokens(e.get("title", "")) for toks in team_tokens.values())),
            None)
        if event is None:
            continue
        for m in event.get("markets", []):
            if m.get("closed") or m.get("sportsMarketType") != "moneyline":
                continue
            question = m.get("question", "")
            outcomes = gamma.parse_json_field(m.get("outcomes"))
            prices = gamma.parse_json_field(m.get("outcomePrices"))
            if outcomes[:1] != ["Yes"] or len(prices) < 1:
                continue
            yes_price = float(prices[0])
            if not (0.01 < yes_price < 0.99):
                continue

            if _DRAW_RE.search(question):
                target, book_prob = "Draw", game["probs"].get("Draw")
            else:
                won = _WIN_RE.match(question)
                if not won:
                    continue
                q_team = _tokens(won.group(1))
                matched = [t for t, toks in team_tokens.items() if toks & q_team]
                if len(matched) != 1:
                    continue
                target, book_prob = matched[0], game["probs"][matched[0]]
            if book_prob is None:
                continue

            edge = book_prob - yes_price
            if abs(edge) >= threshold:
                results.append(Divergence(
                    game=f'{game["away_team"]} @ {game["home_team"]}',
                    outcome=target,
                    book_prob=round(book_prob, 3),
                    pm_price=round(yes_price, 3),
                    edge_pct=round(100 * edge, 2),
                    pm_slug=event.get("slug", ""),
                    commence=game["commence"],
                ))
    results.sort(key=lambda d: abs(d.edge_pct), reverse=True)
    return results
