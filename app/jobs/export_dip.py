"""Job: export the ledger as DIP-shaped CSVs — the producer side of the
one-prediction-system / one-decision-system handoff.

Writes two files into exports/:
  dip_live.csv    — ungraded predictions (upcoming MLB games, pending crypto)
  dip_graded.csv  — graded history (outcome truth included)

Column names are chosen to hit DIP's ingestion aliases exactly
(entity, gameid->event_key, market, date, line, modelp->prob_over,
version->source_version, domain, actual). DIP reports its own mapping,
so a drift here is visible on its side, not silent.
Usage: python -m app.jobs.export_dip
"""
import csv
import os
import re
from datetime import datetime, timezone

import requests
from sqlalchemy import select

from app import db

DIP_URL = os.environ.get("DIP_URL", "http://127.0.0.1:8100")

EXPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "exports")

FIELDS = ["entity", "gameid", "market", "date", "line", "modelp",
          "version", "domain", "actual"]


def _mlb_rows(conn):
    rows = conn.execute(
        select(db.games.c.game_id, db.games.c.start_time, db.games.c.status,
               db.predictions.c.p_home, db.predictions.c.model_id,
               db.grades.c.outcome, db.teams.c.name.label("home_name"))
        .select_from(
            db.predictions
            .join(db.games, db.predictions.c.game_id == db.games.c.game_id)
            .join(db.teams, db.games.c.home_team == db.teams.c.team_id)
            .outerjoin(db.grades,
                       db.grades.c.prediction_id == db.predictions.c.prediction_id))
    ).fetchall()
    for r in rows:
        yield {
            "entity": r.home_name,
            "gameid": r.game_id,
            "market": "home_moneyline",
            "date": r.start_time[:10],
            "line": 0.5,
            "modelp": r.p_home,
            "version": r.model_id,
            "domain": "mlb",
            "actual": r.outcome if r.outcome is not None else "",
        }


def _crypto_rows(conn):
    rows = conn.execute(select(db.crypto_signals)).fetchall()
    for r in rows:
        window = re.search(r"updown-(\w+)-", r.slug)
        yield {
            "entity": r.symbol,
            "gameid": r.slug,
            "market": f"updown_{window.group(1) if window else 'x'}",
            "date": r.captured_at[:10],
            "line": 0.5,
            "modelp": r.model_p_up,
            "version": "diffusion_v1",
            "domain": "crypto",
            "actual": r.outcome if r.outcome is not None else "",
        }


def _tennis_rows():
    """Live tennis matches as TWO sources sharing one event identity — the
    cross-source join DIP's /decision needs to price an edge:
      source contest-edge : our Glicko-2 fair value   (rated matches only)
      source polymarket   : the venue's price for the same question
    Unrated matches get only the venue row — no fabricated model claim."""
    try:
        from src.contest import tennis
        matches = tennis.fetch_matches()
        tennis.attach_model(matches)
    except Exception:
        return
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for m in matches[:20]:
        fav_name, fav_p = max(m.sides, key=lambda s: s[1])
        base = {"entity": fav_name, "gameid": m.slug or m.title,
                "market": "match_moneyline", "date": today, "line": 0.5,
                "domain": "tennis", "actual": ""}
        yield {**base, "modelp": round(fav_p, 3), "version": "venue_price",
               "source": "polymarket"}
        if m.model_p1 is not None:
            model_p = m.model_p1 if fav_name == m.sides[0][0] else 1 - m.model_p1
            yield {**base, "modelp": round(model_p, 3),
                   "version": "glicko2_v1", "source": "contest-edge"}


def _lane_pair_rows():
    """WNBA + weather as model-vs-venue pairs, same join contract as tennis.
    event_key is the Polymarket EVENT slug so DIP can grade from settlement."""
    from datetime import datetime, timezone
    try:
        from src.lanes import core, weather, wnba
    except Exception:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for lane, tag, attach, market in (("wnba", "wnba", wnba.attach, "team_moneyline"),
                                      ("weather", "weather", weather.attach,
                                       "temp_bucket")):
        try:
            rows = core.fetch_lane_rows(lane, tag)
            attach(rows)
        except Exception:
            continue
        for r in rows:
            if r.model_p is None or not r.event_slug:
                continue    # pairs only where we genuinely have a model view
            base = {"entity": r.side, "gameid": r.event_slug, "market": market,
                    "date": today, "line": 0.5, "domain": lane, "actual": ""}
            yield {**base, "modelp": r.market_p, "version": "venue_price",
                   "source": "polymarket"}
            yield {**base, "modelp": r.model_p,
                   "version": "elo_v1" if lane == "wnba" else "openmeteo_v1",
                   "source": "contest-edge"}


