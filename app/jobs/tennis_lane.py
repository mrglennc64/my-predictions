"""Job: freeze tennis predictions (append-only) and grade them at resolution.

Freeze: every live singles match gets one row — Glicko-2 model probability
(NULL if a player is unrated) plus the market price at freeze time. First
freeze wins; rows are never updated except by the one-time grade.
Grade: winner read from the market's own resolution (closed + settled price),
exactly like the crypto lane — never from an external feed.
Runs inside run_pipeline every 30 min.
"""
from datetime import datetime, timezone

from sqlalchemy import insert, select, update

from app import db
from src.contest import tennis
from src.polymarket import gamma


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolved_p1(slug: str, p1_name: str) -> int | None:
    try:
        events = gamma._get("/events", slug=slug)
    except Exception:
        return None
    for ev in events if isinstance(events, list) else [events]:
        for mk in ev.get("markets", []):
            resolved = bool(mk.get("closed")) or \
                str(mk.get("umaResolutionStatus", "")).lower() == "resolved"
            if not resolved:
                continue
            outcomes = gamma.parse_json_field(mk.get("outcomes"))
            prices = gamma.parse_json_field(mk.get("outcomePrices"))
            for o, p in zip(outcomes, prices):
                if str(o) == p1_name:
                    p = float(p)
                    if p >= 0.99:
                        return 1
                    if p <= 0.01:
                        return 0
    return None


def main():
    engine = db.init_db()
    matches = tennis.fetch_matches()
    modeled = tennis.attach_model(matches)

    frozen = graded = 0
    with engine.begin() as conn:
        for m in matches:
            if not m.slug:
                continue
            exists = conn.execute(
                select(db.tennis_predictions.c.pred_id)
                .where(db.tennis_predictions.c.slug == m.slug)).fetchone()
            if exists:
                continue
            conn.execute(insert(db.tennis_predictions).values(
                slug=m.slug, title=m.title,
                p1=m.sides[0][0], p2=m.sides[1][0],
                model_p1=m.model_p1, market_p1=m.sides[0][1],
                frozen_at=_now()))
            frozen += 1

        pending = conn.execute(
            select(db.tennis_predictions)
            .where(db.tennis_predictions.c.outcome.is_(None))).fetchall()
        for row in pending:
            outcome = _resolved_p1(row.slug, row.p1)
            if outcome is None:
                continue
            conn.execute(update(db.tennis_predictions)
                         .where(db.tennis_predictions.c.pred_id == row.pred_id)
                         .values(
                outcome=outcome,
                model_brier=(round((row.model_p1 - outcome) ** 2, 5)
                             if row.model_p1 is not None else None),
                market_brier=round((row.market_p1 - outcome) ** 2, 5),
                graded_at=_now()))
            graded += 1
    print(f"[tennis_lane] frozen {frozen} (modeled {modeled}/{len(matches)}), "
          f"graded {graded}")


if __name__ == "__main__":
    main()
