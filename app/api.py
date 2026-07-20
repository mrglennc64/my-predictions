"""Read-only ledger API + minimal HTML view. No auth, no writes.

Run: python -m uvicorn app.api:app --port 8600
Endpoints: /  /api/today  /api/ledger?days=30  /api/metrics
"""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import db
from src import devig

app = FastAPI(title="Contest Edge Ledger", docs_url=None, redoc_url=None)
engine = db.init_db()


def _today_rows():
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.games.c.game_id, db.games.c.start_time, db.games.c.away_team,
                   db.games.c.home_team, db.predictions.c.p_home,
                   db.predictions.c.market_p_home, db.predictions.c.frozen_at)
            .select_from(db.predictions.join(
                db.games, db.predictions.c.game_id == db.games.c.game_id))
            .where(db.games.c.status == "scheduled")
            .order_by(db.games.c.start_time)).fetchall()
        out = []
        for r in rows:
            live = conn.execute(
                select(db.odds_snapshots.c.home_odds, db.odds_snapshots.c.away_odds)
                .where(db.odds_snapshots.c.game_id == r.game_id,
                       db.odds_snapshots.c.book == "polymarket")
                .order_by(db.odds_snapshots.c.fetched_at.desc()).limit(1)).fetchone()
            live_p = (round(devig.devig_power([live.home_odds, live.away_odds])[0], 3)
                      if live else None)
            out.append({
                "start_time": r.start_time,
                "away": r.away_team.removeprefix("mlb_"),
                "home": r.home_team.removeprefix("mlb_"),
                "p_home_model": r.p_home,
                "p_home_market_at_freeze": r.market_p_home,
                "p_home_polymarket_now": live_p,
                "frozen_at": r.frozen_at,
            })
        return out


def _ledger_rows(days: int = 30):
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.games.c.start_time, db.games.c.away_team, db.games.c.home_team,
                   db.games.c.home_score, db.games.c.away_score,
                   db.predictions.c.p_home, db.predictions.c.market_p_home,
                   db.predictions.c.frozen_at, db.grades.c.outcome,
                   db.grades.c.brier, db.grades.c.market_brier)
            .select_from(db.grades
                         .join(db.predictions,
                               db.grades.c.prediction_id == db.predictions.c.prediction_id)
                         .join(db.games,
                               db.predictions.c.game_id == db.games.c.game_id))
            .order_by(db.games.c.start_time.desc()).limit(days * 20)).fetchall()
        return [{
            "start_time": r.start_time,
            "away": r.away_team.removeprefix("mlb_"),
            "home": r.home_team.removeprefix("mlb_"),
            "score": f"{r.away_score}-{r.home_score}",
            "p_home_model": r.p_home,
            "p_home_market_at_freeze": r.market_p_home,
            "home_won": r.outcome,
            "brier": r.brier,
            "market_brier": r.market_brier,
            "frozen_at": r.frozen_at,
        } for r in rows]


def _metrics():
    rows = _ledger_rows(days=10000)
    n = len(rows)
    briers = [r["brier"] for r in rows]
    m_briers = [r["market_brier"] for r in rows if r["market_brier"] is not None]
    buckets = {}
    for r in rows:
        b = min(int(r["p_home_model"] * 10), 9)
        buckets.setdefault(b, []).append(r["home_won"])
    calibration = [{"bucket": f"{b/10:.1f}-{(b+1)/10:.1f}",
                    "predicted_mid": round(b / 10 + 0.05, 2),
                    "observed": round(sum(v) / len(v), 3),
                    "n": len(v)}
                   for b, v in sorted(buckets.items())]
    return {
        "graded_predictions": n,
        "model_brier": round(sum(briers) / n, 5) if n else None,
        "market_brier": (round(sum(m_briers) / len(m_briers), 5)
                         if m_briers else None),
        "market_brier_n": len(m_briers),
        "calibration": calibration,
        "note": "every number here is reproducible from /api/ledger",
    }


