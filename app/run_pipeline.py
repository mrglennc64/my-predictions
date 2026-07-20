"""One-shot pipeline runner — safe to run every 30 minutes, all jobs idempotent.

ingest_schedule upserts, ingest_polymarket appends snapshots,
predict_and_freeze inserts only missing predictions (first freeze wins),
grade processes each final exactly once, healthcheck asserts invariants.
Usage: python -m app.run_pipeline
"""
from datetime import datetime, timezone

from app.jobs import (grade, healthcheck, ingest_polymarket, ingest_schedule,
                      predict_and_freeze)

STEPS = [("ingest_schedule", ingest_schedule.main),
         ("ingest_polymarket", ingest_polymarket.main),
         ("predict_and_freeze", predict_and_freeze.main),
         ("grade", grade.main),
         ("healthcheck", healthcheck.main)]


def main():
    print(f"=== pipeline {datetime.now(timezone.utc).isoformat()} ===")
    for name, fn in STEPS:
        try:
            fn()
        except SystemExit as e:          # healthcheck exits 1 on failure
            print(f"[{name}] exited: {e}")
        except Exception as e:
            print(f"[{name}] ERROR: {e}")


if __name__ == "__main__":
    main()
