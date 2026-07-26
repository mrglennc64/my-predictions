"""Lock-quality classifier — the guard the one miss (Shenzhen) taught us.

A lock is mechanically certain by the thermometer, but two things can still make
it a bad trade, and BOTH are visible at lock time without waiting for settlement:

  1. Adverse selection. If the market prices our "certain" side far below 1.0,
     the base rate that WE are wrong (wrong station, a pending Wunderground
     revision, a parse error) is elevated. edge_dollars and lag REWARD large
     gaps — so the most attractive-looking lock is sometimes the market warning
     us, not a slow crowd. The single miss so far was bought near a coin-flip.

  2. Thin data. International stations lack the 6-hour-max group and post sparser
     METARs, so obs_max is harder to trust between reports (a missed peak can make
     a DEAD lock wrong). This is a CAUTION flag, not a hard avoid: the ledger
     shows international °C stations actually reconcile clean, so it lowers a lock
     out of PREFERRED but does not condemn it — adverse pricing (axis 1) does.

Read-only classification from fields already on the LOCK row. It does not touch
lock detection, grading, or the money gate — it only labels tradeability so the
/triggers page (and a human) can skip the traps. Pure; unit-tested offline.

Also exposes the EV asymmetry: a wrong lock forfeits `p/(1-p)` of the win, so a
near-conceded (high-p) lock is catastrophic on a revision — the opposite failure
from the coin-flip-low one. The preferred band avoids both ends.
"""

ADVERSE_MAX = 0.70          # sure side priced below this => market disagrees hard
BAND_LO, BAND_HI = 0.80, 0.93   # preferred entry band: real edge, survivable miss


def sure_side_p(state, market_p):
    """Implied probability of the side we would BUY — YES for PROVEN, NO for
    DEAD. `market_p` is the yes-price on the LOCK row."""
    if market_p is None:
        return None
    return market_p if state == "PROVEN" else 1.0 - market_p


def is_us_station(icao):
    """CONUS 'K' stations carry the 6-hour-max group and dense hourly obs — the
    data the lock relies on. PROXY for data quality: the fuller signal (actual
    obs count + 6hr-group presence, captured at scan time) is a follow-up.
    Alaska/Hawaii (PA*/PH*) also report but are treated conservatively here."""
    return bool(icao) and icao.strip().upper().startswith("K")


def classify(state, market_p, obs_max=None, boundary=None, unit=None, icao=None):
    """Return a tradeability label for a lock. tier is one of:
      AVOID     — adverse-priced: the market disagrees with our "certain" read
                  (the Shenzhen trap). This is the empirically-supported danger.
      CAUTION   — clean price but thin-data (non-US) station, and/or outside the
                  preferred entry band. Worth a look, not auto-preferred.
      PREFERRED — US station, priced in the band: real edge, survivable if wrong.

    Thin data is a CAUTION, not an AVOID: the reconciliation ledger shows
    international °C stations reconcile clean (small negative deltas), so
    nationality alone is not a trap — adverse PRICING is. low_data still rides
    on the row as a flag so a human can weight it.
    """
    p = sure_side_p(state, market_p)
    low_data = not is_us_station(icao)
    adverse = p is not None and p < ADVERSE_MAX
    near_conceded = p is not None and p > BAND_HI
    preferred_band = p is not None and BAND_LO <= p <= BAND_HI
    # loss-if-wrong / gain-if-right, in win-multiples: p/(1-p).
    downside_ratio = round(p / (1.0 - p), 1) if p is not None and p < 1.0 else None
    if adverse:
        tier = "AVOID"
    elif preferred_band and not low_data:
        tier = "PREFERRED"
    else:
        tier = "CAUTION"
    return {
        "sure_p": round(p, 3) if p is not None else None,
        "adverse": adverse,
        "low_data": low_data,
        "near_conceded": near_conceded,
        "preferred_band": preferred_band,
        "downside_ratio": downside_ratio,
        "tier": tier,
    }