def _crypto():
    with engine.connect() as conn:
        graded = conn.execute(
            select(db.crypto_signals)
            .where(db.crypto_signals.c.outcome.isnot(None))
            .order_by(db.crypto_signals.c.end_ts.desc())).fetchall()
        pending = conn.execute(
            select(db.crypto_signals)
            .where(db.crypto_signals.c.outcome.is_(None))
            .order_by(db.crypto_signals.c.end_ts.asc()).limit(10)).fetchall()
    n = len(graded)
    mb = sum(r.model_brier for r in graded) / n if n else None
    pb = sum(r.pm_brier for r in graded) / n if n else None
    model_wins = sum(1 for r in graded if r.model_brier < r.pm_brier)
    trades = [r for r in graded if r.trade_pnl is not None]
    row = lambda r: {
        "slug": r.slug, "symbol": r.symbol, "captured_at": r.captured_at,
        "seconds_left": r.seconds_left, "lead_pct": r.lead_pct,
        "model_p_up": r.model_p_up, "pm_p_up": r.pm_p_up,
        "outcome": r.outcome, "model_brier": r.model_brier,
        "pm_brier": r.pm_brier, "trade_side": r.trade_side,
        "trade_price": r.trade_price, "trade_pnl": r.trade_pnl,
    }
    return {
        "graded": n,
        "model_brier": round(mb, 4) if mb is not None else None,
        "polymarket_brier": round(pb, 4) if pb is not None else None,
        "model_wins": model_wins,
        "paper_trades": len(trades),
        "paper_wins": sum(1 for r in trades if r.trade_pnl > 0),
        "paper_pnl": round(sum(r.trade_pnl for r in trades), 2),
        "paper_stake_per_trade": 100,
        "recent_graded": [row(r) for r in graded[:20]],
        "pending": [row(r) for r in pending],
    }


@app.get("/api/crypto")
def api_crypto():
    return _crypto()


@app.get("/api/today")
def api_today():
    return _today_rows()


@app.get("/api/ledger")
def api_ledger(days: int = 30):
    return _ledger_rows(days)


@app.get("/api/metrics")
def api_metrics():
    return _metrics()


