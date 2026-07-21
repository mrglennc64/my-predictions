"""WNBA lane model — team Elo from the teams table (sport='wnba'),
matched to Polymarket events by team-name tokens."""
import re

from sqlalchemy import select

from app import db, elo

K = 20.0        # ~40-game season
HA = 80.0       # home advantage in Elo points (~2.5 pts of spread)

_STOP = {"the", "of"}


def _tokens(name: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
            if w not in _STOP}


def attach(rows) -> int:
    engine = db.init_db()
    with engine.connect() as conn:
        teams = conn.execute(
            select(db.teams.c.name, db.teams.c.elo)
            .where(db.teams.c.sport == "wnba")).fetchall()
    if not teams:
        return 0
    book = [(t.name, _tokens(t.name), t.elo) for t in teams]
    n = 0
    for r in rows:
        side_t = _tokens(r.side)
        ev_t = _tokens(r.event_title or r.title)
        side_team = next((b for b in book if b[1] & side_t), None)
        opp = next((b for b in book
                    if b[1] & ev_t and b is not side_team
                    and not (b[1] & side_t)), None)
        if side_team and opp:
            # home unknown from the market row — use neutral (no HA); the
            # backfill still trains WITH home advantage, ratings carry it
            r.model_p = round(elo.expected_home(side_team[2], opp[2], ha=0), 3)
            n += 1
    return n
