"""Live weather markets with the full fields the watcher needs — description
(for rules), clobTokenIds (for depth), bucket label, and yes-price. Mirrors
core.fetch_lane_rows' pagination but keeps what the lane row drops.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.polymarket import gamma


@dataclass
class WxMarket:
    mslug: str
    event_slug: str
    event_title: str
    side: str                 # bucket label, e.g. "75°F or above"
    market_p: float           # yes price
    yes_token: str | None
    no_token: str | None
    description: str


def fetch_markets(min_volume: float = 0.0, max_events: int = 200) -> list[WxMarket]:
    tag_id = gamma.get_tag_id("weather")
    if not tag_id:
        return []
    # Polymarket stamps weather endDate at NOON UTC while trading runs to
    # midnight — end_date_min=now hides today's markets every afternoon,
    # exactly the near-resolution window. 36h floor keeps them visible
    # without readmitting stale zombies (same fix as lanes/core.py).
    floor = (datetime.now(timezone.utc) - timedelta(hours=36)
             ).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = []
    for offset in (0, 100):
        batch = gamma.get_events(closed=False, tag_id=tag_id, limit=100,
                                 offset=offset, order="volume24hr",
                                 end_date_min=floor)
        events.extend(batch)
        if len(batch) < 100:
            break

    out = []
    for ev in events[:max_events]:
        if float(ev.get("volume24hr") or 0) < min_volume:
            continue
        # daily-MAX lock logic must never see lowest-temperature markets —
        # a day's LOW has mirrored mechanics (today's false Miami flag)
        if "highest-temperature" not in (ev.get("slug") or ""):
            continue
        for mk in ev.get("markets", []):
            if mk.get("closed"):
                continue
            outcomes = gamma.parse_json_field(mk.get("outcomes"))
            prices = gamma.parse_json_field(mk.get("outcomePrices"))
            toks = gamma.parse_json_field(mk.get("clobTokenIds"))
            if len(outcomes) != 2 or len(prices) != 2:
                continue
            side = mk.get("groupItemTitle") or mk.get("question") or "?"
            yes_tok = toks[0] if len(toks) == 2 else None
            no_tok = toks[1] if len(toks) == 2 else None
            out.append(WxMarket(
                mslug=mk.get("slug") or f"{ev.get('slug')}#{side}",
                event_slug=ev.get("slug", ""), event_title=ev.get("title", ""),
                side=side, market_p=round(float(prices[0]), 3),
                yes_token=yes_tok, no_token=no_tok,
                description=mk.get("description") or ""))
    return out
