"""Self-grading crypto watch: record one signal per up/down window, grade it
at resolution, keep a running Brier scoreboard — model vs Polymarket.

Recording rule: the first scan that sees a window with <= CAPTURE_S seconds
remaining freezes that window's signal (one row per slug, never updated
except to fill the grade). Grading rule: after the window ends, read the
resolved Polymarket price (>0.95 => Up, <0.05 => Down).

Usage: python scan.py crypto-watch [minutes]   (default 30)
"""
import time
from datetime import datetime, timezone

from sqlalchemy import insert, select, update

from app import db
from src.polymarket import gamma
from src.scanners import crypto

CAPTURE_S = 180      # freeze the signal in the last 3 minutes of the window
POLL_S = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(conn, signals) -> int:
    recorded = 0
    for s in signals:
        if s.seconds_left > CAPTURE_S:
            continue
        exists = conn.execute(select(db.crypto_signals.c.signal_id)
                              .where(db.crypto_signals.c.slug == s.slug)).fetchone()
        if exists:
            continue
        conn.execute(insert(db.crypto_signals).values(
            slug=s.slug, symbol=s.symbol, captured_at=_now_iso(),
            seconds_left=s.seconds_left, end_ts=s.end_ts, lead_pct=s.lead_pct,
            model_p_up=s.model_p_up, pm_p_up=s.pm_p_up))
        recorded += 1
        print(f"  captured {s.slug}  lead {s.lead_pct:+.3f}%  "
              f"model {s.model_p_up:.3f}  pm {s.pm_p_up:.3f}  "
              f"({s.seconds_left}s left)")
    return recorded


def _resolved_outcome(slug: str) -> int | None:
    try:
        events = gamma._get("/events", slug=slug)
    except Exception:
        return None
    for ev in events if isinstance(events, list) else [events]:
        for mk in ev.get("markets", []):
            outcomes = gamma.parse_json_field(mk.get("outcomes"))
            prices = gamma.parse_json_field(mk.get("outcomePrices"))
            for o, p in zip(outcomes, prices):
                if str(o).lower() == "up":
                    p = float(p)
                    if p > 0.95:
                        return 1
                    if p < 0.05:
                        return 0
    return None


def _grade(conn) -> int:
    now = int(time.time())
    pending = conn.execute(
        select(db.crypto_signals.c.signal_id, db.crypto_signals.c.slug,
               db.crypto_signals.c.model_p_up, db.crypto_signals.c.pm_p_up)
        .where(db.crypto_signals.c.outcome.is_(None),
               db.crypto_signals.c.end_ts < now - 60)).fetchall()
    graded = 0
    for row in pending:
        outcome = _resolved_outcome(row.slug)
        if outcome is None:
            continue
        conn.execute(update(db.crypto_signals)
                     .where(db.crypto_signals.c.signal_id == row.signal_id)
                     .values(outcome=outcome,
                             model_brier=round((row.model_p_up - outcome) ** 2, 5),
                             pm_brier=round((row.pm_p_up - outcome) ** 2, 5),
                             graded_at=_now_iso()))
        graded += 1
        print(f"  graded  {row.slug}  outcome={'Up' if outcome else 'Down'}  "
              f"model_brier {(row.model_p_up - outcome) ** 2:.4f}  "
              f"pm_brier {(row.pm_p_up - outcome) ** 2:.4f}")
    return graded


def _scoreboard(conn) -> str:
    rows = conn.execute(
        select(db.crypto_signals.c.model_brier, db.crypto_signals.c.pm_brier)
        .where(db.crypto_signals.c.outcome.isnot(None))).fetchall()
    if not rows:
        return "scoreboard: nothing graded yet"
    n = len(rows)
    mb = sum(r.model_brier for r in rows) / n
    pb = sum(r.pm_brier for r in rows) / n
    lead = "MODEL leads" if mb < pb else "POLYMARKET leads"
    return (f"scoreboard: {n} graded | model Brier {mb:.4f} vs "
            f"Polymarket {pb:.4f} -> {lead}")


def run(minutes: int = 30):
    engine = db.init_db()
    deadline = time.time() + minutes * 60
    print(f"[crypto-watch] running {minutes} min; capturing signals in the "
          f"last {CAPTURE_S}s of each window, grading at resolution.")
    while time.time() < deadline:
        try:
            signals = crypto.scan(threshold=0.0, min_seconds_left=30)
        except Exception as e:
            print(f"  scan error: {e}")
            signals = []
        with engine.begin() as conn:
            _record(conn, signals)
            _grade(conn)
            print(f"[{_now_iso()[11:19]}] {_scoreboard(conn)}")
        time.sleep(POLL_S)
    with engine.begin() as conn:
        _grade(conn)
        print(f"[crypto-watch] done. {_scoreboard(conn)}")
