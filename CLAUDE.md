# Contest Edge — standing rules (read every session)

These are the project owner's durable instructions. They override any habit
re-derived from reading the source. If an instruction here conflicts with the
code's structure, SAY SO EXPLICITLY and ask whether to rename or re-design —
never partially comply and move on.

## Direction
- Polymarket is the priority lane and the market benchmark. The sportsbook
  line is used as a truth oracle only; we do NOT attempt to beat it, and we
  do NOT build bookie-facing tactics (arb against books, CLV-chasing) that
  get accounts limited.
- Trading/betting vocabulary is FINE in this project. The owner wants the
  trading question answered directly ("would this have made money"), with
  honest execution math (real ask, spread, Kelly) — not moralizing, not
  hedging, not quietly de-fanging features.
- Show probabilities AND trading simulations. Report negative results as
  plainly as positive ones.

## Architecture invariants (do not weaken)
- `predictions` and `crypto_signals` capture rows are append-only; grades
  fill once. No UPDATE path for frozen prediction fields may exist.
- Predictions freeze strictly before event start; healthcheck verifies this
  mechanically. Never bypass it, including for backfill or demos.
- grade.py updates Elo only AFTER grading — ordering is load-bearing.
- Paper trades price against the real CLOB ask, never the mid.

## Process rules learned the hard way
- When the owner gives an instruction, verify completion with evidence
  (grep, git diff) before claiming it done. "Acknowledged" is not "done".
- If an instruction is structurally unsatisfiable as stated, surface the
  conflict in the same reply — offer the rename-vs-redesign choice.
- The owner audits via git history. Keep every change committed with honest
  messages; never rewrite history.

## Operations
- Automation: Task Scheduler "ContestEdge Pipeline" runs run_pipeline.cmd
  every 30 min; server autostarts via Startup-folder VBS on port 8600.
- DB is ledger.sqlite (gitignored). Repo: github.com/mrglennc64/my-predictions.
