"""Crypto up/down market scanner — the BTC-15-minute-bot idea, read-only.

Polymarket runs recurring "<Coin> Up or Down" windows (5m/15m/hourly series).
"Up" resolves YES if spot at window close exceeds spot at window open.
Mid-window, the market must price P(Up) given the move so far; we estimate it
with drift-diffusion from live Coinbase data:

    P(Up) = Phi( current_lead / (sigma_1m * sqrt(minutes_remaining)) )

Divergence between that estimate and the Polymarket "Up" price is the signal
(the reference bot flags >= 0.05). Read-only, no wallet.

Window fields verified live: event.startTime = window open,
event.endDate = window close; symbol comes from the slug prefix.
"""
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from src.polymarket import gamma
from src.odds import spot

_SLUG_SYMBOLS = {"btc": "btc", "bitcoin": "btc", "eth": "eth", "ethereum": "eth",
                 "sol": "sol", "solana": "sol", "xrp": "xrp"}
_MAX_WINDOW_S = 2 * 3600


@dataclass
class CryptoSignal:
    title: str
    slug: str
    symbol: str
    seconds_left: int
    end_ts: int              # unix time the window resolves
    lead_pct: float          # spot move since window open
    model_p_up: float        # drift-diffusion estimate
    pm_p_up: float           # Polymarket "Up" price
    edge: float              # model - market
    up_token: str | None = None    # CLOB token ids, for order-book asks
    down_token: str | None = None


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _ts(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def scan(threshold: float = 0.05, min_seconds_left: int = 60) -> list[CryptoSignal]:
    tag_id = gamma.get_tag_id("up-or-down")
    if not tag_id:
        return []
    now = int(time.time())
    now_iso = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = gamma.get_events(closed=False, tag_id=tag_id, limit=100,
                              order="endDate", ascending=True,
                              end_date_min=now_iso)

    sigmas: dict[str, float] = {}
    signals = []
    for ev in events:
        slug = ev.get("slug", "")
        symbol = _SLUG_SYMBOLS.get(slug.split("-")[0])
        if not symbol:
            continue
        start_ts, end_ts = _ts(ev.get("startTime")), _ts(ev.get("endDate"))
        if not start_ts or not end_ts or end_ts - start_ts > _MAX_WINDOW_S:
            continue
        left = end_ts - now
        if not (min_seconds_left <= left) or now < start_ts:
            continue  # window not currently active

        pm_p_up = up_token = down_token = None
        for mk in ev.get("markets", []):
            outcomes = gamma.parse_json_field(mk.get("outcomes"))
            prices = gamma.parse_json_field(mk.get("outcomePrices"))
            tokens = gamma.parse_json_field(mk.get("clobTokenIds"))
            for i, (o, p) in enumerate(zip(outcomes, prices)):
                if str(o).lower() == "up":
                    pm_p_up = float(p)
                    up_token = tokens[i] if i < len(tokens) else None
                elif str(o).lower() == "down":
                    down_token = tokens[i] if i < len(tokens) else None
        if pm_p_up is None or not (0.02 < pm_p_up < 0.98):
            continue

        try:
            if symbol not in sigmas:
                sigmas[symbol] = spot.minute_sigma(symbol)
            open_px = spot.open_price_at(symbol, start_ts)
            cur_px = spot.spot_price(symbol)
        except Exception:
            continue

        lead = math.log(cur_px / open_px)
        sigma = max(sigmas[symbol], 1e-6)
        model_p = _phi(lead / (sigma * math.sqrt(max(left, 1) / 60)))
        edge = model_p - pm_p_up
        if abs(edge) >= threshold:
            signals.append(CryptoSignal(
                title=ev.get("title", "?"),
                slug=slug,
                symbol=symbol.upper(),
                seconds_left=left,
                end_ts=end_ts,
                lead_pct=round(100 * (cur_px / open_px - 1), 3),
                model_p_up=round(model_p, 3),
                pm_p_up=round(pm_p_up, 3),
                edge=round(edge, 3),
                up_token=up_token,
                down_token=down_token,
            ))
    signals.sort(key=lambda s: abs(s.edge), reverse=True)
    return signals