def _weather_trigger_live_rows(conn):
    """Open weather-trigger locks as LIVE DIP predictions, so DIP referees the
    trigger on its OWN ledger (/decision reads results⋈predictions, never the
    pushed history). Distinct market 'temp_lock', NOT the OpenMeteo forecast
    'temp_bucket'. Encoding makes DIP's own math land on lock accuracy at the
    price paid:
      PROVEN (bet the "or above" bucket YES): side=over, prob_over=best_ask
      DEAD   (bet the bucket NO):             side=under, prob_over=1-best_ask
    DIP grades the bucket YES/NO from Polymarket settlement (_pushed_result by
    entity label), then /decision computes hit = up if over else not up = whether
    the LOCKed side won, and p = prob_over if over else 1-prob_over = best_ask.
    event_key is the EVENT slug so DIP finds the bucket among the event's markets.
    Only booked, still-open locks — resolved ones enter via DIP's settlement."""
    from src.weather_trigger.revision import event_slug_of
    te, tg = db.trigger_events, db.trigger_grades
    resolved = {r[0] for r in conn.execute(select(tg.c.mslug))}
    first_lock = {}
    for r in conn.execute(select(te.c.mslug, te.c.city, te.c.side, te.c.state,
                                 te.c.best_ask)
                          .where(te.c.kind == "LOCK").order_by(te.c.id)):
        if r.mslug not in resolved and r.best_ask is not None:
            first_lock.setdefault(r.mslug, r)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for mslug, r in first_lock.items():
        es = event_slug_of(mslug)
        if not es:
            continue
        proven = r.state == "PROVEN"
        yield {
            "entity": r.side, "gameid": es, "market": "temp_lock",
            "date": today, "line": 0.5,
            "modelp": round(r.best_ask if proven else 1.0 - r.best_ask, 3),
            "version": "trigger_v1", "domain": "weather_trigger",
            "source": "contest-edge", "side": "over" if proven else "under",
            "actual": "",
        }


def main():
    os.makedirs(EXPORT_DIR, exist_ok=True)
    engine = db.init_db()
    live, graded = [], []
    with engine.connect() as conn:
        for row in list(_mlb_rows(conn)) + list(_crypto_rows(conn)):
            if row["actual"] != "":
                graded.append(row)          # ALL graded history feeds evidence
            # owner + DIP-spec call: neither baseball nor pending crypto on
            # the live board (crypto windows are structural coin flips with
            # fees; DIP ingests Polymarket crypto itself now). Both lanes
            # keep grading in their own ledgers.
        trigger_live = list(_weather_trigger_live_rows(conn))
    live = list(_tennis_rows()) + list(_lane_pair_rows()) + trigger_live

    for name, rows in (("dip_live.csv", live), ("dip_graded.csv", graded)):
        path = os.path.join(EXPORT_DIR, name)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    print(f"[export_dip] live {len(live)} rows, graded {len(graded)} rows "
          f"-> {EXPORT_DIR}")
    _push_to_dip(live, graded)


def _push_to_dip(live: list[dict], graded: list[dict]):
    """POST the batch to a running DIP server so its dashboard is populated.
    DIP being down is fine — the CSVs remain the durable handoff."""
    payload = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "predictions": [{
            "player": r["entity"], "sport": r["domain"], "market": r["market"],
            "line": float(r["line"]), "probabilityOver": float(r["modelp"]),
            "event_key": r["gameid"], "timestamp": r["date"],
            "source": r.get("source", "contest-edge"),
            "side": r.get("side", "over"),
        } for r in live],
        "history": [{
            "p": float(r["modelp"]), "hit": int(r["actual"]),
            "market": r["market"], "domain": r["domain"],
        } for r in graded],
    }
    try:
        resp = requests.post(f"{DIP_URL}/predictions", json=payload, timeout=15)
        if resp.ok:
            body = resp.json()
            print(f"[export_dip] pushed to DIP: ingested {body.get('ingested')}, "
                  f"assessment: {body.get('assessment')}")
        else:
            print(f"[export_dip] DIP push {resp.status_code}: {resp.text[:150]}")
    except requests.ConnectionError:
        print("[export_dip] DIP server not running — CSVs written, push skipped")


if __name__ == "__main__":
    main()