@app.get("/", response_class=HTMLResponse)
def home():
    metrics = _metrics()
    today = _today_rows()
    graded = _ledger_rows(days=14)
    cr = _crypto()

    def fmt(v, digits=3):
        return f"{v:.{digits}f}" if isinstance(v, float) else "—"

    today_rows = "\n".join(
        f"<tr><td>{t['start_time'][5:16].replace('T', ' ')}</td>"
        f"<td>{t['away']} @ {t['home']}</td>"
        f"<td>{fmt(t['p_home_model'])}</td>"
        f"<td>{fmt(t['p_home_market_at_freeze'])}</td>"
        f"<td>{fmt(t['p_home_polymarket_now'])}</td>"
        f"<td class='m'>{t['frozen_at'][5:16].replace('T', ' ')}</td></tr>"
        for t in today)
    graded_rows = "\n".join(
        f"<tr><td>{g['start_time'][5:16].replace('T', ' ')}</td>"
        f"<td>{g['away']} @ {g['home']}</td><td>{g['score']}</td>"
        f"<td>{fmt(g['p_home_model'])}</td>"
        f"<td>{'home' if g['home_won'] else 'away'}</td>"
        f"<td>{fmt(g['brier'], 4)}</td>"
        f"<td>{fmt(g['market_brier'], 4) if g['market_brier'] is not None else '—'}</td></tr>"
        for g in graded) or \
        "<tr><td colspan='7'>No graded games yet — first grades land the " \
        "morning after tonight's slate.</td></tr>"

    def crow(r):
        if r["outcome"] is None:
            res, winner = "pending", ""
        else:
            res = "Up" if r["outcome"] else "Down"
            winner = ("model" if r["model_brier"] < r["pm_brier"] else
                      "polymarket" if r["pm_brier"] < r["model_brier"] else "tie")
        if r["trade_side"] is None:
            trade = "—"
        else:
            trade = f"{r['trade_side']}@{r['trade_price']:.2f}"
            if r["trade_pnl"] is not None:
                trade += f" → {r['trade_pnl']:+.0f}"
        return (f"<tr><td>{r['symbol']}</td><td class='m'>{r['slug'][-19:]}</td>"
                f"<td>{r['lead_pct']:+.3f}%</td><td>{r['model_p_up']:.3f}</td>"
                f"<td>{r['pm_p_up']:.3f}</td><td>{res}</td><td>{winner}</td>"
                f"<td>{trade}</td></tr>")

    paper_bit = (f" &nbsp;·&nbsp; paper: {cr['paper_trades']} trades, "
                 f"{cr['paper_wins']} wins, ${cr['paper_pnl']:+.2f} "
                 f"(${cr['paper_stake_per_trade']}/trade)"
                 if cr.get("paper_trades") else "")
    crypto_head = ("nothing graded yet — watch is capturing"
                   if not cr["graded"] else
                   f"{cr['graded']} graded &nbsp;·&nbsp; model Brier "
                   f"{cr['model_brier']} vs Polymarket {cr['polymarket_brier']}"
                   f" &nbsp;·&nbsp; model closer on {cr['model_wins']}/{cr['graded']}"
                   + paper_bit)
    crypto_rows = "\n".join(crow(r) for r in
                            (cr["pending"] + cr["recent_graded"])[:20]) or \
        "<tr><td colspan='8'>run: python scan.py crypto-watch 120</td></tr>"

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Contest Edge — Ledger</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<style>
 body{{font-family:'Segoe UI',system-ui,sans-serif;max-width:60rem;margin:2rem auto;
      padding:0 1rem;color:#1a2420;background:#fafbf8;line-height:1.5}}
 h1{{font-size:1.5rem;margin-bottom:.2rem}} h2{{font-size:1.05rem;margin:2rem 0 .5rem}}
 .head{{font-family:Consolas,monospace;color:#0e7a4c;margin:.5rem 0 1.5rem}}
 table{{border-collapse:collapse;width:100%;font-size:.85rem;
       font-variant-numeric:tabular-nums}}
 th{{text-align:left;font-family:Consolas,monospace;font-size:.7rem;
    text-transform:uppercase;letter-spacing:.07em;color:#5c6b63;
    border-bottom:2px solid #1a2420;padding:.4rem .6rem .3rem 0}}
 td{{border-bottom:1px solid #d8e0da;padding:.4rem .6rem .4rem 0}}
 .m{{color:#5c6b63}} .note{{color:#5c6b63;font-size:.8rem;margin-top:2rem}}
</style></head><body>
<h1>Contest Edge — MLB Ledger</h1>
<p class="head">Model Brier: {fmt(metrics['model_brier'], 5) if metrics['model_brier'] else '—'}
 &nbsp;·&nbsp; Market Brier: {fmt(metrics['market_brier'], 5) if metrics['market_brier'] else '—'}
 &nbsp;·&nbsp; {metrics['graded_predictions']} graded predictions</p>
<p>Calibrated probabilities, frozen before first pitch, graded in public.
Probabilities, never picks. Market benchmark: Polymarket.</p>
<h2>Today — frozen predictions</h2>
<table><tr><th>Start (UTC)</th><th>Game</th><th>Model P(home)</th>
<th>Market @ freeze</th><th>Polymarket now</th><th>Frozen at</th></tr>
{today_rows}</table>
<h2>Crypto up/down — live self-grading watch</h2>
<p class="head">{crypto_head}</p>
<table><tr><th>Sym</th><th>Window</th><th>Spot lead</th><th>Model P(Up)</th>
<th>PM P(Up)</th><th>Result</th><th>Closer</th><th>Paper trade</th></tr>
{crypto_rows}</table>
<p class="note">Paper trades fire only when |model − market| ≥ 0.10 AND the
model's edge still clears 0.10 against the actual order-book ask — the price
a real buy would pay. $100 hypothetical stake each. Zero real orders.</p>
<h2>Graded ledger (recent)</h2>
<table><tr><th>Start (UTC)</th><th>Game</th><th>Score</th><th>Model P(home)</th>
<th>Winner</th><th>Brier</th><th>Market Brier</th></tr>
{graded_rows}</table>
<p class="note">Raw data: <a href="/api/ledger">/api/ledger</a> ·
<a href="/api/metrics">/api/metrics</a> · <a href="/api/today">/api/today</a>.
Every number on this page is reproducible from the raw ledger.
Predictions are append-only; frozen_at precedes game start by construction.</p>
</body></html>"""
