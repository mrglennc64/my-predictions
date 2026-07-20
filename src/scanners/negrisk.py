"""Negative-risk arbitrage monitor.

In a negRisk event exactly one outcome resolves YES, so YES prices across all
outcomes should sum to ~$1.00.
  - sum(best asks) < 1.00  -> buy YES on every outcome: cost < $1, payout $1.
  - sum(best bids) > 1.00  -> buy NO on every outcome: cost n - sum, payout n-1.
Fees/slippage eat ~1-2%, so only flag drifts outside the threshold band.
"""
from dataclasses import dataclass, field

from src.polymarket import gamma


@dataclass
class NegRiskOpportunity:
    event_title: str
    slug: str
    n_outcomes: int
    ask_sum: float          # cost to buy YES on everything
    bid_sum: float          # value credited selling YES on everything
    edge_pct: float         # guaranteed gross margin of the better direction
    direction: str          # "buy-yes-all" or "buy-no-all"
    liquidity: float
    outcomes: list[tuple[str, float, float]] = field(default_factory=list)  # (name, bid, ask)


def scan(threshold: float = 0.02, min_liquidity: float = 1000.0,
         max_events: int = 500) -> list[NegRiskOpportunity]:
    opportunities = []
    for event in gamma.iter_open_events(max_events=max_events):
        if not event.get("negRisk"):
            continue
        markets = [m for m in event.get("markets", [])
                   if not m.get("closed") and m.get("bestBid") is not None
                   and m.get("bestAsk") is not None]
        if len(markets) < 2:
            continue

        outcomes = []
        for m in markets:
            bid, ask = float(m["bestBid"]), float(m["bestAsk"])
            if not (0.0 < ask <= 1.0 and 0.0 <= bid < 1.0):
                outcomes = []
                break
            outcomes.append((m.get("groupItemTitle") or m.get("question", "?"), bid, ask))
        if not outcomes:
            continue

        ask_sum = sum(a for _, _, a in outcomes)
        bid_sum = sum(b for _, b, _ in outcomes)
        n = len(outcomes)

        # buy-yes-all: pay ask_sum, exactly one leg pays $1
        yes_edge = 1.0 - ask_sum
        # buy-no-all: NO asks are (1 - bid); pay n - bid_sum, payout n - 1
        no_edge = bid_sum - 1.0

        liquidity = sum(float(m.get("liquidityNum") or 0) for m in markets)
        if liquidity < min_liquidity:
            continue

        if yes_edge >= threshold or no_edge >= threshold:
            direction = "buy-yes-all" if yes_edge >= no_edge else "buy-no-all"
            edge = max(yes_edge, no_edge)
            cost = ask_sum if direction == "buy-yes-all" else n - bid_sum
            opportunities.append(NegRiskOpportunity(
                event_title=event.get("title", "?"),
                slug=event.get("slug", ""),
                n_outcomes=n,
                ask_sum=round(ask_sum, 4),
                bid_sum=round(bid_sum, 4),
                edge_pct=round(100 * edge / max(cost, 1e-9), 2),
                direction=direction,
                liquidity=round(liquidity),
                outcomes=outcomes,
            ))
    opportunities.sort(key=lambda o: o.edge_pct, reverse=True)
    return opportunities
