"""Tennis combo suggestions from live Polymarket prices — no model required.

We have no tennis rating model (yet), so the market's own leg prices are the
truth source; the value-add is hygiene and arithmetic: singles only, live
volume only, legs in the playable price band, cross-match independence, and
fair combo payouts precomputed (fair multiple = 1e6 / product of cent prices).
Quotes at or above fair are good; below fair is the margin you're paying.
"""
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations

from src.polymarket import gamma

MIN_VOLUME = 2000.0       # thin markets have stale prices — fair math gets mushy
LEG_MIN, LEG_MAX = 0.35, 0.85


@dataclass
class Match:
    title: str
    volume: float
    sides: list[tuple[str, float]]    # [(player, price)] both sides
    slug: str = ""
    model_p1: float | None = None     # Glicko-2 P(sides[0] wins); None unrated


def key_from_full_name(name: str) -> str:
    """'Pablo Carreno Busta' -> 'carreno busta|p' (tennis-data key format)."""
    nk = unicodedata.normalize("NFKD", name or "")
    nk = "".join(c for c in nk if not unicodedata.combining(c))
    parts = nk.lower().replace(".", "").split()
    if len(parts) < 2:
        return ""
    return " ".join(parts[1:]) + "|" + parts[0][0]


def attach_model(matches: list["Match"]) -> int:
    """Fill model_p1 from tennis_ratings; returns how many matches got a
    model probability (both players rated, 5+ career matches each)."""
    from sqlalchemy import select
    from app import db
    from src.models import glicko2
    engine = db.init_db()
    now = int(datetime.now(timezone.utc).timestamp())
    with engine.connect() as conn:
        rows = conn.execute(select(db.tennis_ratings)).fetchall()
    book = {r.player: r for r in rows}
    n = 0
    for m in matches:
        r1 = book.get(key_from_full_name(m.sides[0][0]))
        r2 = book.get(key_from_full_name(m.sides[1][0]))
        if r1 and r2 and r1.matches >= 5 and r2.matches >= 5:
            a = glicko2.age(glicko2.Rating(r1.rating, r1.rd, r1.vol, r1.last_ts), now)
            b = glicko2.age(glicko2.Rating(r2.rating, r2.rd, r2.vol, r2.last_ts), now)
            m.model_p1 = round(glicko2.expected(a, b), 3)
            n += 1
    return n


@dataclass
class Slip:
    legs: list[tuple[str, float]]     # (label, price)
    p_hit: float
    fair_multiple: float
    style: str                        # 'anchor' | 'leverage'


def fetch_matches(max_events: int = 200) -> list[Match]:
    tag_id = gamma.get_tag_id("tennis")
    if not tag_id:
        return []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = []
    for offset in (0, 100):
        batch = gamma.get_events(closed=False, tag_id=tag_id, limit=100,
                                 offset=offset, order="volume24hr",
                                 end_date_min=now)
        events.extend(batch)
        if len(batch) < 100:
            break

    matches = []
    for ev in events[:max_events]:
        title = ev.get("title", "")
        if "/" in title:
            continue  # doubles teams are written A/B — skip doubles
        for mk in ev.get("markets", []):
            if mk.get("closed") or mk.get("sportsMarketType") not in (None, "moneyline"):
                continue
            outcomes = gamma.parse_json_field(mk.get("outcomes"))
            prices = gamma.parse_json_field(mk.get("outcomePrices"))
            if len(outcomes) != 2 or len(prices) != 2:
                continue
            if outcomes[0] in ("Yes", "No"):
                continue  # want player-named two-way markets
            vol = float(ev.get("volume24hr") or mk.get("volume24hr") or 0)
            if vol < MIN_VOLUME:
                continue
            p0, p1 = float(prices[0]), float(prices[1])
            if not (0.02 < p0 < 0.98):
                continue
            matches.append(Match(
                title=title, volume=vol,
                sides=[(str(outcomes[0]), p0), (str(outcomes[1]), p1)],
                slug=ev.get("slug", "")))
            break
    matches.sort(key=lambda m: -m.volume)
    return matches


def build_slips(matches: list[Match], n_legs: int = 3, top: int = 5) -> list[Slip]:
    legs_per_match = []
    for m in matches:
        legs = [(f"{m.title} -> {name}", p) for name, p in m.sides
                if LEG_MIN <= p <= LEG_MAX]
        if legs:
            legs_per_match.append(legs)

    slips = []
    for ix in combinations(range(len(legs_per_match)), n_legs):
        def expand(rest, chosen):
            if not rest:
                p = 1.0
                for _, price in chosen:
                    p *= price
                n_coin = sum(1 for _, pr in chosen if pr < 0.60)
                slips.append(Slip(
                    legs=list(chosen), p_hit=p,
                    fair_multiple=round(1 / p, 2),
                    style="leverage" if n_coin >= 2 else "anchor"))
                return
            for leg in legs_per_match[rest[0]]:
                expand(rest[1:], chosen + [leg])
        expand(list(ix), [])

    anchors = sorted((s for s in slips if s.style == "anchor"),
                     key=lambda s: -s.p_hit)[:top]
    leverage = sorted((s for s in slips if s.style == "leverage"),
                      key=lambda s: -s.p_hit)[:top]
    return anchors + leverage
