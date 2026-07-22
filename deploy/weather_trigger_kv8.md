# Weather-trigger — kv8 deploy runbook (PREPPED, NOT DEPLOYED)

Read-only paper-mode lag-logger. Writes append-only `trigger_events` to the
shared `ledger.sqlite`. **Do not run any of this until reviewed.**

## Recommended: pm2 watch loop (matches `predictions-watch` / crypto-watch)

Preferred over cron because the long-lived process keeps the DST-offset cache
in memory (fetches each station's Open-Meteo offset once per run, not every
tick) and uses the adaptive 300s/600s peak polling built into `run()`.

    # on kv8, after `git pull`:
    cd /var/www/predictions
    pm2 start "venv/bin/python scan.py weather-trigger 1440" --name weather-trigger
    pm2 save
    # logs: pm2 logs weather-trigger    (1440 min = 24h, pm2 restarts it daily)

## Alternative: cron one-shot (what was asked for)

Simpler and crash-proof, but loses adaptive polling and re-fetches offsets each
run. Keep the cadence at */10 — at */5 the ~40 station Open-Meteo offset lookups
(cache doesn't persist across separate processes) would exceed the free ~10k/day.

    # crontab -e  — flock stops overlapping runs if one is slow
    */10 * * * * /usr/bin/flock -n /tmp/wxtrig.lock -c 'cd /var/www/predictions && venv/bin/python scan.py weather-trigger once >> logs/weather_trigger.log 2>&1'

## Pre-deploy checklist

- [ ] `git add src/weather_trigger app/db.py app/api.py app/jobs/healthcheck.py scan.py exports/weather_rules_review.md deploy/`
- [ ] commit + push from desktop; `ssh kv8 "cd /var/www/predictions && git pull"`
- [ ] `ssh kv8 "cd /var/www/predictions && venv/bin/python -m src.weather_trigger.tests"` (expect 6 passed)
- [ ] `ssh kv8 "cd /var/www/predictions && venv/bin/python -m src.weather_trigger.review"` — eyeball exports/weather_rules_review.md
- [ ] confirm aviationweather.gov + clob.polymarket.com + api.open-meteo.com reachable from kv8
- [ ] start ONE of the two runners above (not both)
- [ ] restart web so /triggers renders: `pm2 restart predictions-web`
- [ ] after ~2 days: `venv/bin/python -c "from src.weather_trigger import digest; digest.print_digest()"` and hand-audit ~10 locks vs Wunderground before believing any $ figure

## Rollback

    pm2 delete weather-trigger        # (or remove the crontab line)
    # trigger_events is append-only + gitignored; nothing else is affected.
