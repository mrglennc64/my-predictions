# Contest Edge

Polymarket + sports prediction engine for contests. Polymarket lane first; sports
modeling lane (Elo, Dixon-Coles, LightGBM, Monte Carlo parlay pricing) next.

Strategy note: this project does NOT try to out-predict sportsbook lines —
nobody credibly does. It treats the de-vigged book line as truth and hunts
mispricings on the softer side (Polymarket retail prices, contest fields,
correlated parlay pricing).

Full research-backed plan: https://claude.ai/code/artifact/bb06151e-b7d9-4540-acfb-82cf86a8ae27

## Scanners (all read-only, no wallet, no API key needed except divergence)

```
python scan.py negrisk              # negative-risk monitor: YES prices in a
                                    # one-winner event must sum to $1
python scan.py wallets              # consensus open positions of top-PnL wallets
python scan.py divergence mls       # de-vigged book line (ESPN, free, no key)
                                    # vs Polymarket prices; sports: mlb nba wnba
                                    # nfl nhl mls epl
```

## Layout

```
src/polymarket/   gamma.py (metadata + league tags) · clob.py (books/prices) · data_api.py (wallets/leaderboard)
src/odds/         espn.py (free scoreboard odds, 2-way + 3-way soccer)
src/scanners/     negrisk.py · wallets.py · divergence.py
src/devig.py      power-method vig removal (favorite-longshot aware)
```

## API notes (verified live 2026-07-20)

- Gamma `/events`, `/markets`: no auth. List fields (`outcomePrices`,
  `clobTokenIds`) are JSON-encoded strings — use `gamma.parse_json_field`.
- Gamma sports structure: `/sports` registry, `/teams?league=mls`,
  `/tags/slug/{league}` -> tag id -> `/events?tag_id=` gives game events with
  per-team "Will X win?" Yes/No moneyline markets (plus draw for soccer).
- Data API `/v1/leaderboard`: `timePeriod` takes `day|week|month|all`
  (`30d` → 400). `/positions?user=` is public for any wallet.
- CLOB V2 (April 2026): legacy py-clob-client is dead; raw REST reads used here
  work fine without any client library.
- ESPN scoreboard: odds appear day-of only; finished games carry none. Soccer
  moneylines live at `odds.moneyline.{home,away}.close.odds` + `drawOdds`.
- Verified live 2026-07-20: Polymarket MLS pregame prices sit within ~1% of the
  de-vigged ESPN line — divergence alerts are rare by design and cluster
  around news events.

## v1: the ledger (MLB)

Calibrated probabilities, frozen before game time, graded in public. The
`predictions` table is append-only — no UPDATE for it exists in the codebase.

```
python -m app.backfill                    # one-time: warm Elo on 2024-present,
                                          # tune K/HA on 2025 Brier
python -m app.jobs.ingest_schedule        # 06:00 daily
python -m app.jobs.ingest_polymarket      # every 30 min — market benchmark from
                                          # Polymarket game markets (free, no key)
python -m app.jobs.ingest_odds            # optional: sportsbook consensus
                                          # (needs ODDS_API_KEY, $29 tier)
python -m app.jobs.predict_and_freeze     # 09:00 daily + T-60min
python -m app.jobs.grade                  # 06:30 daily (grades, THEN updates Elo)
python -m app.jobs.healthcheck            # 07:30 daily (mechanical invariants)
python -m app.show_today                  # inspect the frozen slate
```

Tuned 2026-07-20: K=4, HA=30, 2025 held-out Brier 0.24347 on 2,434 games
(coin flip 0.250). Data: MLB Stats API (free, official). DB: SQLite via
SQLAlchemy (`CONTEST_EDGE_DB` env to point at Postgres later).

The market benchmark is Polymarket itself: every MLB game trades there
(matched by league tag + ET-dated slug), prices track the sharp line within
~1%, and reading them needs no key, no wallet, no subscription. Polymarket
also lists per-game MLB Player Props events — the v2 props lane's data is
already free.

## Roadmap

- [x] P1/P5: Polymarket data spine + negrisk/wallets scanners (live)
- [x] Divergence scanner vs free ESPN odds (MLS verified; all sports wired)
- [ ] Continuous monitor mode + alerting (divergence spikes on news)
- [ ] SQLite ledger scoring every surfaced signal vs resolution (Brier)
- [ ] Wallet quality scores (per-category realized PnL, not just leaderboard rank)
- [ ] Sports lane: Elo + Dixon-Coles + calibrated LightGBM
- [ ] Monte Carlo game sim -> correlated parlay pricing (the moat)
- [ ] Contest optimizer (variance/leverage portfolios, half-Kelly)
- [ ] Web app + deploy to VPS (pm2 + nginx)
