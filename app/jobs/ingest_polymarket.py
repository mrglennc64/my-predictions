"""Job: snapshot Polymarket MLB prices into odds_snapshots (book='polymarket').

Polymarket is the market benchmark for the ledger: free, no key, and its
sports prices track the sharp line closely. Matches each scheduled game to
its Polymarket event via the Gamma MLB league tag, reads the per-team
moneyline prices, and stores them as decimal-odds rows (1/price) so they fit
the same schema as any bookmaker.
Cron: every 30 min alongside predict_and_freeze.
Usage: python -m app.jobs.ingest_polymarket
"""
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, select

from app import db
from src.polymarket import gamma

_WIN_RE = re.compile(r"^Will (?:the )?(.+?) win on ", re.IGNORECASE)
_STOP = {"the", "of", "at", "vs"}


def _tokens(name: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
            if w not in _STOP}


def _team_prices(event: dict) -> dict[frozenset, float]:
    """{team-name-tokens: P(win)} from an event's moneyline markets.

    Handles both market shapes seen live: Yes/No markets titled
    'Will X win on ...', and two-outcome markets with team-name outcomes.
    """
    prices: dict[frozenset, float] = {}
    for m in event.get("markets", []):
        if m.get("closed"):
            continue
        if m.get("sportsMarketType") not in (None, "moneyline"):
            continue
        outcomes = gamma.parse_json_field(m.get("outcomes"))
        vals = gamma.parse_json_field(m.get("outcomePrices"))
        if len(outcomes) != len(vals) or not outcomes:
            continue
        if outcomes[:1] == ["Yes"]:
            won = _WIN_RE.match(m.get("question", ""))
            if won:
                prices[frozenset(_tokens(won.group(1)))] = float(vals[0])
        else:
            for o, v in zip(outcomes, vals):
                toks = _tokens(str(o))
                if toks and str(o).lower() not in ("yes", "no", "draw"):
                    prices[frozenset(toks)] = float(v)
    return prices


def main():
    engine = db.init_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tag_id = gamma.get_tag_id("mlb")
    if not tag_id:
        print("[ingest_polymarket] could not resolve mlb tag")
        return
    game_slug = re.compile(r"^mlb-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}$")
    events = []
    for offset in (0, 100, 200, 300):
        batch = gamma.get_events(closed=False, tag_id=tag_id, limit=100,
                                 offset=offset, order="startDate")
        events.extend(e for e in batch if game_slug.match(e.get("slug", "")))
        if len(batch) < 100:
            break

    written = unmatched = 0
    with engine.begin() as conn:
        names = {r.team_id: r.name for r in
                 conn.execute(select(db.teams.c.team_id, db.teams.c.name))}
        scheduled = conn.execute(
            select(db.games.c.game_id, db.games.c.home_team, db.games.c.away_team,
                   db.games.c.start_time)
            .where(db.games.c.status == "scheduled")).fetchall()
        for g in scheduled:
            home_toks = _tokens(names.get(g.home_team, ""))
            away_toks = _tokens(names.get(g.away_team, ""))
            # Polymarket slugs end with the game's US-Eastern date
            start = datetime.fromisoformat(g.start_time.replace("Z", "+00:00"))
            et_date = (start - timedelta(hours=4)).strftime("%Y-%m-%d")
            event = next((e for e in events
                          if e.get("slug", "").endswith(et_date)
                          and home_toks & _tokens(e.get("title", ""))
                          and away_toks & _tokens(e.get("title", ""))), None)
            if event is None:
                unmatched += 1
                continue
            prices = _team_prices(event)
            p_home = next((p for toks, p in prices.items() if toks & home_toks), None)
            p_away = next((p for toks, p in prices.items() if toks & away_toks), None)
            if not p_home or not p_away or not (0.01 < p_home < 0.99):
                unmatched += 1
                continue
            conn.execute(insert(db.odds_snapshots).values(
                game_id=g.game_id, book="polymarket", market="h2h",
                home_odds=round(1 / p_home, 4), away_odds=round(1 / p_away, 4),
                fetched_at=now))
            written += 1
    print(f"[ingest_polymarket] wrote {written} snapshots, "
          f"{unmatched} games unmatched, at {now}")


if __name__ == "__main__":
    main()
