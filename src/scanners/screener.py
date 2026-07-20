"""Market screener — poly-maker's selection logic, read-only.

poly-maker (1.4k stars) ranks markets by liquidity-reward income vs
volatility/spread risk before quoting them. We don't market-make, but the same
screen answers a contest question: which markets are incentivized, liquid, and
calm enough that prices there are informative — and which are wild enough that
mispricings persist.

Score favors: reward-incentivized, high 24h volume, mid-range price (real
two-sided uncertainty), low recent volatility, tight spread.
"""
from dataclasses import dataclass

from src.polymarket import gamma


@dataclass
class ScreenedMarket:
    question: str
    slug: str
    price: float
    spread: float
    volume24h: float
    liquidity: float
    day_move: float          # |oneDayPriceChange|
    incentivized: bool
    competitive: float       # Polymarket's own 0-1 competitiveness score
    score: float


def scan(top_n: int = 25, min_volume24h: float = 5000.0,
         max_markets: int = 600) -> list[ScreenedMarket]:
    rows = []
    for offset in range(0, max_markets, 100):
        batch = gamma.get_markets(limit=100, offset=offset, order="volume24hr")
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 100:
            break

    screened = []
    for m in rows:
        if m.get("closed"):
            continue
        vol24 = float(m.get("volume24hr") or 0)
        if vol24 < min_volume24h:
            continue
        bid, ask = m.get("bestBid"), m.get("bestAsk")
        if bid is None or ask is None:
            continue
        bid, ask = float(bid), float(ask)
        mid = (bid + ask) / 2
        if not (0.03 < mid < 0.97):
            continue  # near-resolved: prices informative but no contest value
        spread = float(m.get("spread") or (ask - bid))
        day_move = abs(float(m.get("oneDayPriceChange") or 0))
        incentivized = bool(m.get("holdingRewardsEnabled")) or \
            float(m.get("rewardsMinSize") or 0) > 0
        competitive = float(m.get("competitive") or 0)
        liquidity = float(m.get("liquidityNum") or 0)

        # centrality: 1.0 at 50c, 0 at the edges — genuine uncertainty
        centrality = 1 - abs(mid - 0.5) / 0.47
        calm = 1 / (1 + 10 * day_move + 50 * spread)
        score = (vol24 ** 0.5) * centrality * calm * (1.5 if incentivized else 1.0)

        screened.append(ScreenedMarket(
            question=m.get("question", "?"),
            slug=m.get("slug", ""),
            price=round(mid, 3),
            spread=round(spread, 4),
            volume24h=round(vol24),
            liquidity=round(liquidity),
            day_move=round(day_move, 3),
            incentivized=incentivized,
            competitive=round(competitive, 2),
            score=round(score, 1),
        ))
    screened.sort(key=lambda s: s.score, reverse=True)
    return screened[:top_n]
