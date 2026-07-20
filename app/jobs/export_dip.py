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
from sqlalchemy import select

from app import db

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


def main():
    os.makedirs(EXPORT_DIR, exist_ok=True)
    engine = db.init_db()
    live, graded = [], []
    with engine.connect() as conn:
        for row in list(_mlb_rows(conn)) + list(_crypto_rows(conn)):
            (graded if row["actual"] != "" else live).append(row)

    for name, rows in (("dip_live.csv", live), ("dip_graded.csv", graded)):
        path = os.path.join(EXPORT_DIR, name)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
    print(f"[export_dip] live {len(live)} rows, graded {len(graded)} rows "
          f"-> {EXPORT_DIR}")


if __name__ == "__main__":
    main()
