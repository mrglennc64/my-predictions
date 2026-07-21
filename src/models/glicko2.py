"""Glicko-2 ratings (Glickman, glicko.net/glicko/glicko2.pdf) — the right
rating system for tennis: per-player uncertainty (RD) that grows with
inactivity, so a returning player's rating is appropriately distrusted.

Implementation notes: each match is processed as its own rating period for
both players (standard simplification), with RD aged by elapsed weeks before
each appearance. TAU controls volatility responsiveness; 0.5 is Glickman's
conservative recommendation.
"""
import math
from dataclasses import dataclass

SCALE = 173.7178
BASE = 1500.0
TAU = 0.5
DEFAULT_RD = 350.0
DEFAULT_VOL = 0.06
WEEKLY_RD_GROWTH = 8.0     # RD points added per idle week, capped at default


@dataclass
class Rating:
    rating: float = BASE
    rd: float = DEFAULT_RD
    vol: float = DEFAULT_VOL
    last_ts: int = 0        # unix time of last processed match


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi ** 2))


def _e(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def age(r: Rating, now_ts: int) -> Rating:
    """Grow RD with inactivity before using a rating."""
    if r.last_ts and now_ts > r.last_ts:
        weeks = (now_ts - r.last_ts) / (7 * 86400)
        r.rd = min(DEFAULT_RD, math.sqrt(r.rd ** 2 +
                                         (WEEKLY_RD_GROWTH ** 2) * weeks))
    return r


def expected(a: Rating, b: Rating) -> float:
    """P(a beats b), uncertainty of both sides folded in."""
    mu_a, mu_b = (a.rating - BASE) / SCALE, (b.rating - BASE) / SCALE
    phi = math.sqrt((a.rd / SCALE) ** 2 + (b.rd / SCALE) ** 2)
    return 1.0 / (1.0 + math.exp(-_g(phi) * (mu_a - mu_b)))


def update(winner: Rating, loser: Rating, ts: int):
    """Process one match; mutates both ratings. Opponent values are
    snapshotted pre-match so the second update never sees the first's result."""
    for r in (winner, loser):
        age(r, ts)
    w_snap = Rating(winner.rating, winner.rd, winner.vol, winner.last_ts)
    l_snap = Rating(loser.rating, loser.rd, loser.vol, loser.last_ts)
    for player, opp, score in ((winner, l_snap, 1.0), (loser, w_snap, 0.0)):
        mu = (player.rating - BASE) / SCALE
        phi = player.rd / SCALE
        mu_j = (opp.rating - BASE) / SCALE
        phi_j = opp.rd / SCALE
        g_j = _g(phi_j)
        e_j = _e(mu, mu_j, phi_j)
        v = 1.0 / (g_j * g_j * e_j * (1 - e_j))
        delta = v * g_j * (score - e_j)

        # volatility update (Illinois-style iteration, Glickman step 5)
        a0 = math.log(player.vol ** 2)
        def f(x):
            ex = math.exp(x)
            num = ex * (delta * delta - phi * phi - v - ex)
            den = 2.0 * (phi * phi + v + ex) ** 2
            return num / den - (x - a0) / (TAU * TAU)
        A = a0
        if delta * delta > phi * phi + v:
            B = math.log(delta * delta - phi * phi - v)
        else:
            k = 1
            while f(a0 - k * TAU) < 0:
                k += 1
            B = a0 - k * TAU
        fa, fb = f(A), f(B)
        for _ in range(100):
            if abs(B - A) < 1e-6:
                break
            C = A + (A - B) * fa / (fb - fa)
            fc = f(C)
            if fc * fb <= 0:
                A, fa = B, fb
            else:
                fa /= 2
            B, fb = C, fc
        new_vol = math.exp(A / 2)

        phi_star = math.sqrt(phi * phi + new_vol * new_vol)
        new_phi = 1.0 / math.sqrt(1.0 / (phi_star ** 2) + 1.0 / v)
        new_mu = mu + new_phi ** 2 * g_j * (score - e_j)

        player.rating = new_mu * SCALE + BASE
        player.rd = new_phi * SCALE
        player.vol = new_vol
        player.last_ts = ts
