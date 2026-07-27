"""Job: is tennis's positive model-edge actually FILLABLE?

Weather died on depth, not signal — an ~80% model that lost money because you
couldn't fill the winners (55% phantom). Tennis showed a positive gross edge
(bet the model's disagreement with the venue, +3.5%/bet). Before believing it,
answer the same question: for each live match where the Glicko model disagrees
with the price, capture the CLOB book on the EDGE side and record how many
dollars you could actually buy at +EV prices (asks below the model's fair).

Read-only, append-only. No trading, no gate.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from app import db
from src.contest import tennis
from src.weather_trigger import clob

MIN_EDGE = 0.03    # only where the model disagrees with the venue by >= 3c


def edge_side(model_p1, price_p1, price_p2):
    """(index, price, model_fair) of the side the model would bet, or None.

    Edge is measured on each side against its OWN real quote. The two Polymarket
    outcome prices do NOT sum to 1 (there's vig/spread), so deriving side 1 as
    1 - price_p1 overstated the underdog edge — use the real price_p2 instead.
    Pick the side whose real edge is larger and clears MIN_EDGE."""
    if model_p1 is None:
        return None
    e0 = model_p1 - price_p1                 # model's edge on side 0
    e1 = (1 - model_p1) - price_p2           # model's edge on side 1, real quote
    if e0 >= e1 and e0 >= MIN_EDGE:
        return (0, price_p1, model_p1)
    if e1 > e0 and e1 >= MIN_EDGE:
        return (1, price_p2, round(1 - model_p1, 3))
    return None


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    engine = db.init_db()
    try:
        matches = tennis.fetch_matches()
        tennis.attach_model(matches)
    except Exception as e:
        print(f"[tennis_depth] fetch failed: {type(e).__name__}: {e}")
        return

    rows = []
    for m in matches:
        es = edge_side(m.model_p1, m.sides[0][1], m.sides[1][1])
        if not es or len(m.tokens) < 2:
            continue
        idx, price, fair = es
        try:
            book = clob.fetch_book(m.tokens[idx])
            ev_edge, walked = clob.edge_dollars(book, fair=fair)
            _, best_ask = clob.best_bid_ask(book)
        except Exception:
            continue
        # edge_cents must be measured against the ask you'd actually PAY, not the
        # displayed price — otherwise it's the same displayed-vs-real inflation
        # that killed weather. Fall back to the quote only if the book is empty.
        fill_price = best_ask if best_ask is not None else price
        notional = round(sum(l["price"] * l["size"] for l in walked), 2)
        rows.append(dict(
            slug=m.slug, title=m.title[:80], edge_player=m.sides[idx][0],
            price_paid=round(fill_price, 3),
            model_fair=round(fair, 3),
            edge_cents=round((fair - fill_price) * 100, 1),
            ev_edge_dollars=ev_edge, fillable_notional=notional,
            n_levels=len(walked), best_ask=best_ask, captured_at=_now()))

    if rows:
        with engine.begin() as conn:
            conn.execute(db.tennis_depth.insert(), rows)
    s = latest_summary()
    print(f"[tennis_depth] captured {len(rows)}; " + (s["verdict"] if s else "no edge matches with depth"))


def latest_summary():
    """Median fillable +EV notional on the model-edge side across snapshots."""
    with db.get_engine().connect() as conn:
        vals = [r[0] for r in conn.execute(
            select(db.tennis_depth.c.fillable_notional))
            if r[0] is not None]
    if not vals:
        return None
    s = sorted(vals)
    med = s[len(s) // 2]
    real = sum(1 for v in vals if v >= 100)
    verdict = (f"tennis edge-side depth: {len(vals)} snapshots, median "
               f"${med:.0f} fillable at +EV prices, {real} with >=$100. "
               + ("REAL depth — unlike weather, the +EV is capturable." if med >= 50
                  else "thin — the +EV may be as unfillable as weather was."))
    return {"n": len(vals), "median_fillable": round(med, 2),
            "with_100": real, "verdict": verdict}


if __name__ == "__main__":
    main()
