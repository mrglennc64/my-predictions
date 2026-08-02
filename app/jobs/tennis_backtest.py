"""VERDICT 2026-08-01: TRIAL CLOSED — no harvestable edge. Kept running for the
record, NOT as a live trading signal. Three converging tests killed the thesis:
clean live Polymarket bets went net-negative once pre-fix contaminated rows were
excluded; a 15k-match backtest vs de-vigged Pinnacle lost in every config
(-5%/bet, CI below 0); and the model's Brier trails the market on every lane.
Tennis is now calibration-only like the other lanes. This job still writes its
status so the negative result stays visible; it no longer implies a trade.

Does the tennis model-edge actually make money, or is +EV small-sample noise?

The decisive question for the tennis lane. Its model is LESS accurate than the
market (Brier ~0.239 vs ~0.183), so any positive return comes from selective
value-betting, not from being right more often — which makes sample size the
whole ballgame. A +3.5%/bet result on ~100 bets can be pure variance.

So this replays every GRADED tennis prediction where the model disagreed with the
price by >= MIN_EDGE, prices the bet at the REAL quote for that side (never a
1-p fabrication), computes realized ROI per $1 staked, and reports the mean with
a 95% confidence interval. The verdict is honest:

  - CI entirely above 0  -> edge CONFIRMED at 95%
  - CI entirely below 0  -> model LOSES money, not viable
  - CI spans 0           -> INCONCLUSIVE: +EV not yet proven, needs more bets
                            (we also print how many bets that would take)

Rows lacking market_p2 (pre-migration) can only price side-1 bets; model-likes-
side-2 bets there are EXCLUDED and counted, never faked.

Read-only. No trading, no gate.
"""
import json
import math
import os
from datetime import datetime, timezone

from sqlalchemy import select

from app import db
from app.jobs.tennis_depth import MIN_EDGE, edge_side


def bet_roi(price, won):
    """Polymarket share pays $1 if right. Buy at `price`; ROI per $1 staked is
    (1-price)/price on a win, -1 on a loss."""
    if not price or price <= 0 or price >= 1:
        return None
    return (1 - price) / price if won else -1.0


def _edge_from_row(model_p1, market_p1, market_p2):
    """Which side + real price the model would bet, honestly.
    Full quote -> use edge_side. Missing market_p2 -> only side-1 is priceable;
    return the special marker 'unpriced' if the model prefers the unpriceable
    side 2, so the caller can exclude-and-count rather than fake it."""
    if market_p2 is not None:
        return edge_side(model_p1, market_p1, market_p2)
    if (model_p1 - market_p1) >= MIN_EDGE:            # side-1 edge, real price
        return (0, market_p1, model_p1)
    if (1 - model_p1) - (1 - market_p1) >= MIN_EDGE:  # would bet side 2, no price
        return "unpriced"
    return None


STAKE = 100.0     # hypothetical $ per bet, for the P&L line
FEE = 0.07        # Polymarket taker fee on winnings

# Where the pipeline drops the machine-readable edge status the homepage reads.
_STATUS_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "exports", "tennis_edge_status.json")


def main():
    engine = db.init_db()
    tp = db.tennis_predictions
    with engine.connect() as conn:
        rows = conn.execute(select(
            tp.c.model_p1, tp.c.market_p1, tp.c.market_p2, tp.c.outcome
        ).where(tp.c.outcome.isnot(None), tp.c.model_p1.isnot(None))).fetchall()

    rois, wins, unpriced, full_quote = [], 0, 0, 0
    pnl_gross = pnl_fee = 0.0
    for r in rows:
        es = _edge_from_row(r.model_p1, r.market_p1, r.market_p2)
        if es == "unpriced":
            unpriced += 1
            continue
        if not es:
            continue
        idx, price, _fair = es
        won = (r.outcome == 1) if idx == 0 else (r.outcome == 0)
        gross = bet_roi(price, won)
        if gross is None:
            continue
        # Gate the CI and the "proven" flag on NET-of-fee returns: a real edge has
        # to survive Polymarket's 7% taker fee, not just beat 0 gross. A win nets
        # (1-p)/p*(1-fee); a loss still forfeits the full stake. Proving on gross
        # would flash green on an edge the fee erases — the exact trap that made
        # DIP's fee-free favourite light misleading.
        net = (1 - price) / price * (1 - FEE) if won else -1.0
        rois.append(net)
        wins += int(won)
        full_quote += int(r.market_p2 is not None)
        pnl_gross += STAKE * gross
        pnl_fee += STAKE * net

    print(summarize(rois, wins, unpriced, full_quote))
    _write_status(rois, wins, unpriced, pnl_gross, pnl_fee)


