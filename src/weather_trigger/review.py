"""Acceptance gate: write the parsed rules for the first 10 live weather
markets to a review file so a human can verify settlement source / unit /
station BEFORE the watcher acts on them. Run: python -m src.weather_trigger.review
"""
from datetime import datetime, timezone

from src.weather_trigger import fetch, rules

OUT = "exports/weather_rules_review.md"


def build() -> str:
    markets = fetch.fetch_markets()
    # one representative market (bucket) per event, first 10 events
    seen, picks = set(), []
    for mk in markets:
        if mk.event_slug in seen:
            continue
        seen.add(mk.event_slug)
        picks.append(mk)
        if len(picks) >= 10:
            break

    lines = [f"# Weather markets — parsed settlement rules (review before watching)",
             f"_generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_",
             "", "Verify each row against the live market rules. A market with an",
             "EXCLUDED reason is never watched.", ""]
    for mk in picks:
        r = rules.parse_rules(mk.description)
        status = "✅ watchable" if r.watchable else f"⛔ EXCLUDED — {r.excluded}"
        lines += [
            f"## {mk.event_title}",
            f"- market: `{mk.mslug}`",
            f"- **station (ICAO):** `{r.icao}`  ·  {r.station or '—'}",
            f"- **unit:** {r.unit or '—'}  ·  **source:** {r.source or '—'}",
            f"- **window:** {r.window}",
            f"- status: {status}",
            f"- rules excerpt: {(mk.description or '')[:240].strip()}…",
            "",
        ]
    return "\n".join(lines)


def main():
    text = build()
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {OUT} ({text.count('##')} markets)")


if __name__ == "__main__":
    main()
