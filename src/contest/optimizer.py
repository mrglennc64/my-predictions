"""Contest entry optimizer — maximize P(finish top-N), not expected hits.

The academic result this implements (Hunter/Vielma/Zaman; Haugh/Singal): in a
top-N-payout pick'em contest, the max-accuracy entry (all favorites) is wrong,
because thousands of entrants hold nearly the same chalk. Winning requires
being different where it's cheapest — taking underdogs whose true win chance
is close to the favorite's, where the field's over-concentration on chalk
gives maximum leverage per point of expected value given up.

Model:
  - True outcome probabilities: the market price (sharper than us), fallback
    to our model when no market price exists.
  - Field behavior: each entrant picks the favorite with probability equal to
    a sharpened ownership curve own = p^g / (p^g + (1-p)^g), g ~ 1.7 — the
    documented chalk bias (crowds over-pick favorites relative to fair odds).
  - For each simulated outcome of the slate, a field entrant's score is
    Poisson-binomial; rank is computed analytically from its pmf. Our entry
    is hill-climbed from chalk, flipping picks while P(top-N) improves.

Zero wagering: output is an entry sheet and probabilities.
"""
import math
import os
import random
from dataclasses import dataclass

import requests

API = os.environ.get("LEDGER_API", "https://predictions.usesmpt.com")
OWNERSHIP_GAMMA = 1.7


@dataclass
class Game:
    label: str
    p_home: float            # true prob (market preferred)
    p_home_model: float
    own_home: float          # share of field picking home


def _ownership(p: float, g: float = OWNERSHIP_GAMMA) -> float:
    return p ** g / (p ** g + (1 - p) ** g)


def fetch_slate() -> list[Game]:
    rows = requests.get(f"{API}/api/today", timeout=30).json()
    games = []
    for r in rows:
        p_true = (r.get("p_home_polymarket_now")
                  or r.get("p_home_market_at_freeze")
                  or r["p_home_model"])
        games.append(Game(
            label=f'{r["away"]} @ {r["home"]}',
            p_home=float(p_true),
            p_home_model=float(r["p_home_model"]),
            own_home=_ownership(float(p_true)),
        ))
    # one entry per matchup: de-dup doubleheader repeats by label keeping first
    seen, out = set(), []
    for g in games:
        if g.label not in seen:
            seen.add(g.label)
            out.append(g)
    return out


def _pmf_poisson_binomial(probs: list[float]) -> list[float]:
    pmf = [1.0]
    for p in probs:
        nxt = [0.0] * (len(pmf) + 1)
        for k, v in enumerate(pmf):
            nxt[k] += v * (1 - p)
            nxt[k + 1] += v * p
        pmf = nxt
    return pmf


def _binom_cdf(n: int, p: float, k: int) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    total = 0.0
    logp, log1p = math.log(p), math.log(1 - p)
    for i in range(0, min(k, n) + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1)
                          - math.lgamma(n - i + 1) + i * logp + (n - i) * log1p)
    return min(total, 1.0)


def p_top_n(entry: list[bool], games: list[Game], field_size: int, top_n: int,
            outcome_draws: list[list[bool]]) -> float:
    """entry[i] True = pick home. Averaged over pre-drawn slate outcomes."""
    total = 0.0
    for outcome in outcome_draws:
        our = sum(1 for pick, res in zip(entry, outcome) if pick == res)
        # per-game prob a field entrant is correct, given this outcome
        q = [g.own_home if res else 1 - g.own_home
             for g, res in zip(games, outcome)]
        pmf = _pmf_poisson_binomial(q)
        p_above = sum(pmf[our + 1:])
        p_tie = pmf[our] if our < len(pmf) else 0.0
        p_eff = min(1.0, p_above + 0.5 * p_tie)   # ties split rank
        total += _binom_cdf(field_size - 1, p_eff, top_n - 1)
    return total / len(outcome_draws)


def optimize(games: list[Game], field_size: int = 1000, top_n: int = 10,
             sims: int = 1500, seed: int = 7) -> dict:
    rng = random.Random(seed)
    draws = [[rng.random() < g.p_home for g in games] for _ in range(sims)]

    chalk = [g.p_home >= 0.5 for g in games]
    p_chalk = p_top_n(chalk, games, field_size, top_n, draws)

    entry, best = chalk[:], p_chalk
    improved = True
    while improved:
        improved = False
        for i in range(len(games)):
            cand = entry[:]
            cand[i] = not cand[i]
            p = p_top_n(cand, games, field_size, top_n, draws)
            if p > best:
                entry, best, improved = cand, p, True
    return {"games": games, "chalk": chalk, "entry": entry,
            "p_chalk": p_chalk, "p_entry": best,
            "field_size": field_size, "top_n": top_n}


def report(result: dict) -> str:
    games, chalk, entry = result["games"], result["chalk"], result["entry"]
    lines = [f"Contest: field {result['field_size']}, paying top {result['top_n']}",
             f"P(cash) all-favorites entry: {result['p_chalk']:.3%}",
             f"P(cash) optimized entry:     {result['p_entry']:.3%}  "
             f"({result['p_entry'] / max(result['p_chalk'], 1e-12):.2f}x)", ""]
    for g, c, e in zip(games, chalk, entry):
        side = g.label.split(" @ ")[1] if e else g.label.split(" @ ")[0]
        tag = ""
        if e != c:
            fav_own = g.own_home if c else 1 - g.own_home
            tag = (f"  <- LEVERAGE: underdog p={g.p_home if e else 1 - g.p_home:.2f}"
                   f", field on favorite {fav_own:.0%}")
        lines.append(f"  {g.label:38} pick {side:22} "
                     f"(true P(home) {g.p_home:.2f}, field {g.own_home:.0%} home)"
                     + tag)
    return "\n".join(lines)
