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
from src.polymarket import clob, gamma
from src.scanners import crypto

CAPTURE_S = 180      # freeze the signal in the last 3 minutes of the window
POLL_S = 30
TRADE_EDGE = 0.10    # paper-trade only when |model - market| clears this
STAKE = 100.0        # hypothetical dollars per trade


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _paper_trade(s) -> tuple[str | None, float | None]:
    """Decide side and fetch the real order-book ask for it. The mid can lie;
    the ask is what a buy would actually cost. No fill, no trade."""
    edge = s.model_p_up - s.pm_p_up
    if edge >= TRADE_EDGE and s.up_token:
        side, token = "up", s.up_token
    elif edge <= -TRADE_EDGE and s.down_token:
        side, token = "down", s.down_token
    else:
        return None, None
    try:
        ask = float(clob.get_price(token, "buy")["price"])
    except Exception:
        return None, None
    if not (0.01 < ask < 0.99):
        return None, None
    # model's probability for the side we buy, vs what the book charges
    model_p = s.model_p_up if side == "up" else 1 - s.model_p_up
    if model_p - ask < TRADE_EDGE:
        return None, None  # spread ate the edge — pass
    return side, ask


def _record(conn, signals) -> int:
    recorded = 0
    for s in signals:
        if s.seconds_left > CAPTURE_S:
            continue
        exists = conn.execute(select(db.crypto_signals.c.signal_id)
                              .where(db.crypto_signals.c.slug == s.slug)).fetchone()
        if exists:
            continue
        side, ask = _paper_trade(s)
        conn.execute(insert(db.crypto_signals).values(
            slug=s.slug, symbol=s.symbol, captured_at=_now_iso(),
            seconds_left=s.seconds_left, end_ts=s.end_ts, lead_pct=s.lead_pct,
            model_p_up=s.model_p_up, pm_p_up=s.pm_p_up,
            trade_side=side, trade_price=ask))
        recorded += 1
        trade_note = f"  TRADE {side}@{ask:.3f}" if side else ""
        print(f"  captured {s.slug}  lead {s.lead_pct:+.3f}%  "
              f"model {s.model_p_up:.3f}  pm {s.pm_p_up:.3f}  "
              f"({s.seconds_left}s left){trade_note}")
    return recorded


def _resolved_outcome(slug: str) -> int | None:
    """Winner from the market's own resolution — never from spot data.

    Requires the market to be explicitly closed (or UMA-resolved) before
    reading the settled prices, so an extreme-but-live price can never grade
    a window early.
    """
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
                if str(o).lower() == "up":
                    p = float(p)
                    if p >= 0.99:
                        return 1
                    if p <= 0.01:
                        return 0
    return None


def _grade(conn) -> int:
    now = int(time.time())
    pending = conn.execute(
        select(db.crypto_signals.c.signal_id, db.crypto_signals.c.slug,
               db.crypto_signals.c.model_p_up, db.crypto_signals.c.pm_p_up,
               db.crypto_signals.c.trade_side, db.crypto_signals.c.trade_price)
        .where(db.crypto_signals.c.outcome.is_(None),
               db.crypto_signals.c.end_ts < now - 60)).fetchall()
    graded = 0
    for row in pending:
        outcome = _resolved_outcome(row.slug)
        if outcome is None:
            continue
        pnl = None
        if row.trade_side is not None and row.trade_price:
            won = (row.trade_side == "up") == (outcome == 1)
            pnl = round(STAKE * (1 / row.trade_price - 1), 2) if won else -STAKE
        conn.execute(update(db.crypto_signals)
                     .where(db.crypto_signals.c.signal_id == row.signal_id)
                     .values(outcome=outcome,
                             model_brier=round((row.model_p_up - outcome) ** 2, 5),
                             pm_brier=round((row.pm_p_up - outcome) ** 2, 5),
                             trade_pnl=pnl,
                             graded_at=_now_iso()))
        graded += 1
        pnl_note = f"  pnl {pnl:+.2f}" if pnl is not None else ""
        print(f"  graded  {row.slug}  outcome={'Up' if outcome else 'Down'}  "
              f"model_brier {(row.model_p_up - outcome) ** 2:.4f}  "
              f"pm_brier {(row.pm_p_up - outcome) ** 2:.4f}{pnl_note}")
    return graded


def _scoreboard(conn) -> str:
    rows = conn.execute(
        select(db.crypto_signals.c.model_brier, db.crypto_signals.c.pm_brier,
               db.crypto_signals.c.trade_pnl)
        .where(db.crypto_signals.c.outcome.isnot(None))).fetchall()
    if not rows:
        return "scoreboard: nothing graded yet"
    n = len(rows)
    mb = sum(r.model_brier for r in rows) / n
    pb = sum(r.pm_brier for r in rows) / n
    lead = "MODEL leads" if mb < pb else "POLYMARKET leads"
    trades = [r.trade_pnl for r in rows if r.trade_pnl is not None]
    paper = ""
    if trades:
        wins = sum(1 for p in trades if p > 0)
        paper = (f" | paper: {len(trades)} trades, {wins} wins, "
                 f"P&L ${sum(trades):+.2f} on ${STAKE:.0f} stakes")
    return (f"scoreboard: {n} graded | model Brier {mb:.4f} vs "
            f"Polymarket {pb:.4f} -> {lead}{paper}")


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
            recorded = _record(conn, signals)
            graded = _grade(conn)
            print(f"[{_now_iso()[11:19]}] {_scoreboard(conn)}")
        if recorded or graded:
            # push fresh state to DIP while crypto windows are still pending,
            # so its live view includes them (they resolve within minutes)
            try:
                from app.jobs import export_dip
                export_dip.main()
            except Exception as e:
                print(f"  dip push skipped: {e}")
        time.sleep(POLL_S)
    with engine.begin() as conn:
        _grade(conn)
        print(f"[crypto-watch] done. {_scoreboard(conn)}")
