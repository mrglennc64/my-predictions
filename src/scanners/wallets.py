"""Smart-wallet basket — surface consensus positions of top-PnL traders.

Every wallet's positions are public via the Data API. This ranks the PnL
leaderboard, pulls each top wallet's open positions, and reports markets where
several sharp wallets agree on the same side. Signal, not gospel: wash-trading
and honeypot wallets exist, so consensus across independent wallets is required.
"""
import time
from collections import defaultdict
from dataclasses import dataclass

from src.polymarket import data_api


@dataclass
class ConsensusPosition:
    market_title: str
    outcome: str
    n_wallets: int
    wallets: list[str]
    total_value: float      # combined current USD value
    avg_price: float        # size-weighted average entry price
    cur_price: float


def scan(top_n_wallets: int = 15, min_wallets: int = 3, window: str = "month",
         min_position_usd: float = 500.0, pause_s: float = 0.35) -> list[ConsensusPosition]:
    leaders = data_api.get_leaderboard(window=window, rank_by="pnl", limit=top_n_wallets)
    by_market: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for row in leaders:
        wallet = row.get("proxyWallet") or row.get("wallet")
        if not wallet:
            continue
        try:
            positions = data_api.get_positions(wallet, limit=100)
        except Exception:
            continue
        for p in positions:
            value = float(p.get("currentValue") or 0)
            if value < min_position_usd:
                continue
            key = (p.get("title") or p.get("market", "?"), p.get("outcome", "?"))
            by_market[key].append({
                "wallet": wallet,
                "value": value,
                "avgPrice": float(p.get("avgPrice") or 0),
                "curPrice": float(p.get("curPrice") or 0),
            })
        time.sleep(pause_s)  # stay far under the 150/10s positions limit

    consensus = []
    for (title, outcome), rows in by_market.items():
        wallets = sorted({r["wallet"] for r in rows})
        if len(wallets) < min_wallets:
            continue
        total = sum(r["value"] for r in rows)
        wavg = sum(r["avgPrice"] * r["value"] for r in rows) / total if total else 0
        consensus.append(ConsensusPosition(
            market_title=title,
            outcome=outcome,
            n_wallets=len(wallets),
            wallets=[w[:10] + "…" for w in wallets],
            total_value=round(total, 2),
            avg_price=round(wavg, 3),
            cur_price=round(rows[0]["curPrice"], 3),
        ))
    consensus.sort(key=lambda c: (c.n_wallets, c.total_value), reverse=True)
    return consensus
