"""Generic lane framework — every candidate market family runs through the
same discipline: fetch live markets by Gamma tag, attach a model probability
if the lane has a model, freeze append-only, grade from the market's own
resolution, keep a per-lane scoreboard. Lanes differ only in their tag and
their model hook, which is what makes them comparable.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from src.polymarket import gamma

_WIN_RE = re.compile(r"^Will (?:the )?(.+?) win", re.IGNORECASE)


@dataclass
class LaneRow:
    lane: str
    mslug: str
    title: str
    side: str
    market_p: float
    model_p: float | None = None
    event_title: str = ""
    event_slug: str = ""


def fetch_lane_rows(lane: str, tag_slug: str, max_events: int = 150,
                    min_volume: float = 500.0) -> list[LaneRow]:
    tag_id = gamma.get_tag_id(tag_slug)
    if not tag_id:
        return []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = []
    for offset in (0, 100):
        batch = gamma.get_events(closed=False, tag_id=tag_id, limit=100,
                                 offset=offset, order="volume24hr",
                                 end_date_min=now)
        events.extend(batch)
        if len(batch) < 100:
            break

    rows = []
    for ev in events[:max_events]:
        vol = float(ev.get("volume24hr") or 0)
        if vol < min_volume:
            continue
        for mk in ev.get("markets", []):
            if mk.get("closed"):
                continue
            outcomes = gamma.parse_json_field(mk.get("outcomes"))
            prices = gamma.parse_json_field(mk.get("outcomePrices"))
            if len(outcomes) != 2 or len(prices) != 2:
                continue
            p0 = float(prices[0])
            if not (0.02 < p0 < 0.98):
                continue
            if str(outcomes[0]) == "Yes":
                won = _WIN_RE.match(mk.get("question", ""))
                side = won.group(1) if won else \
                    (mk.get("groupItemTitle") or mk.get("question", "?"))
            else:
                side = str(outcomes[0])
            rows.append(LaneRow(
                lane=lane, mslug=mk.get("slug") or f"{ev.get('slug')}#{side}",
                title=mk.get("question") or ev.get("title", "?"),
                side=side, market_p=round(p0, 3),
                event_title=ev.get("title", ""),
                event_slug=ev.get("slug", "")))
    return rows


def resolved_outcome(mslug: str) -> int | None:
    """Settled result for a market slug — index-0 outcome won (1) or lost (0).
    Requires explicit closure, same rule as every other lane."""
    # Gamma's bare /markets returns ONLY open markets and closed=true ONLY
    # settled ones — a settled market is invisible without the flag.
    try:
        markets = gamma._get("/markets", slug=mslug, closed="true")
        if not markets:
            markets = gamma._get("/markets", slug=mslug)
    except Exception:
        return None
    for mk in markets if isinstance(markets, list) else [markets]:
        resolved = bool(mk.get("closed")) or \
            str(mk.get("umaResolutionStatus", "")).lower() == "resolved"
        if not resolved:
            continue
        prices = gamma.parse_json_field(mk.get("outcomePrices"))
        if prices:
            p = float(prices[0])
            if p >= 0.99:
                return 1
            if p <= 0.01:
                return 0
    return None
