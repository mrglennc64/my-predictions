"""Elo model for MLB — 538-style: small K, modest home advantage.

E_home = 1 / (1 + 10^(-(R_home + HA - R_away)/400))
R' = R + K * (S - E)
Between seasons each team regresses one-third toward 1500.
"""

DEFAULT_K = 4.0
DEFAULT_HA = 24.0
MEAN = 1500.0
SEASON_REGRESSION = 1 / 3


def expected_home(r_home: float, r_away: float, ha: float = DEFAULT_HA) -> float:
    return 1.0 / (1.0 + 10 ** (-((r_home + ha - r_away) / 400.0)))


def update(r_home: float, r_away: float, home_won: bool,
           k: float = DEFAULT_K, ha: float = DEFAULT_HA) -> tuple[float, float]:
    e = expected_home(r_home, r_away, ha)
    s = 1.0 if home_won else 0.0
    delta = k * (s - e)
    return r_home + delta, r_away - delta


def regress_season(rating: float) -> float:
    return rating + (MEAN - rating) * SEASON_REGRESSION
