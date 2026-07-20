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
