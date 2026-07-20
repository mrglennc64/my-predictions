"""Vig removal — convert bookmaker odds to true probabilities.

Multiplicative normalization is biased for longshots; power method (a light
goto_conversion-style approach) handles favorite-longshot bias better and is
the default here.
"""


def implied(decimal_odds: list[float]) -> list[float]:
    return [1.0 / o for o in decimal_odds]


def devig_multiplicative(decimal_odds: list[float]) -> list[float]:
    probs = implied(decimal_odds)
    total = sum(probs)
    return [p / total for p in probs]


def devig_power(decimal_odds: list[float], tol: float = 1e-10) -> list[float]:
    """Solve sum((1/o)^k) = 1 by bisection; shrinks longshots harder than favorites."""
    probs = implied(decimal_odds)
    lo, hi = 0.5, 3.0
    for _ in range(200):
        k = (lo + hi) / 2
        total = sum(p ** k for p in probs)
        if abs(total - 1.0) < tol:
            break
        if total > 1.0:
            lo = k
        else:
            hi = k
    return [p ** k for p in probs]