def _write_status(rois, wins, unpriced, pnl_gross, pnl_fee):
    """Persist a machine-readable edge status the homepage banner reads. Records
    the FIRST date the CI cleared 0 so the milestone is dated even if a later run
    dips back under (proven reflects the CURRENT interval, first_proven_at sticks)."""
    n = len(rois)
    if n < 2:
        return
    mean = sum(rois) / n
    std = math.sqrt(sum((x - mean) ** 2 for x in rois) / (n - 1))
    lo = mean - 1.96 * std / math.sqrt(n)
    hi = mean + 1.96 * std / math.sqrt(n)
    proven = lo > 0
    need = int((1.96 * std / mean) ** 2) + 1 if mean > 0 else None

    prior = {}
    try:
        with open(_STATUS_PATH, encoding="utf-8") as f:
            prior = json.load(f)
    except (OSError, ValueError):
        prior = {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    first_proven = prior.get("first_proven_at") or (now if proven else None)

    if proven and not prior.get("proven"):
        print(f"[tennis_backtest] *** MILESTONE: tennis edge CLEARED 0 at 95% "
              f"(n={n}, mean {mean:+.2%}, CI low {lo:+.2%}) — edge PROVEN ***")

    status = {
        "n": n, "correct": wins, "win_rate": round(wins / n, 4),
        "mean_roi": round(mean, 4), "ci_lo": round(lo, 4), "ci_hi": round(hi, 4),
        "pnl_gross": round(pnl_gross, 0), "pnl_fee": round(pnl_fee, 0),
        "unpriced_excluded": unpriced, "bets_needed": need,
        "proven": proven, "first_proven_at": first_proven, "updated_at": now,
    }
    try:
        os.makedirs(os.path.dirname(_STATUS_PATH), exist_ok=True)
        with open(_STATUS_PATH, "w", encoding="utf-8") as f:
            json.dump(status, f)
    except OSError:
        pass


def summarize(rois, wins, unpriced, full_quote):
    n = len(rois)
    if n == 0:
        return f"[tennis_backtest] no priceable graded edge bets yet (unpriced skipped: {unpriced})"
    mean = sum(rois) / n
    var = sum((x - mean) ** 2 for x in rois) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    se = std / math.sqrt(n) if n else 0.0
    lo, hi = mean - 1.96 * se, mean + 1.96 * se
    win_rate = wins / n

    if lo > 0:
        verdict = "EDGE CONFIRMED at 95% (CI entirely > 0)."
    elif hi < 0:
        verdict = "NEGATIVE — the model LOSES money at 95% (CI entirely < 0)."
    elif mean > 0:
        # bets needed for the current mean to clear 0 at 95%, if it holds
        need = int((1.96 * std / mean) ** 2) + 1
        verdict = (f"INCONCLUSIVE — 95% CI spans 0, +EV NOT yet proven; "
                   f"~{need} priceable bets needed to prove it (have {n}).")
    else:
        verdict = ("INCONCLUSIVE — 95% CI spans 0 and the point estimate is "
                   "currently <= 0; no edge in evidence.")

    return (f"[tennis_backtest] n={n} priceable edge bets "
            f"({full_quote} full-quote, {n - full_quote} side-1-only; "
            f"{unpriced} model-likes-side2 excluded for want of a real price). "
            f"win rate {win_rate:.1%}, mean NET ROI {mean:+.2%}/bet (after 7% fee), "
            f"95% CI [{lo:+.2%}, {hi:+.2%}]. {verdict}")


if __name__ == "__main__":
    main()
