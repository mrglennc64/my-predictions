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

# Data-reliability gate. Prefer a station's OWN measured settlement bias over its
# nationality: a station that reconciles clean is trustworthy wherever it is, and
# one that reads hot/cold is not, even in the US (a pre-fix Seattle read +19.5°F —
# far worse than any international station). The K-ICAO check is only a FALLBACK
# for stations that haven't reconciled enough post-fix settlements to judge yet.
STATION_MIN_RECON = 3       # post-fix reconciliations needed to trust a station's own bias
BIAS_MAX_CEQ = 1.0          # |mean settlement gap|, °C-equiv, above which a station reads unreliably


def _bias_ceq(mean_delta, unit):
    """Absolute mean settlement gap on a common °C scale (1°F = 0.556°C), so a
    °F and a °C station's reliability compare on the same axis."""
    if mean_delta is None:
        return None
    return abs(mean_delta) / 1.8 if unit == "fahrenheit" else abs(mean_delta)


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


def classify(state, market_p, obs_max=None, boundary=None, unit=None, icao=None,
             station_bias_mean=None, station_bias_n=0):
    """Return a tradeability label for a lock. tier is one of:
      AVOID     — adverse-priced: the market disagrees with our "certain" read
                  (the Shenzhen trap). This is the empirically-supported danger.
      CAUTION   — unreliable-reader station and/or outside the preferred band.
      PREFERRED — reliable station, priced in the band: real edge, survivable.

    Data reliability comes from the station's OWN reconciliation history when it
    has >= STATION_MIN_RECON post-fix settlements: |mean gap| >= BIAS_MAX_CEQ
    (°C-equiv) => unreliable reader => low_data. Below that sample it falls back
    to the US-nationality proxy (K-ICAO). So a proven-clean international station
    can reach PREFERRED, and a proven-biased US station is demoted — the axis is
    MEASURED reliability, not country. `reliability_basis` records which path ran.
    """
    p = sure_side_p(state, market_p)
    bias_ceq = _bias_ceq(station_bias_mean, unit)
    if station_bias_n >= STATION_MIN_RECON and bias_ceq is not None:
        low_data = bias_ceq >= BIAS_MAX_CEQ         # judged on the station's own record
        reliability_basis = "station"
    else:
        low_data = not is_us_station(icao)          # not enough history yet: proxy
        reliability_basis = "proxy"
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
        "reliability_basis": reliability_basis,
        "bias_ceq": round(bias_ceq, 2) if bias_ceq is not None else None,
        "bias_n": station_bias_n,
        "near_conceded": near_conceded,
        "preferred_band": preferred_band,
        "downside_ratio": downside_ratio,
        "tier": tier,
    }
