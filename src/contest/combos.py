"""Combo (parlay) builder for Polymarket Combos / Combo Cup play.

Cross-game MLB legs are genuinely independent, so a combo's fair probability
is the product of its legs' true (market) probabilities — no correlation
machinery needed. Polymarket quotes combos via RFQ with a margin on top of
fair; the only combos worth buying are those quoted BELOW our fair price, and
the only combos worth holding for a leaderboard are ones the crowd doesn't
share.

Outputs two lists:
  anchors  — highest P(all hit): safest slips, likely crowded
  leverage — highest P(all hit) among slips carrying >= 2 contrarian legs
             (sides the field underweights): Combo Cup differentiation
"""
from dataclasses import dataclass
from itertools import combinations

from src.contest.optimizer import Game, fetch_slate


@dataclass
class Leg:
    label: str          # 'PIT @ NYY -> PIT'
    p: float            # true prob (market-derived)
    field_share: float  # fraction of the crowd on this side


@dataclass
class Combo:
    legs: list[Leg]
    p_hit: float
    fair_cents: float       # fair price of the combo token, in cents
    fair_decimal: float     # fair decimal odds
    crowd_overlap: float    # product of field shares — how crowded the slip is


def _legs_from(games: list[Game]) -> list[list[Leg]]:
    per_game = []
    for g in games:
        away, home = g.label.split(" @ ")
        per_game.append([
            Leg(f"{g.label} -> {home}", g.p_home, g.own_home),
            Leg(f"{g.label} -> {away}", 1 - g.p_home, 1 - g.own_home),
        ])
    return per_game


def build(games: list[Game], n_legs: int = 3, top: int = 5,
          min_leg_p: float = 0.35) -> dict:
    per_game = _legs_from(games)
    combos = []
    for game_ix in combinations(range(len(per_game)), n_legs):
        # per selected game, consider both sides but drop hopeless legs
        def expand(ix_list, chosen):
            if not ix_list:
                p = 1.0
                overlap = 1.0
                for leg in chosen:
                    p *= leg.p
                    overlap *= leg.field_share
                combos.append(Combo(
                    legs=list(chosen), p_hit=p,
                    fair_cents=round(100 * p, 1),
                    fair_decimal=round(1 / p, 2),
                    crowd_overlap=overlap))
                return
            for leg in per_game[ix_list[0]]:
                if leg.p >= min_leg_p:
                    expand(ix_list[1:], chosen + [leg])
        expand(list(game_ix), [])

    anchors = sorted(combos, key=lambda c: -c.p_hit)[:top]
    contrarian = [c for c in combos
                  if sum(1 for l in c.legs if l.field_share < 0.5) >= 2]
    leverage = sorted(contrarian, key=lambda c: -c.p_hit)[:top]
    return {"anchors": anchors, "leverage": leverage, "n_scanned": len(combos)}


def report(result: dict, n_legs: int) -> str:
    lines = [f"{result['n_scanned']} candidate {n_legs}-leg combos scanned",
             "",
             "Fair prices below are OUR number: only buy a combo quoted",
             "cheaper than fair; leaderboard slips also want low crowd overlap.",
             ""]
    for name, combos in (("ANCHORS (safest, crowded)", result["anchors"]),
                         ("LEVERAGE (differentiated)", result["leverage"])):
        lines.append(name)
        for c in combos:
            lines.append(f"  P(hit) {c.p_hit:.1%}  fair {c.fair_cents}c  "
                         f"pays {c.fair_decimal}x  crowd-overlap "
                         f"{c.crowd_overlap:.1%}")
            for leg in c.legs:
                tag = "  *contrarian*" if leg.field_share < 0.5 else ""
                lines.append(f"      {leg.label}  p={leg.p:.2f} "
                             f"field {leg.field_share:.0%}{tag}")
        lines.append("")
    return "\n".join(lines)
