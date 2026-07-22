"""Requirement 4: order-book snapshot + fillable EDGE-DOLLARS.

Read-only against the public CLOB /book endpoint (no auth). Edge-dollars —
how much you could actually fill at prices better than the mechanically
certain value, walking the book — is the headline metric. Edge-percent alone
is vanity: a 40c mispricing on $12 of depth is a toy, and only dollars say so.
"""
import requests

BOOK_URL = "https://clob.polymarket.com/book"


def fetch_book(token_id: str) -> dict:
    r = requests.get(BOOK_URL, params={"token_id": token_id}, timeout=30)
    r.raise_for_status()
    return r.json()


def _levels(raw) -> list[tuple[float, float]]:
    out = []
    for lvl in raw or []:
        try:
            out.append((float(lvl["price"]), float(lvl["size"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def best_bid_ask(book: dict) -> tuple[float | None, float | None]:
    bids = _levels(book.get("bids"))
    asks = _levels(book.get("asks"))
    best_bid = max((p for p, _ in bids), default=None)
    best_ask = min((p for p, _ in asks), default=None)
    return best_bid, best_ask


def edge_dollars(book: dict, fair: float = 0.99) -> tuple[float, list]:
    """Dollars fillable buying the certain-side token below `fair`, walking asks.

    For a PROVEN/DEAD bucket you buy the certain token; every ask priced under
    `fair` is +EV. Returns (edge_dollars, walked_levels). Profit per share at
    price p that resolves to 1 is (1 - p); we conservatively use (fair - p).
    """
    total = 0.0
    walked = []
    for price, size in sorted(_levels(book.get("asks"))):
        if price >= fair:
            break
        total += (fair - price) * size
        walked.append({"price": price, "size": size})
    return round(total, 2), walked
